"""Runtime configuration for the Voice Analysis ECAPA service."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODEL_DIR = _REPO_ROOT / "runtime" / "models" / "ecapa" / "spkrec-ecapa-voxceleb"


@dataclass(frozen=True)
class ServiceConfig:
    host: str = "0.0.0.0"
    port: int = 8077
    backend: str = "speechbrain_ecapa"
    model_dir: Path = _DEFAULT_MODEL_DIR
    sample_rate: int = 16_000
    min_clip_ms: int = 400
    max_clip_ms: int = 30_000
    embedding_dim: int = 192
    max_request_bytes: int = 32 * 1024 * 1024
    max_queue_requests: int = 4
    max_queue_bytes: int = 128 * 1024 * 1024
    max_batch_windows: int = 16
    max_batch_audio_seconds: float = 60.0
    microbatch_wait_ms: int = 10
    torch_threads: int = 4
    torch_interop_threads: int = 1
    api_key: str = ""
    auth_header: str = "X-Voice-Analysis-Key"

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        return cls(
            host=os.getenv("VOICE_EMBEDDING_HOST", os.getenv("VOICEPRINT_HOST", "0.0.0.0")),
            port=int(os.getenv("VOICE_EMBEDDING_PORT", os.getenv("VOICEPRINT_PORT", "8077"))),
            backend=os.getenv("VOICEPRINT_BACKEND", "speechbrain_ecapa"),
            model_dir=Path(os.getenv("ECAPA_MODEL_DIR", str(_DEFAULT_MODEL_DIR))),
            sample_rate=int(os.getenv("VOICEPRINT_SAMPLE_RATE", "16000")),
            min_clip_ms=int(os.getenv("VOICEPRINT_MIN_CLIP_MS", "400")),
            max_clip_ms=int(os.getenv("VOICEPRINT_MAX_CLIP_MS", "30000")),
            embedding_dim=int(os.getenv("VOICEPRINT_EMBED_DIM", "192")),
            max_request_bytes=int(os.getenv("VOICEPRINT_MAX_REQUEST_BYTES", str(32 * 1024 * 1024))),
            max_queue_requests=int(os.getenv("VOICEPRINT_QUEUE_REQUESTS", "4")),
            max_queue_bytes=int(os.getenv("VOICEPRINT_QUEUE_BYTES", str(128 * 1024 * 1024))),
            max_batch_windows=int(os.getenv("VOICEPRINT_BATCH_WINDOWS", "16")),
            max_batch_audio_seconds=float(os.getenv("VOICEPRINT_BATCH_AUDIO_SECONDS", "60")),
            microbatch_wait_ms=int(os.getenv("VOICEPRINT_MICROBATCH_WAIT_MS", "10")),
            torch_threads=int(os.getenv("VOICEPRINT_TORCH_THREADS", "4")),
            torch_interop_threads=int(os.getenv("VOICEPRINT_TORCH_INTEROP_THREADS", "1")),
            api_key=os.getenv("VOICEANALYSIS_API_KEY", ""),
            auth_header=os.getenv("VOICEANALYSIS_AUTH_HEADER", "X-Voice-Analysis-Key"),
        )


def configure_process_runtime(cfg: ServiceConfig) -> None:
    """Set native runtime bounds before torch/model initialization."""
    value = str(cfg.torch_threads)
    os.environ["OMP_NUM_THREADS"] = value
    os.environ["MKL_NUM_THREADS"] = value
    os.environ["OPENBLAS_NUM_THREADS"] = value
    os.environ.setdefault("MALLOC_ARENA_MAX", "2")
    try:
        import torch

        torch.set_num_threads(cfg.torch_threads)
        torch.set_num_interop_threads(cfg.torch_interop_threads)
    except (ImportError, RuntimeError):
        # RuntimeError means inter-op threads were already initialized. Health
        # exposes actual values so a deployment mismatch remains visible.
        pass
