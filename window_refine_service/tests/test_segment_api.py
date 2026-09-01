from __future__ import annotations

import wave
from array import array
from pathlib import Path

from fastapi.testclient import TestClient

from window_refine_service.app import create_app
from window_refine_service.config import ServiceConfig


_API_KEY = "test-api-key"


def _config(audio_root: Path, **overrides) -> ServiceConfig:
    return ServiceConfig(audio_root=audio_root, api_key=_API_KEY, **overrides)


class _FakeBackend:
    backend_name = "fake_pyannote"
    loaded = True
    error = None
    device = "cpu"

    @property
    def available(self) -> bool:
        return True

    def segment(self, *, audio_path, asr_candidate_windows, profile, speech_db_threshold):
        return (
            [
                {
                    "window_id": "speech_window_000",
                    "source_candidate_id": asr_candidate_windows[0]["candidate_id"],
                    "start_ms": 0,
                    "end_ms": 3000,
                    "duration_ms": 3000,
                    "raw_start_ms": 0,
                    "raw_end_ms": 3000,
                    "source_segment_indices": [0],
                    "backend": "fake_pyannote",
                    "speech_ratio": 1.0,
                    "overlap_ms": 0,
                    "change_point_count": 0,
                    "boundary_left_silence_ms": 0,
                    "boundary_right_silence_ms": 0,
                    "loudness_db": -20.0,
                    "loudness_std_db": 0.0,
                    "flags": [],
                    "reject_reasons": [],
                }
            ],
            {"selected_count": 1, "backend": "fake_pyannote"},
        )


class _MissingBackend:
    backend_name = "pyannote_segmentation"
    loaded = False
    error = "RuntimeError: local model directory not found"
    device = None

    @property
    def available(self) -> bool:
        return False

    def segment(self, **_kwargs):  # pragma: no cover - defensive
        raise RuntimeError("should not be called")


def _write_wav(path: Path, seconds: float = 1.0, sample_rate: int = 16_000) -> None:
    n = int(seconds * sample_rate)
    samples = array("h", [1000] * n)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())


def test_health_reports_ready() -> None:
    client = TestClient(create_app(config=ServiceConfig(), backend=_FakeBackend()))

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["model_ready"] is True
    assert body["backend"] == "fake_pyannote"
    assert client.get("/health/ready").status_code == 200


def test_segment_returns_candidates(tmp_path: Path) -> None:
    audio = tmp_path / "demo.wav"
    _write_wav(audio)
    client = TestClient(create_app(config=_config(tmp_path), backend=_FakeBackend()))

    resp = client.post(
        "/segment",
        headers={"X-Voice-Analysis-Key": _API_KEY},
        json={
            "audio_path": str(audio),
            "asr_candidate_windows": [
                {"candidate_id": "asr_candidate_000", "start_ms": 0, "end_ms": 3000, "source_segment_indices": [0]}
            ],
            "profile": {"clean_window": {}},
            "speech_db_threshold": -45.0,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["backend"] == "fake_pyannote"
    assert body["speech_window_candidates"][0]["window_id"] == "speech_window_000"
    assert body["audit"]["selected_count"] == 1


def test_segment_requires_api_key_and_rejects_path_escape(tmp_path: Path) -> None:
    inside = tmp_path / "inside.wav"
    _write_wav(inside)
    outside_root = tmp_path / "allowed"
    outside_root.mkdir()
    client = TestClient(create_app(config=_config(outside_root), backend=_FakeBackend()))

    unauthorized = client.post(
        "/segment",
        json={"audio_path": str(inside), "asr_candidate_windows": []},
    )
    assert unauthorized.status_code == 401

    escaped = client.post(
        "/segment",
        headers={"X-Voice-Analysis-Key": _API_KEY},
        json={"audio_path": str(inside), "asr_candidate_windows": []},
    )
    assert escaped.status_code == 400


def test_segment_allows_only_configured_temporary_root(tmp_path: Path) -> None:
    audio_root = tmp_path / "audio"
    temporary_root = tmp_path / "temporary"
    audio_root.mkdir()
    temporary_root.mkdir()
    audio = temporary_root / "normalized.wav"
    _write_wav(audio)
    client = TestClient(create_app(
        config=_config(audio_root, temporary_root=temporary_root),
        backend=_FakeBackend(),
    ))

    response = client.post(
        "/segment",
        headers={"X-Voice-Analysis-Key": _API_KEY},
        json={
            "audio_path": str(audio),
            "asr_candidate_windows": [
                {"candidate_id": "asr_candidate_000", "start_ms": 0, "end_ms": 1000}
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_health_reports_model_unavailable() -> None:
    client = TestClient(create_app(config=ServiceConfig(), backend=_MissingBackend()))

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "model_unavailable"
    assert body["model_ready"] is False
    assert "local model directory" in body["model_error"]
    assert client.get("/health/ready").status_code == 503
