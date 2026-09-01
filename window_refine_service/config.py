"""Runtime config for the local speech-window refinement service."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODEL_ROOT = _REPO_ROOT / "runtime" / "models" / "pyannote"
_DEFAULT_AUDIO_ROOT = _REPO_ROOT / "runtime" / "audio"
_DEFAULT_TEMPORARY_ROOT = _REPO_ROOT / "runtime" / "tmp"


@dataclass(frozen=True)
class ServiceConfig:
    host: str = "0.0.0.0"
    port: int = 8078
    backend: str = "pyannote_segmentation"
    model_root: Path = _DEFAULT_MODEL_ROOT
    segmentation_model_dir: Path = _DEFAULT_MODEL_ROOT / "segmentation-3.0"
    osd_model_dir: Path = _DEFAULT_MODEL_ROOT / "overlapped-speech-detection"
    osd_enabled: bool = False
    sample_rate: int = 16_000
    device: str = "auto"
    allow_model_download: bool = False
    hf_token_env: str = "HUGGINGFACE_ACCESS_TOKEN"
    audio_root: Path = _DEFAULT_AUDIO_ROOT
    temporary_root: Path = _DEFAULT_TEMPORARY_ROOT
    api_key: str = ""
    auth_header: str = "X-Voice-Analysis-Key"

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        model_root = Path(os.getenv("WINDOW_REFINE_MODEL_ROOT", str(_DEFAULT_MODEL_ROOT)))
        return cls(
            host=os.getenv("WINDOW_REFINE_HOST", "0.0.0.0"),
            port=int(os.getenv("WINDOW_REFINE_PORT", "8078")),
            backend=os.getenv("WINDOW_REFINE_BACKEND", "pyannote_segmentation"),
            model_root=model_root,
            segmentation_model_dir=Path(
                os.getenv(
                    "PYANNOTE_SEGMENTATION_MODEL_DIR",
                    str(model_root / "segmentation-3.0"),
                )
            ),
            osd_model_dir=Path(
                os.getenv(
                    "PYANNOTE_OSD_MODEL_DIR",
                    str(model_root / "overlapped-speech-detection"),
                )
            ),
            osd_enabled=os.getenv("PYANNOTE_OSD_ENABLED", "0") == "1",
            sample_rate=int(os.getenv("WINDOW_REFINE_SAMPLE_RATE", "16000")),
            device=os.getenv("WINDOW_REFINE_DEVICE", "auto"),
            allow_model_download=os.getenv("WINDOW_REFINE_ALLOW_MODEL_DOWNLOAD", "0") == "1",
            hf_token_env=os.getenv("PYANNOTE_AUTH_TOKEN_ENV", "HUGGINGFACE_ACCESS_TOKEN"),
            audio_root=Path(os.getenv("VOICEANALYSIS_AUDIO_ROOT", str(_DEFAULT_AUDIO_ROOT))),
            temporary_root=Path(os.getenv("VOICEANALYSIS_TEMPORARY_ROOT", str(_DEFAULT_TEMPORARY_ROOT))),
            api_key=os.getenv("VOICEANALYSIS_API_KEY", ""),
            auth_header=os.getenv("VOICEANALYSIS_AUTH_HEADER", "X-Voice-Analysis-Key"),
        )
