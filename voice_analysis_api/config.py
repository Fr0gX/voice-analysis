"""Configuration for the M2 task API."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ApiConfig:
    task_root: Path
    retention_seconds: int
    expired_metadata_seconds: int
    cleanup_interval_seconds: int
    max_segments_bytes: int
    max_audio_bytes: int
    backend_slots: int
    shutdown_grace_seconds: float
    api_key: str
    embedding_ready_url: str
    refine_ready_url: str


def load_api_config() -> ApiConfig:
    raw = yaml.safe_load((_REPO_ROOT / "config" / "services.yaml").read_text(encoding="utf-8"))
    section = dict(raw.get("task_api") or {})
    root = Path(os.getenv("VOICEANALYSIS_TASK_ROOT", section.get("task_root", "runtime/tasks")))
    if not root.is_absolute():
        root = (_REPO_ROOT / root).resolve()
    return ApiConfig(
        task_root=root,
        retention_seconds=int(section.get("retention_seconds", 86400)),
        expired_metadata_seconds=int(section.get("expired_metadata_seconds", 86400)),
        cleanup_interval_seconds=int(section.get("cleanup_interval_seconds", 300)),
        max_segments_bytes=int(section.get("max_segments_bytes", 16 * 1024 * 1024)),
        max_audio_bytes=int(section.get("max_audio_bytes", 1024 * 1024 * 1024)),
        backend_slots=int(section.get("backend_slots", 1)),
        shutdown_grace_seconds=float(section.get("shutdown_grace_seconds", 10)),
        api_key=os.getenv("VOICEANALYSIS_API_KEY", ""),
        embedding_ready_url=os.getenv("VOICE_EMBEDDING_BASE_URL", "http://127.0.0.1:8077").rstrip("/") + "/health/ready",
        refine_ready_url=os.getenv("WINDOW_REFINE_BASE_URL", "http://127.0.0.1:8078").rstrip("/") + "/health/ready",
    )
