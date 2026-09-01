"""M1 deterministic, stage-oriented single-recording analysis pipeline."""
from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .adapters import EmbeddingPort, HttpEmbeddingClient, HttpWindowRefineClient, WindowRefinePort
from .assignment import assign_segments
from .audio import PcmReader, normalize_audio, probe_audio
from .clustering import build_speakers, select_gold_windows
from .config import AnalysisConfig, load_analysis_config
from .contracts import AnalysisRequest, AnalysisResult
from .errors import EngineError, component_error, deadline_error, input_error
from .execution import InlineStageExecutor, ResourceClass, StageExecutor, StageWork
from .exporters import export_result
from .resources import MemoryGuard
from .sanitization import sanitize_public
from .windows import discover_candidates, score_windows


ProgressCallback = Callable[[dict[str, Any]], None]


class AnalysisEngine:
    def __init__(
        self,
        *,
        executor: StageExecutor | None = None,
        window_refine: WindowRefinePort | None = None,
        voice_embedding: EmbeddingPort | None = None,
    ) -> None:
        self.executor = executor or InlineStageExecutor()
        self.window_refine = window_refine
        self.voice_embedding = voice_embedding

    async def analyze(
        self,
        request: AnalysisRequest,
        *,
        progress: ProgressCallback | None = None,
    ) -> AnalysisResult:
        cfg = load_analysis_config(request.config_overlay)
        guard = MemoryGuard(cfg)
        run_id = uuid.uuid4().hex
        temporary_root = Path(cfg.section("runtime")["temporary_root"]).resolve()
        run_dir = (temporary_root / run_id).resolve()
        if run_dir.parent != temporary_root:
            raise input_error("TEMPORARY_PATH_INVALID", "run directory escaped the configured temporary root")
        if request.output_dir is not None:
            output = request.output_dir.resolve()
            if output == temporary_root or temporary_root in output.parents:
                raise input_error("OUTPUT_PATH_INVALID", "output directory must be outside the temporary root")
        run_dir.mkdir(parents=True, exist_ok=False)
        memory_audit: dict[str, Any] = {}
        try:
            probe = await self._stage(
                "audio_probe",
                ResourceClass.AUDIO_IO,
                lambda: probe_audio(request.audio_path, cfg, request.deadline_epoch_ms),
                blocking=True,
                guard=guard,
                memory_audit=memory_audit,
                deadline=request.deadline_epoch_ms,
                progress=progress,
            )
            normalized_segments, input_warnings = _normalize_segments(request, probe.duration_ms, cfg)
            normalized_path = run_dir / "normalized.wav"
            actual_duration = await self._stage(
                "audio_normalize",
                ResourceClass.AUDIO_IO,
                lambda: normalize_audio(request.audio_path, normalized_path, cfg, request.deadline_epoch_ms),
                blocking=True,
                estimated_memory_bytes=4 * 1024 * 1024,
                guard=guard,
                memory_audit=memory_audit,
                deadline=request.deadline_epoch_ms,
                progress=progress,
            )
            if any(int(segment["end_ms"]) > actual_duration for segment in normalized_segments):
                raise input_error("SEGMENT_OUT_OF_BOUNDS", "a segment exceeds decoded audio duration")
            reader = PcmReader(normalized_path)
            algorithm = cfg.section("algorithm")
            candidates, candidate_audit = discover_candidates(
                normalized_segments,
                max_join_gap_ms=int(algorithm["candidate"]["max_join_gap_ms"]),
            )
            refine_client = self.window_refine or HttpWindowRefineClient(cfg)
            profile = {
                "name": "consult_speech_window_cold_start_v1",
                "clean_window": {"max_join_gap_ms": 500},
                "speech_window_candidate": {
                    "edge_trim_ms": 200,
                    "min_trimmed_ms": 1200,
                    "max_join_gap_ms": 500,
                    "min_speech_ratio": 0.75,
                    "max_loudness_std_db": 10.0,
                },
            }
            refined = await self._stage(
                "window_refine",
                ResourceClass.WINDOW_REFINE,
                lambda: refine_client.refine(normalized_path, candidates, profile, request.deadline_epoch_ms),
                estimated_memory_bytes=16 * 1024 * 1024,
                guard=guard,
                memory_audit=memory_audit,
                deadline=request.deadline_epoch_ms,
                progress=progress,
            )
            partial_failures: list[dict[str, Any]] = []
            candidate_results = list(refined.get("candidate_results") or [])
            failed_candidates = [row for row in candidate_results if str(row.get("status")) not in {"success", "empty"}]
            successful_candidates = [row for row in candidate_results if str(row.get("status")) in {"success", "empty"}]
            if candidates and candidate_results and not successful_candidates:
                raise component_error("WINDOW_REFINE_ALL_CANDIDATES_FAILED", "all window-refine candidates failed", "window_refine")
            for row in failed_candidates:
                partial_failures.append({"stage": "window_refine", "candidate_id": row.get("candidate_id"), "status": row.get("status")})
            scored, accepted, score_audit = score_windows(list(refined["windows"]))
            gold_specs, gold_audit = select_gold_windows(accepted)

            inference_windows: list[dict[str, Any]] = []
            sentence_ids: dict[int, str] = {}
            for segment in normalized_segments:
                if int(segment["end_ms"]) - int(segment["start_ms"]) < 1500:
                    continue
                index = int(segment["input_index"])
                window_id = f"sentence:{index}"
                sentence_ids[index] = window_id
                inference_windows.append({
                    "window_id": window_id,
                    "start_ms": segment["start_ms"],
                    "end_ms": segment["end_ms"],
                    "kind": "sentence",
                })
            gold_ids: list[str] = []
            for index, spec in enumerate(gold_specs):
                window_id = f"gold:{index}:{spec.window_id}"
                gold_ids.append(window_id)
                inference_windows.append({
                    "window_id": window_id,
                    "start_ms": spec.start_ms,
                    "end_ms": spec.end_ms,
                    "kind": "gold",
                })

            embed_result: dict[str, Any] = {
                "model_version": "not_invoked",
                "backend": "speechbrain_ecapa",
                "items": {},
                "audit": {"requested_window_count": 0, "batch_count": 0},
            }
            if inference_windows:
                embedding_client = self.voice_embedding or HttpEmbeddingClient(cfg)
                embed_result = await self._stage(
                    "voice_embedding",
                    ResourceClass.VOICE_EMBEDDING,
                    lambda: embedding_client.embed(reader, inference_windows, run_id, request.deadline_epoch_ms),
                    estimated_memory_bytes=min(64 * 1024 * 1024, sum(min(30000, row["end_ms"] - row["start_ms"]) * 32 for row in inference_windows)),
                    guard=guard,
                    memory_audit=memory_audit,
                    deadline=request.deadline_epoch_ms,
                    progress=progress,
                )
                items = embed_result["items"]
                success_count = sum(1 for row in items.values() if row.get("status") == "success" and row.get("embedding"))
                if success_count == 0:
                    raise component_error("VOICE_EMBEDDING_ALL_WINDOWS_FAILED", "all embedding windows failed", "voice_embedding")
                for window in inference_windows:
                    row = items.get(window["window_id"]) or {}
                    if row.get("status") != "success" or not row.get("embedding"):
                        partial_failures.append({
                            "stage": "voice_embedding",
                            "window_id": window["window_id"],
                            "status": row.get("status") or "missing_result",
                        })
            items = embed_result["items"]
            sentence_embeddings = {
                index: list(items[window_id]["embedding"])
                for index, window_id in sentence_ids.items()
                if items.get(window_id, {}).get("status") == "success" and items[window_id].get("embedding")
            }
            gold_embeddings = [
                list(items[window_id]["embedding"])
                if items.get(window_id, {}).get("status") == "success" and items[window_id].get("embedding")
                else None
                for window_id in gold_ids
            ]
            speakers, cluster_audit = await self._stage(
                "speaker_clustering",
                ResourceClass.CPU_CLUSTER,
                lambda: build_speakers(
                    gold_specs,
                    gold_embeddings,
                    dense_nme_max_bytes=guard.dense_nme_budget(
                        int(cfg.section("runtime")["dense_nme_max_bytes"])
                    ),
                ),
                blocking=True,
                estimated_memory_bytes=int(cfg.section("runtime")["dense_nme_max_bytes"]),
                guard=guard,
                memory_audit=memory_audit,
                deadline=request.deadline_epoch_ms,
                progress=progress,
            )
            qualities = await self._stage(
                "segment_quality",
                ResourceClass.AUDIO_IO,
                lambda: {
                    int(segment["input_index"]): reader.quality_metrics(
                        int(segment["start_ms"]),
                        int(segment["end_ms"]),
                        speech_db_threshold=-45.0,
                    )
                    for segment in normalized_segments
                },
                blocking=True,
                estimated_memory_bytes=4 * 1024 * 1024,
                guard=guard,
                memory_audit=memory_audit,
                deadline=request.deadline_epoch_ms,
                progress=progress,
            )
            assignments, assignment_audit = await self._stage(
                "segment_assignment",
                ResourceClass.CPU_CLUSTER,
                lambda: assign_segments(normalized_segments, speakers, sentence_embeddings, qualities),
                blocking=True,
                estimated_memory_bytes=max(1024 * 1024, len(normalized_segments) * 4096),
                guard=guard,
                memory_audit=memory_audit,
                deadline=request.deadline_epoch_ms,
                progress=progress,
            )
            result_segments = []
            echo_speaker = bool(cfg.section("input").get("echo_asr_speaker", True))
            for segment in normalized_segments:
                index = int(segment["input_index"])
                normalized = {
                    "id": segment["id"],
                    "start_ms": segment["start_ms"],
                    "end_ms": segment["end_ms"],
                    "text": segment["text"],
                    "confidence": segment["confidence"],
                }
                if echo_speaker and segment.get("speaker") is not None:
                    normalized["speaker"] = segment["speaker"]
                result_segments.append({
                    "input_index": index,
                    "source": segment["source"],
                    "normalized": normalized,
                    "assignment": assignments[index],
                })
            status = "partial" if partial_failures else "success"
            warnings = input_warnings + [
                {"code": "PARTIAL_COMPONENT_FAILURE", **failure}
                for failure in partial_failures
            ]
            payload = AnalysisResult(
                run_id=run_id,
                status=status,
                audio={**probe.public_dict(), "duration_ms": actual_duration},
                configuration=cfg.public_snapshot(),
                models={
                    "window_refine": {"backend": refined.get("backend"), "profile": "consult_speech_window_cold_start_v1"},
                    "voice_embedding": {"backend": embed_result.get("backend"), "model_version": embed_result.get("model_version")},
                    "manifest": "config/model-manifest.json",
                },
                speakers=[speaker.public_dict() for speaker in speakers],
                segments=result_segments,
                warnings=warnings,
                components={
                    "window_refine": {"status": "partial" if failed_candidates else "success", "backend": refined.get("backend")},
                    "voice_embedding": {"status": "partial" if any(item["stage"] == "voice_embedding" for item in partial_failures) else "success", "model_version": embed_result.get("model_version")},
                },
                audit={
                    "input_metadata": sanitize_public(request.document.metadata),
                    "asr_speaker_used_by_algorithm": False,
                    "candidate_discovery": candidate_audit,
                    "candidates": candidates,
                    "window_refine": sanitize_public(refined.get("audit") or {}),
                    "scored_windows": scored,
                    "window_score": score_audit,
                    "gold_window_selection": gold_audit,
                    "embedding": embed_result.get("audit") or {},
                    "clustering": cluster_audit,
                    "assignment": assignment_audit,
                    "memory": memory_audit,
                },
            )
            if request.output_dir is not None:
                await self._stage(
                    "export",
                    ResourceClass.EXPORT_IO,
                    lambda: export_result(payload.model_dump(mode="json"), request.output_dir, cfg),
                    blocking=True,
                    guard=guard,
                    memory_audit=memory_audit,
                    deadline=request.deadline_epoch_ms,
                    progress=progress,
                )
            return payload
        finally:
            if run_dir.parent == temporary_root and run_dir.name == run_id:
                shutil.rmtree(run_dir, ignore_errors=True)

    async def _stage(
        self,
        stage: str,
        resource: ResourceClass,
        operation,
        *,
        guard: MemoryGuard,
        memory_audit: dict[str, Any],
        deadline: int | None,
        progress: ProgressCallback | None,
        blocking: bool = False,
        estimated_memory_bytes: int = 0,
    ):
        if deadline is not None and int(time.time() * 1000) >= deadline:
            raise deadline_error(stage)
        memory_audit[f"{stage}:before"] = guard.check(stage).to_dict()
        if progress is not None:
            progress({"stage": stage, "status": "started", "resource": resource.value})
        result = await self.executor.run(
            StageWork(stage, resource, estimated_memory_bytes, blocking),
            operation,
        )
        if deadline is not None and int(time.time() * 1000) >= deadline:
            raise deadline_error(stage)
        memory_audit[f"{stage}:after"] = guard.check(stage).to_dict()
        if progress is not None:
            progress({"stage": stage, "status": "completed", "resource": resource.value})
        return result


def _normalize_segments(request: AnalysisRequest, duration_ms: int, cfg: AnalysisConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accept_speaker = bool(cfg.section("input").get("accept_asr_speaker", True))
    echo_speaker = bool(cfg.section("input").get("echo_asr_speaker", True))
    rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for input_index, item in enumerate(request.document.segments):
        if item.end_ms > duration_ms:
            raise input_error("SEGMENT_OUT_OF_BOUNDS", f"segment {item.id} exceeds audio duration")
        raw = item.model_dump(mode="json", exclude_unset=True)
        if item.speaker is not None and not accept_speaker:
            raise input_error("ASR_SPEAKER_DISABLED", f"segment {item.id} contains disabled speaker field")
        if not echo_speaker:
            raw.pop("speaker", None)
        if item.text == "":
            warnings.append({"code": "EMPTY_SEGMENT_TEXT", "segment_id": item.id})
        rows.append({
            "input_index": input_index,
            "id": item.id,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "text": item.text,
            "confidence": item.confidence,
            "speaker": str(item.speaker) if item.speaker is not None else None,
            "source": sanitize_public(raw),
        })
    return sorted(rows, key=lambda row: int(row["start_ms"])), warnings
