"""Bounded, batch-only ECAPA voiceprint HTTP service."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import secrets
import time
from typing import Any, Protocol

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError, model_validator

from .config import ServiceConfig, configure_process_runtime
from .ecapa_backend import ECAPAVoiceprintBackend
from .scheduler import InferenceRequest, InferenceScheduler, InferenceWindow, QueueOverloaded

logger = logging.getLogger("voice_embedding_service")


def _process_metrics() -> dict[str, int | None]:
    """Return Linux cgroup-visible process metrics without adding psutil."""
    status_path = Path("/proc/self/status")
    if not status_path.is_file():
        return {"rss_bytes": None, "native_threads": None}
    values: dict[str, int | None] = {"rss_bytes": None, "native_threads": None}
    try:
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                values["rss_bytes"] = int(line.split()[1]) * 1024
            elif line.startswith("Threads:"):
                values["native_threads"] = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return {"rss_bytes": None, "native_threads": None}
    return values


class EmbeddingBackend(Protocol):
    @property
    def available(self) -> bool: ...
    @property
    def loaded(self) -> bool: ...
    def extract_batch(self, audio_batch: list[np.ndarray], sample_rate: int): ...


class WindowMetadata(BaseModel):
    window_id: str = Field(min_length=1, max_length=200)
    offset: int = Field(ge=0)
    length: int = Field(gt=0)
    kind: str

    @model_validator(mode="after")
    def validate_pcm_alignment(self) -> "WindowMetadata":
        if self.offset % 2 or self.length % 2:
            raise ValueError("PCM window offsets and lengths must be int16 aligned")
        if self.kind not in {"sentence", "gold"}:
            raise ValueError("window kind must be sentence or gold")
        return self


class EmbedMetadata(BaseModel):
    request_id: str = Field(min_length=1, max_length=200)
    expected_model_version: str
    deadline_ms: int = Field(gt=0)
    sample_rate: int
    windows: list[WindowMetadata] = Field(min_length=1)


class EmbedItem(BaseModel):
    window_id: str
    status: str
    embedding: list[float] | None = None
    error: str | None = None


class EmbedResponse(BaseModel):
    request_id: str
    model_version: str
    backend: str
    items: list[EmbedItem]


def create_backend(cfg: ServiceConfig) -> EmbeddingBackend:
    if cfg.backend.strip().lower() != "speechbrain_ecapa":
        raise ValueError(f"unsupported VOICEPRINT_BACKEND: {cfg.backend}")
    return ECAPAVoiceprintBackend(cfg.model_dir)


def _parse_metadata(raw: str, cfg: ServiceConfig, model_version: str) -> EmbedMetadata:
    try:
        metadata = EmbedMetadata.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid metadata: {exc}") from exc
    if metadata.sample_rate != cfg.sample_rate:
        raise HTTPException(status_code=400, detail=f"sample_rate must be {cfg.sample_rate}")
    if metadata.expected_model_version != model_version:
        raise HTTPException(status_code=400, detail="model version mismatch")
    if metadata.deadline_ms <= int(time.time() * 1000):
        raise HTTPException(status_code=504, detail="request deadline exceeded")
    ids = [item.window_id for item in metadata.windows]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=400, detail="duplicate window_id")
    return metadata


def create_app(*, config: ServiceConfig | None = None, backend: EmbeddingBackend | None = None) -> FastAPI:
    cfg = config or ServiceConfig.from_env()
    configure_process_runtime(cfg)
    vp_backend = backend or create_backend(cfg)
    scheduler = InferenceScheduler(vp_backend, cfg)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        _ = vp_backend.available
        if not bool(getattr(vp_backend, "loaded", False)):
            logger.error("ECAPA model preload failed: %s", getattr(vp_backend, "error", None))
        scheduler.start()
        yield
        scheduler.stop()

    app = FastAPI(title="Voice Analysis Embedding Service", version="1.0.0", lifespan=lifespan)
    app.state.inference_scheduler = scheduler

    def require_api_key(request: Request) -> None:
        provided = request.headers.get(cfg.auth_header, "")
        if not cfg.api_key or not secrets.compare_digest(provided, cfg.api_key):
            raise HTTPException(status_code=401, detail="invalid API key")

    @app.get("/health")
    def health() -> dict[str, Any]:
        ready = bool(getattr(vp_backend, "loaded", False))
        process_metrics = _process_metrics()
        try:
            import torch
            actual_threads = torch.get_num_threads()
            actual_interop_threads = torch.get_num_interop_threads()
        except ImportError:  # pragma: no cover
            actual_threads = None
            actual_interop_threads = None
        return {
            "status": "ok" if ready else "model_unavailable",
            "model_ready": ready,
            "backend": getattr(vp_backend, "backend_name", cfg.backend),
            "model_version": getattr(vp_backend, "model_version", "unknown"),
            "embedding_dim": cfg.embedding_dim,
            "device": getattr(vp_backend, "device", None),
            "queue": scheduler.snapshot(),
            "runtime": {
                "torch_threads": actual_threads,
                "torch_interop_threads": actual_interop_threads,
                **process_metrics,
            },
        }

    @app.get("/health/ready")
    def ready() -> dict[str, Any]:
        if not bool(getattr(vp_backend, "loaded", False)):
            raise HTTPException(status_code=503, detail="ECAPA model is not ready")
        return {
            "status": "ready",
            "backend": getattr(vp_backend, "backend_name", cfg.backend),
            "model_version": getattr(vp_backend, "model_version", "unknown"),
        }

    @app.post("/embed", response_model=EmbedResponse)
    async def embed(
        request: Request,
        metadata: str = Form(...),
        audio: UploadFile = File(...),
    ) -> EmbedResponse:
        require_api_key(request)
        if not bool(getattr(vp_backend, "loaded", False)):
            raise HTTPException(status_code=503, detail="ECAPA model is not ready")
        if audio.content_type != "application/octet-stream":
            raise HTTPException(status_code=400, detail="audio part must be application/octet-stream")
        pcm = await audio.read(cfg.max_request_bytes + 1)
        if len(pcm) > cfg.max_request_bytes:
            raise HTTPException(status_code=413, detail="PCM request exceeds 32 MiB")
        if len(pcm) % 2:
            raise HTTPException(status_code=400, detail="PCM payload must be int16 aligned")
        model_version = str(getattr(vp_backend, "model_version", "unknown"))
        parsed = _parse_metadata(metadata, cfg, model_version)
        windows: list[InferenceWindow] = []
        immediate: dict[str, EmbedItem] = {}
        min_samples = cfg.min_clip_ms * cfg.sample_rate // 1000
        max_samples = cfg.max_clip_ms * cfg.sample_rate // 1000
        for item in parsed.windows:
            if item.offset + item.length > len(pcm):
                immediate[item.window_id] = EmbedItem(
                    window_id=item.window_id,
                    status="invalid_boundary",
                    error="PCM window is out of bounds",
                )
                continue
            raw = pcm[item.offset : item.offset + item.length]
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if len(samples) < min_samples:
                immediate[item.window_id] = EmbedItem(window_id=item.window_id, status="too_short")
            else:
                # Preserve the previous ECAPA input rule: an individual window
                # contributes at most its first 30 seconds.
                windows.append(InferenceWindow(window_id=item.window_id, audio=samples[:max_samples]))
        inferred: dict[str, dict[str, Any]] = {}
        if windows:
            request = InferenceRequest(
                request_id=parsed.request_id,
                windows=windows,
                pcm_bytes=len(pcm),
                deadline_ms=parsed.deadline_ms,
            )
            try:
                future = scheduler.submit(request)
            except QueueOverloaded as exc:
                raise HTTPException(status_code=429, detail=str(exc)) from exc
            result = await asyncio.wrap_future(future)
            if result["deadline_exceeded"]:
                raise HTTPException(status_code=504, detail="request deadline exceeded")
            inferred = result["items"]
        items: list[EmbedItem] = []
        for spec in parsed.windows:
            if spec.window_id in immediate:
                items.append(immediate[spec.window_id])
                continue
            raw_item = inferred.get(spec.window_id)
            if raw_item is None:
                raise HTTPException(status_code=500, detail=f"missing inference result: {spec.window_id}")
            items.append(EmbedItem(window_id=spec.window_id, **raw_item))
        return EmbedResponse(
            request_id=parsed.request_id,
            model_version=model_version,
            backend=str(getattr(vp_backend, "backend_name", cfg.backend)),
            items=items,
        )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    cfg = ServiceConfig.from_env()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=cfg.host, port=cfg.port, workers=1)


if __name__ == "__main__":
    main()
