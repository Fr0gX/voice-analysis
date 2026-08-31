"""Download the gated pyannote segmentation model into runtime/models.

Prerequisites:
1. Accept the model terms for pyannote/segmentation-3.0 on HuggingFace.
2. Set HUGGINGFACE_ACCESS_TOKEN to a token that can access them.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from huggingface_hub import snapshot_download
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing dependency 'huggingface_hub'. Install the window-refine "
        "environment first: pip install -r environments/window-refine/requirements.txt"
    ) from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = Path(
    os.getenv("WINDOW_REFINE_MODEL_ROOT", str(REPO_ROOT / "runtime" / "models" / "pyannote"))
)
TOKEN_ENV = os.getenv("PYANNOTE_AUTH_TOKEN_ENV", "HUGGINGFACE_ACCESS_TOKEN")
TOKEN = os.getenv(TOKEN_ENV)


MODELS = [
    ("pyannote/segmentation-3.0", MODEL_ROOT / "segmentation-3.0"),
]


def main() -> None:
    if not TOKEN:
        raise SystemExit(f"Missing HuggingFace token env: {TOKEN_ENV}")
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    for repo_id, local_dir in MODELS:
        print(f"[window-refine] downloading {repo_id} -> {local_dir}")
        snapshot_download(
            repo_id=repo_id,
            local_dir=str(local_dir),
            token=TOKEN,
            local_dir_use_symlinks=False,
        )
    print("[window-refine] model download complete")


if __name__ == "__main__":
    main()
