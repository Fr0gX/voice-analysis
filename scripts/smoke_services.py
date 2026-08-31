from __future__ import annotations

from array import array
import json
import math
import os
from pathlib import Path
import time
import wave

import httpx
import numpy as np


REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIO_PATH = REPO_ROOT / "runtime" / "audio" / "model-smoke.wav"
API_KEY = os.environ.get("VOICEANALYSIS_API_KEY", "")
AUTH_HEADER = os.environ.get("VOICEANALYSIS_AUTH_HEADER", "X-Voice-Analysis-Key")


def _make_probe() -> tuple[bytes, int]:
    sample_rate = 16_000
    samples = array(
        "h",
        (
            int(2500 * math.sin(2.0 * math.pi * 220.0 * i / sample_rate))
            for i in range(sample_rate * 2)
        ),
    )
    AUDIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(AUDIO_PATH), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())
    return samples.tobytes(), sample_rate


def main() -> None:
    if not API_KEY:
        raise RuntimeError("VOICEANALYSIS_API_KEY is missing")
    pcm, sample_rate = _make_probe()
    headers = {AUTH_HEADER: API_KEY}
    metadata = {
        "request_id": "local-model-smoke",
        "expected_model_version": "ecapa_voxceleb_v1",
        "deadline_ms": int(time.time() * 1000) + 60_000,
        "sample_rate": sample_rate,
        "windows": [
            {
                "window_id": "sentence:0",
                "offset": 0,
                "length": len(pcm),
                "kind": "sentence",
            }
        ],
    }
    with httpx.Client(timeout=90.0) as client:
        embed = client.post(
            "http://127.0.0.1:8077/embed",
            headers=headers,
            data={"metadata": json.dumps(metadata)},
            files={"audio": ("model-smoke.pcm", pcm, "application/octet-stream")},
        )
        embed.raise_for_status()
        item = embed.json()["items"][0]
        vector = np.asarray(item.get("embedding") or [], dtype=np.float32)
        if item.get("status") != "success" or vector.size != 192:
            raise RuntimeError(f"unexpected embedding response: {item}")
        if not np.isclose(float(np.linalg.norm(vector)), 1.0, atol=1e-4):
            raise RuntimeError("embedding is not L2 normalized")

        refine = client.post(
            "http://127.0.0.1:8078/segment",
            headers=headers,
            json={
                "audio_path": str(AUDIO_PATH),
                "asr_candidate_windows": [
                    {
                        "candidate_id": "asr_candidate_000",
                        "start_ms": 0,
                        "end_ms": 2000,
                        "source_segment_indices": [0],
                    }
                ],
                "profile": {"clean_window": {}},
                "speech_db_threshold": -45.0,
            },
        )
        refine.raise_for_status()
        if refine.json().get("success") is not True:
            raise RuntimeError(f"unexpected window-refine response: {refine.text}")
    print("Real ECAPA and pyannote service smoke passed")


if __name__ == "__main__":
    main()
