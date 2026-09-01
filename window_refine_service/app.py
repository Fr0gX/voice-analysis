"""HTTP service for pyannote-based speech-window refinement.

POST /segment receives the original audio path and ASR candidate windows, then
returns smaller continuous single-speaker speech-window candidates.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
import secrets
import threading
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .config import ServiceConfig
from .pyannote_backend import PyannoteWindowRefineBackend

logger = logging.getLogger("window_refine_service")


class RefineBackend(Protocol):
    backend_name: str
    loaded: bool
    error: str | None
    device: str | None

    @property
    def available(self) -> bool: ...

    def segment(
        self,
        *,
        audio_path: str,
        asr_candidate_windows: list[dict[str, Any]],
        profile: dict[str, Any],
        speech_db_threshold: float,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...


class SegmentRequest(BaseModel):
    audio_path: str
    asr_candidate_windows: list[dict[str, Any]] = Field(default_factory=list)
    profile: dict[str, Any] = Field(default_factory=dict)
    speech_db_threshold: float = -45.0


class SegmentResponse(BaseModel):
    success: bool
    backend: str
    speech_window_candidates: list[dict[str, Any]] = Field(default_factory=list)
    candidate_results: list[dict[str, Any]] = Field(default_factory=list)
    audit: dict[str, Any] = Field(default_factory=dict)


def create_backend(cfg: ServiceConfig) -> RefineBackend:
    backend = cfg.backend.strip().lower()
    if backend == "pyannote_segmentation":
        return PyannoteWindowRefineBackend(cfg)
    raise ValueError(f"unsupported WINDOW_REFINE_BACKEND: {cfg.backend}")


def create_app(
    *,
    config: ServiceConfig | None = None,
    backend: RefineBackend | None = None,
) -> FastAPI:
    cfg = config or ServiceConfig.from_env()
    refine_backend: RefineBackend = backend or create_backend(cfg)
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        _ = refine_backend.available
        if getattr(refine_backend, "loaded", False):
            logger.info("window refine model preloaded: %s", cfg.segmentation_model_dir)
        else:
            logger.warning("window refine model preload failed: %s", getattr(refine_backend, "error", None))
        yield

    app = FastAPI(title="Voice Analysis Window Refine Service", version="1.0.0", lifespan=lifespan)

    def require_api_key(request: Request) -> None:
        provided = request.headers.get(cfg.auth_header, "")
        if not cfg.api_key or not secrets.compare_digest(provided, cfg.api_key):
            raise HTTPException(status_code=401, detail="invalid API key")

    def resolve_audio_path(raw: str) -> str:
        audio_root = cfg.audio_root.resolve()
        temporary_root = cfg.temporary_root.resolve()
        requested = Path(raw)
        if requested.is_absolute():
            resolved = requested.resolve()
        else:
            cwd_candidate = (Path.cwd() / requested).resolve()
            if cwd_candidate == audio_root or audio_root in cwd_candidate.parents:
                resolved = cwd_candidate
            elif cwd_candidate == temporary_root or temporary_root in cwd_candidate.parents:
                resolved = cwd_candidate
            else:
                resolved = (audio_root / requested).resolve()
        allowed = (
            resolved == audio_root
            or audio_root in resolved.parents
            or resolved == temporary_root
            or temporary_root in resolved.parents
        )
        if not allowed:
            raise HTTPException(status_code=400, detail="audio_path must be inside an allowed Voice Analysis runtime root")
        if not resolved.is_file():
            raise HTTPException(status_code=400, detail="audio_path does not exist")
        return str(resolved)

    @app.get("/health")
    def health() -> dict[str, Any]:
        ready = bool(getattr(refine_backend, "loaded", False))
        return {
            "status": "ok" if ready else "model_unavailable",
            "model_ready": ready,
            "backend": getattr(refine_backend, "backend_name", cfg.backend),
            "segmentation_model_dir": str(cfg.segmentation_model_dir),
            "osd_model_dir": str(cfg.osd_model_dir),
            "osd_enabled": bool(cfg.osd_enabled),
            "osd_ready": bool(getattr(refine_backend, "_osd_pipeline", None) is not None),
            "osd_error": getattr(refine_backend, "osd_error", None),
            "overlap_source": (
                "legacy_osd_pipeline"
                if getattr(refine_backend, "_osd_pipeline", None) is not None
                else "segmentation_powerset"
            ),
            "device": getattr(refine_backend, "device", None),
            "model_error": getattr(refine_backend, "error", None),
        }

    @app.get("/health/ready")
    def ready() -> dict[str, Any]:
        if not bool(getattr(refine_backend, "loaded", False)):
            raise HTTPException(status_code=503, detail="pyannote model is not ready")
        return {
            "status": "ready",
            "backend": getattr(refine_backend, "backend_name", cfg.backend),
        }

    @app.post("/segment", response_model=SegmentResponse)
    def segment(req: SegmentRequest, request: Request) -> SegmentResponse:
        require_api_key(request)
        audio_path = resolve_audio_path(req.audio_path)
        try:
            if not refine_backend.available:
                raise RuntimeError(getattr(refine_backend, "error", None) or "model unavailable")
            with lock:
                windows, audit = refine_backend.segment(
                    audio_path=audio_path,
                    asr_candidate_windows=req.asr_candidate_windows,
                    profile=req.profile,
                    speech_db_threshold=float(req.speech_db_threshold),
                )
            candidate_results = list(audit.get("candidate_results") or [])
            all_failed = bool(req.asr_candidate_windows) and bool(candidate_results) and all(
                str(row.get("status") or "") == "inference_failed"
                for row in candidate_results
            )
            return SegmentResponse(
                success=not all_failed,
                backend=getattr(refine_backend, "backend_name", cfg.backend),
                speech_window_candidates=windows,
                candidate_results=candidate_results,
                audit=audit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("window refine segment failed: %s", exc, exc_info=True)
            return SegmentResponse(
                success=False,
                backend=getattr(refine_backend, "backend_name", cfg.backend),
                speech_window_candidates=[],
                candidate_results=[],
                audit={
                    "error": f"{type(exc).__name__}: {exc}",
                    "model_ready": bool(getattr(refine_backend, "loaded", False)),
                    "segmentation_model_dir": str(cfg.segmentation_model_dir),
                    "osd_model_dir": str(cfg.osd_model_dir),
                },
            )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    cfg = ServiceConfig.from_env()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
