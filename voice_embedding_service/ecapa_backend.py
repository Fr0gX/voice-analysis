"""SpeechBrain ECAPA batch inference backend."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

MODEL_VERSION = "ecapa_voxceleb_v1"


class ECAPAVoiceprintBackend:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = Path(model_dir)
        self._classifier = None
        self.error: str | None = None
        self.backend_name = "speechbrain_ecapa"

    @property
    def model_version(self) -> str:
        return MODEL_VERSION

    @property
    def loaded(self) -> bool:
        return self._classifier is not None

    @property
    def available(self) -> bool:
        self._ensure_model()
        return self.loaded

    @property
    def device(self) -> str | None:
        if self._classifier is None:
            return None
        return str(getattr(self._classifier, "device", "cpu"))

    def _ensure_model(self) -> None:
        if self._classifier is not None or self.error:
            return
        try:
            from speechbrain.inference.speaker import EncoderClassifier

            kwargs: dict[str, Any] = {"source": str(self.model_dir), "savedir": str(self.model_dir)}
            try:
                from speechbrain.utils.fetching import LocalStrategy

                kwargs["local_strategy"] = LocalStrategy.COPY
            except Exception:  # pragma: no cover - older SpeechBrain
                pass
            self._classifier = EncoderClassifier.from_hparams(**kwargs)
            for module in getattr(self._classifier, "mods", {}).values():
                module.eval()
        except Exception as exc:  # pragma: no cover - optional runtime/model
            self.error = str(exc)

    def extract_batch(
        self, audio_batch: list[np.ndarray], sample_rate: int
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Return one L2-normalized vector per variable-length PCM window."""
        self._ensure_model()
        if self._classifier is None:
            raise RuntimeError(f"ECAPA model unavailable: {self.error}")
        if not audio_batch:
            return np.empty((0, 192), dtype=np.float32), {}

        import torch

        lengths = [int(len(audio)) for audio in audio_batch]
        max_len = max(lengths)
        padded = np.zeros((len(audio_batch), max_len), dtype=np.float32)
        for index, audio in enumerate(audio_batch):
            padded[index, : lengths[index]] = np.asarray(audio, dtype=np.float32)
        wavs = torch.from_numpy(padded)
        wav_lens = torch.tensor([length / max_len for length in lengths], dtype=torch.float32)
        with torch.inference_mode():
            embeddings = self._classifier.encode_batch(wavs, wav_lens=wav_lens, normalize=False)
        matrix = embeddings.squeeze(1).detach().cpu().numpy().astype(np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        if np.any(norms <= 0):
            raise RuntimeError("ECAPA produced a zero-norm embedding")
        matrix = matrix / norms
        return matrix, {
            "embedding_backend": self.backend_name,
            "model_version": self.model_version,
            "dimension": int(matrix.shape[1]),
        }
