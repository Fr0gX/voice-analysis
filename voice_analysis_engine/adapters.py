"""HTTP ports for the existing 8078 and 8077 model services."""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from .audio import PcmReader
from .config import AnalysisConfig
from .errors import component_error, deadline_error


class WindowRefinePort(Protocol):
    async def refine(
        self,
        audio_path: Path,
        candidates: list[dict[str, Any]],
        profile: dict[str, Any],
        deadline_epoch_ms: int | None,
    ) -> dict[str, Any]: ...


class EmbeddingPort(Protocol):
    async def embed(
        self,
        reader: PcmReader,
        windows: list[dict[str, Any]],
        run_id: str,
        deadline_epoch_ms: int | None,
    ) -> dict[str, Any]: ...


def _api_key() -> str:
    value = os.getenv("VOICEANALYSIS_API_KEY", "")
    if not value:
        raise component_error("COMPONENT_AUTH_MISSING", "VOICEANALYSIS_API_KEY is not configured", "component_ready", retryable=False)
    return value


def _timeout(connect_timeout: float, deadline_epoch_ms: int | None, stage: str) -> httpx.Timeout:
    if deadline_epoch_ms is None:
        return httpx.Timeout(None, connect=connect_timeout)
    remaining = (deadline_epoch_ms - int(time.time() * 1000)) / 1000.0
    if remaining <= 0:
        raise deadline_error(stage)
    return httpx.Timeout(remaining, connect=min(connect_timeout, remaining))


class HttpWindowRefineClient:
    def __init__(self, cfg: AnalysisConfig):
        self.cfg = cfg.section("components")["window_refine"]
        self.auth_header = os.getenv("VOICEANALYSIS_AUTH_HEADER", "X-Voice-Analysis-Key")

    async def refine(self, audio_path, candidates, profile, deadline_epoch_ms):
        base = str(self.cfg["base_url"]).rstrip("/")
        timeout = _timeout(float(self.cfg["connect_timeout_sec"]), deadline_epoch_ms, "window_refine")
        headers = {self.auth_header: _api_key()}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                ready = await client.get(base + str(self.cfg["ready_path"]))
                if ready.status_code != 200:
                    raise component_error("WINDOW_REFINE_UNAVAILABLE", "window refine model is not ready", "window_refine")
                response = await client.post(
                    base + str(self.cfg["operation_path"]),
                    headers=headers,
                    json={
                        "audio_path": str(audio_path.resolve()),
                        "asr_candidate_windows": candidates,
                        "profile": profile,
                        "speech_db_threshold": -45.0,
                    },
                )
        except httpx.TimeoutException as exc:
            if deadline_epoch_ms is not None and int(time.time() * 1000) >= deadline_epoch_ms:
                raise deadline_error("window_refine") from exc
            raise component_error("WINDOW_REFINE_TIMEOUT", "window refine request timed out", "window_refine") from exc
        except httpx.HTTPError as exc:
            raise component_error("WINDOW_REFINE_UNAVAILABLE", "window refine request failed", "window_refine") from exc
        if response.status_code == 401:
            raise component_error("WINDOW_REFINE_AUTH", "window refine authentication failed", "window_refine", retryable=False)
        if response.status_code != 200:
            raise component_error("WINDOW_REFINE_PROTOCOL", f"window refine returned HTTP {response.status_code}", "window_refine")
        try:
            payload = response.json()
        except ValueError as exc:
            raise component_error("WINDOW_REFINE_PROTOCOL", "window refine response is not JSON", "window_refine") from exc
        if not payload.get("success"):
            raise component_error("WINDOW_REFINE_INFERENCE", "window refine inference failed", "window_refine")
        windows = payload.get("speech_window_candidates")
        if not isinstance(windows, list):
            raise component_error("WINDOW_REFINE_PROTOCOL", "window refine response is missing windows", "window_refine")
        return {
            "backend": str(payload.get("backend") or "unknown"),
            "windows": windows,
            "candidate_results": payload.get("candidate_results") or [],
            "audit": payload.get("audit") or {},
        }


@dataclass(frozen=True)
class _PackedWindow:
    window_id: str
    start_ms: int
    end_ms: int
    kind: str

    @property
    def duration_ms(self) -> int:
        return max(0, min(30000, self.end_ms - self.start_ms))


class HttpEmbeddingClient:
    def __init__(self, cfg: AnalysisConfig):
        self.cfg = cfg.section("components")["voice_embedding"]
        self.algorithm = cfg.section("algorithm")["embedding"]
        self.auth_header = os.getenv("VOICEANALYSIS_AUTH_HEADER", "X-Voice-Analysis-Key")

    async def embed(self, reader, windows, run_id, deadline_epoch_ms):
        model_version = await self._ready(deadline_epoch_ms)
        unique: dict[tuple[int, int], _PackedWindow] = {}
        aliases: dict[str, str] = {}
        immediate: dict[str, dict[str, Any]] = {}
        for row in windows:
            start, end = int(row["start_ms"]), int(row["end_ms"])
            if end <= start:
                immediate[str(row["window_id"])] = {"status": "invalid_boundary", "embedding": None}
                continue
            key = (start, min(end, start + 30000))
            if key not in unique:
                unique[key] = _PackedWindow(str(row["window_id"]), start, end, str(row["kind"]))
            aliases[str(row["window_id"])] = unique[key].window_id
        ordered = sorted(unique.values(), key=lambda item: (item.duration_ms, item.start_ms, item.end_ms, item.window_id))
        batches = plan_embedding_batches(ordered, self.algorithm)
        canonical: dict[str, dict[str, Any]] = {}
        batch_audit: list[dict[str, Any]] = []
        for index, batch in enumerate(batches):
            values, audit = await self._send_batch(reader, batch, run_id, index, model_version, deadline_epoch_ms)
            canonical.update(values)
            batch_audit.append(audit)
        results = dict(immediate)
        for requested, canonical_id in aliases.items():
            results[requested] = dict(canonical.get(canonical_id) or {"status": "inference_failed", "embedding": None})
        return {
            "model_version": model_version,
            "backend": "speechbrain_ecapa",
            "items": results,
            "audit": {
                "requested_window_count": len(windows),
                "unique_window_count": len(unique),
                "deduplicated_count": max(0, len(windows) - len(unique)),
                "batch_count": len(batches),
                "batches": batch_audit,
            },
        }

    async def _ready(self, deadline_epoch_ms: int | None) -> str:
        base = str(self.cfg["base_url"]).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=_timeout(float(self.cfg["connect_timeout_sec"]), deadline_epoch_ms, "voice_embedding")) as client:
                response = await client.get(base + str(self.cfg["ready_path"]))
        except httpx.TimeoutException as exc:
            raise component_error("VOICE_EMBEDDING_TIMEOUT", "voice embedding readiness timed out", "voice_embedding") from exc
        except httpx.HTTPError as exc:
            raise component_error("VOICE_EMBEDDING_UNAVAILABLE", "voice embedding service is unavailable", "voice_embedding") from exc
        if response.status_code != 200:
            raise component_error("VOICE_EMBEDDING_UNAVAILABLE", "voice embedding model is not ready", "voice_embedding")
        try:
            model_version = str(response.json()["model_version"])
        except (ValueError, KeyError, TypeError) as exc:
            raise component_error("VOICE_EMBEDDING_PROTOCOL", "voice embedding ready response is invalid", "voice_embedding") from exc
        return model_version

    async def _send_batch(self, reader, batch, run_id, index, model_version, deadline_epoch_ms):
        parts: list[bytes] = []
        metadata_windows: list[dict[str, Any]] = []
        offset = 0
        for item in batch:
            raw = await asyncio.to_thread(reader.read_ms, item.start_ms, item.end_ms, maximum_ms=30000)
            parts.append(raw)
            metadata_windows.append({
                "window_id": item.window_id,
                "offset": offset,
                "length": len(raw),
                "kind": item.kind,
            })
            offset += len(raw)
        pcm = b"".join(parts)
        deadline = deadline_epoch_ms or (int(time.time() * 1000) + 24 * 60 * 60 * 1000)
        metadata = {
            "request_id": f"{run_id}:{index}",
            "expected_model_version": model_version,
            "deadline_ms": deadline,
            "sample_rate": 16000,
            "windows": metadata_windows,
        }
        base = str(self.cfg["base_url"]).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=_timeout(float(self.cfg["connect_timeout_sec"]), deadline_epoch_ms, "voice_embedding")) as client:
                response = await client.post(
                    base + str(self.cfg["operation_path"]),
                    headers={self.auth_header: _api_key()},
                    data={"metadata": json.dumps(metadata, separators=(",", ":"))},
                    files={"audio": ("batch.pcm", pcm, "application/octet-stream")},
                )
        except httpx.TimeoutException as exc:
            if deadline_epoch_ms is not None and int(time.time() * 1000) >= deadline_epoch_ms:
                raise deadline_error("voice_embedding") from exc
            raise component_error("VOICE_EMBEDDING_TIMEOUT", "voice embedding request timed out", "voice_embedding") from exc
        except httpx.HTTPError as exc:
            raise component_error("VOICE_EMBEDDING_UNAVAILABLE", "voice embedding request failed", "voice_embedding") from exc
        if response.status_code == 401:
            raise component_error("VOICE_EMBEDDING_AUTH", "voice embedding authentication failed", "voice_embedding", retryable=False)
        if response.status_code in {429, 503, 504}:
            raise component_error("VOICE_EMBEDDING_OVERLOADED", f"voice embedding returned HTTP {response.status_code}", "voice_embedding")
        if response.status_code != 200:
            raise component_error("VOICE_EMBEDDING_PROTOCOL", f"voice embedding returned HTTP {response.status_code}", "voice_embedding")
        try:
            payload = response.json()
            rows = payload["items"]
        except (ValueError, KeyError, TypeError) as exc:
            raise component_error("VOICE_EMBEDDING_PROTOCOL", "voice embedding response is invalid", "voice_embedding") from exc
        items = {
            str(row["window_id"]): {
                "status": str(row.get("status") or "inference_failed"),
                "embedding": row.get("embedding"),
                "error": row.get("error"),
            }
            for row in rows
        }
        return items, {
            "batch_index": index,
            "window_count": len(batch),
            "audio_seconds": round(sum(item.duration_ms for item in batch) / 1000.0, 3),
            "pcm_bytes": len(pcm),
        }


def plan_embedding_batches(
    ordered: list[_PackedWindow],
    limits: dict[str, Any],
) -> list[list[_PackedWindow]]:
    batches: list[list[_PackedWindow]] = []
    current: list[_PackedWindow] = []
    seconds = 0.0
    size = 0
    for item in ordered:
        item_seconds = item.duration_ms / 1000.0
        item_size = item.duration_ms * 16000 * 2 // 1000
        if current and (
            len(current) >= int(limits["batch_windows"])
            or seconds + item_seconds > float(limits["batch_audio_seconds"])
            or size + item_size > int(limits["maximum_request_bytes"])
        ):
            batches.append(current)
            current, seconds, size = [], 0.0, 0
        current.append(item)
        seconds += item_seconds
        size += item_size
    if current:
        batches.append(current)
    return batches
