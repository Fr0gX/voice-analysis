from __future__ import annotations

import asyncio
import json
import math
import time
import wave

import pytest

from voice_analysis_engine.contracts import AnalysisRequest, SegmentDocument
from voice_analysis_engine.engine import AnalysisEngine
from voice_analysis_engine.errors import EngineError
from voice_analysis_engine.execution import InlineStageExecutor, ResourceClass, StageWork
from voice_analysis_engine.config import load_analysis_config
from voice_analysis_engine.resources import MemoryGuard


def _write_tone(path, seconds=12):
    sample_rate = 16000
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        frames = bytearray()
        for index in range(sample_rate * seconds):
            value = int(6000 * math.sin(2 * math.pi * 220 * index / sample_rate))
            frames.extend(int(value).to_bytes(2, "little", signed=True))
        writer.writeframes(frames)


def _vec(index):
    value = [0.0] * 192
    value[index] = 1.0
    return value


class FakeRefine:
    async def refine(self, audio_path, candidates, profile, deadline_epoch_ms):
        windows = []
        for index, (start, end) in enumerate([(0, 2000), (2500, 4500), (5000, 7000), (7500, 9500)]):
            windows.append({
                "window_id": f"w{index}",
                "source_candidate_id": candidates[0]["candidate_id"],
                "start_ms": start,
                "end_ms": end,
                "duration_ms": end - start,
                "source_segment_indices": [index],
                "backend": "fake",
                "speech_ratio": 0.95,
                "overlap_ms": 0,
                "change_point_count": 0,
                "boundary_left_silence_ms": 100,
                "boundary_right_silence_ms": 100,
                "loudness_db": -20.0,
                "loudness_std_db": 2.0,
                "flags": [],
            })
        return {"backend": "fake", "windows": windows, "candidate_results": [], "audit": {"full_audio_loaded": False}}


class FakeEmbedding:
    async def embed(self, reader, windows, run_id, deadline_epoch_ms):
        items = {}
        for row in windows:
            index = 0 if row["start_ms"] < 5000 else 10
            items[row["window_id"]] = {"status": "success", "embedding": _vec(index)}
        return {"model_version": "fake-v1", "backend": "fake", "items": items, "audit": {"batch_count": 1}}


class FailingEmbedding(FakeEmbedding):
    def __init__(self, *, all_windows=False):
        self.all_windows = all_windows

    async def embed(self, reader, windows, run_id, deadline_epoch_ms):
        result = await super().embed(reader, windows, run_id, deadline_epoch_ms)
        for row in windows:
            if self.all_windows or row["window_id"] == "sentence:1":
                result["items"][row["window_id"]] = {"status": "inference_failed", "embedding": None}
        return result


class RecordingExecutor(InlineStageExecutor):
    def __init__(self):
        self.works: list[StageWork] = []

    async def run(self, work, operation):
        self.works.append(work)
        return await super().run(work, operation)


def test_end_to_end_fake_components_exports_and_cleans_temp(tmp_path):
    audio = tmp_path / "input.wav"
    output = tmp_path / "output"
    _write_tone(audio)
    document = SegmentDocument.model_validate({
        "metadata": {"api_token": "must-not-leak", "audio_path": "C:\\private\\recording.wav"},
        "segments": [
            {"id": f"s{i}", "start_ms": i * 2500, "end_ms": i * 2500 + 2000, "text": f"line {i}", "speaker": "wrong", "access_token": "must-not-leak"}
            for i in range(4)
        ]
    })
    executor = RecordingExecutor()
    result = asyncio.run(AnalysisEngine(
        executor=executor,
        window_refine=FakeRefine(),
        voice_embedding=FakeEmbedding(),
    ).analyze(AnalysisRequest(audio_path=audio, document=document, output_dir=output)))
    assert result.status == "success"
    assert result.audit["asr_speaker_used_by_algorithm"] is False
    assert (output / "result.json").is_file()
    assert (output / "transcript.txt").read_text(encoding="utf-8").startswith("[00:00:00.000")
    authority = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert all("embedding" not in segment for segment in authority["segments"])
    assert all("embedding" not in segment["assignment"] for segment in authority["segments"])
    assert authority["audit"]["input_metadata"]["api_token"] == "<redacted>"
    assert authority["audit"]["input_metadata"]["audio_path"] == "recording.wav"
    assert all(segment["source"]["access_token"] == "<redacted>" for segment in authority["segments"])
    assert {work.resource.value for work in executor.works} >= {"audio_io", "window_refine", "voice_embedding", "cpu_cluster", "export_io"}
    runtime_root = __import__("pathlib").Path(__file__).resolve().parents[2] / "runtime" / "tmp"
    assert [path for path in runtime_root.glob("*") if path.is_dir()] == []


def test_stage_rejects_result_that_finishes_after_deadline():
    config = load_analysis_config()
    engine = AnalysisEngine()

    async def operation():
        await asyncio.sleep(0.03)
        return "late"

    async def run():
        return await engine._stage(
            "deadline_test",
            ResourceClass.CPU_CLUSTER,
            operation,
            guard=MemoryGuard(config),
            memory_audit={},
            deadline=int(time.time() * 1000) + 10,
            progress=None,
        )

    with pytest.raises(EngineError) as captured:
        asyncio.run(run())
    assert captured.value.code == "DEADLINE_EXCEEDED"
    assert captured.value.stage == "deadline_test"


def test_individual_embedding_failure_returns_partial_and_unknown(tmp_path):
    audio = tmp_path / "input.wav"
    _write_tone(audio)
    document = SegmentDocument.model_validate({"segments": [
        {"id": f"s{i}", "start_ms": i * 2500, "end_ms": i * 2500 + 2000, "text": f"line {i}"}
        for i in range(4)
    ]})
    result = asyncio.run(AnalysisEngine(
        window_refine=FakeRefine(),
        voice_embedding=FailingEmbedding(),
    ).analyze(AnalysisRequest(audio_path=audio, document=document)))
    by_index = {row["input_index"]: row for row in result.segments}
    assert result.status == "partial"
    assert by_index[1]["assignment"]["label"] == "unknown"
    assert any(row["code"] == "PARTIAL_COMPONENT_FAILURE" for row in result.warnings)


def test_all_embedding_failures_are_systemic(tmp_path):
    audio = tmp_path / "input.wav"
    _write_tone(audio)
    document = SegmentDocument.model_validate({"segments": [
        {"id": "s0", "start_ms": 0, "end_ms": 2000, "text": "line"}
    ]})
    with pytest.raises(EngineError) as captured:
        asyncio.run(AnalysisEngine(
            window_refine=FakeRefine(),
            voice_embedding=FailingEmbedding(all_windows=True),
        ).analyze(AnalysisRequest(audio_path=audio, document=document)))
    assert captured.value.code == "VOICE_EMBEDDING_ALL_WINDOWS_FAILED"
