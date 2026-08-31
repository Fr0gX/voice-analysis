"""Opt-in real-model parity gate used by release CI on the golden PCM corpus."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from voice_embedding_service.config import ServiceConfig, configure_process_runtime
from voice_embedding_service.ecapa_backend import ECAPAVoiceprintBackend


@pytest.mark.skipif(
    not os.getenv("VOICEPRINT_PARITY_PCM_DIR"),
    reason="set VOICEPRINT_PARITY_PCM_DIR to the private golden 16k PCM corpus",
)
def test_real_batch_matches_single_window_baseline() -> None:
    corpus = Path(os.environ["VOICEPRINT_PARITY_PCM_DIR"])
    paths = sorted(corpus.glob("*.pcm"))
    assert paths, "golden corpus contains no .pcm files"
    cfg = ServiceConfig.from_env()
    configure_process_runtime(cfg)
    backend = ECAPAVoiceprintBackend(cfg.model_dir)
    assert backend.available, backend.error
    audio = [
        np.frombuffer(path.read_bytes(), dtype="<i2").astype(np.float32) / 32768.0
        for path in paths
    ]
    single = np.stack([backend.extract_batch([samples], cfg.sample_rate)[0][0] for samples in audio])
    batched_rows = []
    for start in range(0, len(audio), cfg.max_batch_windows):
        batched_rows.extend(backend.extract_batch(audio[start : start + cfg.max_batch_windows], cfg.sample_rate)[0])
    batched = np.stack(batched_rows)
    cosine = np.sum(single * batched, axis=1)
    assert float(np.min(cosine)) >= 0.999
    assert float(np.quantile(cosine, 0.001)) >= 0.9999
