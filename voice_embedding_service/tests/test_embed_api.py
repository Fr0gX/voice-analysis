from __future__ import annotations

import json
import time

import numpy as np
from fastapi.testclient import TestClient

from voice_embedding_service.app import create_app, create_backend
from voice_embedding_service.config import ServiceConfig


_API_KEY = "test-api-key"


def _config(**overrides) -> ServiceConfig:
    return ServiceConfig(api_key=_API_KEY, **overrides)


class _FakeBackend:
    model_version = "ecapa_voxceleb_v1"
    backend_name = "fake"
    loaded = True
    error = None
    device = "cpu"

    @property
    def available(self) -> bool:
        return True

    def extract_batch(self, audio_batch: list[np.ndarray], sample_rate: int):
        rows = []
        for index, audio in enumerate(audio_batch):
            vector = np.zeros(192, dtype=np.float32)
            vector[index % 192] = 1.0
            rows.append(vector)
        return np.stack(rows), {"model_version": self.model_version}


def _metadata(windows: list[dict], **overrides) -> dict:
    value = {
        "request_id": "task-1",
        "expected_model_version": "ecapa_voxceleb_v1",
        "deadline_ms": int(time.time() * 1000) + 10_000,
        "sample_rate": 16_000,
        "windows": windows,
    }
    value.update(overrides)
    return value


def _post(client: TestClient, metadata: dict, pcm: bytes):
    return client.post(
        "/embed",
        headers={"X-Voice-Analysis-Key": _API_KEY},
        data={"metadata": json.dumps(metadata)},
        files={"audio": ("windows.pcm", pcm, "application/octet-stream")},
    )


def test_health_reports_runtime_and_queue() -> None:
    with TestClient(create_app(config=_config(), backend=_FakeBackend())) as client:
        body = client.get("/health").json()
    assert body["model_ready"] is True
    assert body["model_version"] == "ecapa_voxceleb_v1"
    assert body["queue"]["queued_requests"] == 0
    assert "torch_threads" in body["runtime"]
    assert "rss_bytes" in body["runtime"]
    assert "native_threads" in body["runtime"]
    with TestClient(create_app(config=_config(), backend=_FakeBackend())) as client:
        assert client.get("/health/ready").status_code == 200


def test_embed_returns_items_by_window_id_in_request_order() -> None:
    first = (np.ones(16_000, dtype="<i2") * 1000).tobytes()
    second = (np.ones(8_000, dtype="<i2") * 500).tobytes()
    pcm = first + second
    windows = [
        {"window_id": "sentence:1", "offset": 0, "length": len(first), "kind": "sentence"},
        {"window_id": "gold:1", "offset": len(first), "length": len(second), "kind": "gold"},
    ]
    with TestClient(create_app(config=_config(), backend=_FakeBackend())) as client:
        response = _post(client, _metadata(windows), pcm)
    assert response.status_code == 200
    body = response.json()
    assert [item["window_id"] for item in body["items"]] == ["sentence:1", "gold:1"]
    assert all(item["status"] == "success" for item in body["items"])
    assert len(body["items"][0]["embedding"]) == 192


def test_too_short_is_explicit_and_has_no_zero_vector() -> None:
    pcm = np.ones(100, dtype="<i2").tobytes()
    windows = [{"window_id": "short", "offset": 0, "length": len(pcm), "kind": "sentence"}]
    with TestClient(create_app(config=_config(), backend=_FakeBackend())) as client:
        response = _post(client, _metadata(windows), pcm)
    assert response.status_code == 200
    assert response.json()["items"] == [{
        "window_id": "short", "status": "too_short", "embedding": None, "error": None
    }]


def test_invalid_boundary_is_isolated_from_valid_window() -> None:
    pcm = np.ones(8_000, dtype="<i2").tobytes()
    good = {"window_id": "w", "offset": 0, "length": len(pcm), "kind": "gold"}
    bad = {"window_id": "bad", "offset": 0, "length": len(pcm) + 2, "kind": "gold"}
    with TestClient(create_app(config=_config(), backend=_FakeBackend())) as client:
        response = _post(client, _metadata([bad, good]), pcm)
    assert response.status_code == 200
    assert [item["status"] for item in response.json()["items"]] == [
        "invalid_boundary",
        "success",
    ]


def test_duplicate_ids_model_and_deadline_are_rejected() -> None:
    pcm = np.ones(8_000, dtype="<i2").tobytes()
    good = {"window_id": "w", "offset": 0, "length": len(pcm), "kind": "gold"}
    cases = [
        (_metadata([good, good]), 400),
        (_metadata([good], expected_model_version="wrong"), 400),
        (_metadata([good], deadline_ms=int(time.time() * 1000) - 1), 504),
    ]
    with TestClient(create_app(config=_config(), backend=_FakeBackend())) as client:
        for metadata, expected in cases:
            assert _post(client, metadata, pcm).status_code == expected


def test_request_size_limit() -> None:
    cfg = _config(max_request_bytes=100)
    pcm = b"\x00" * 102
    windows = [{"window_id": "w", "offset": 0, "length": 102, "kind": "gold"}]
    with TestClient(create_app(config=cfg, backend=_FakeBackend())) as client:
        assert _post(client, _metadata(windows), pcm).status_code == 413


def test_audio_part_requires_binary_pcm_content_type() -> None:
    pcm = np.ones(8_000, dtype="<i2").tobytes()
    windows = [{"window_id": "w", "offset": 0, "length": len(pcm), "kind": "gold"}]
    with TestClient(create_app(config=_config(), backend=_FakeBackend())) as client:
        response = client.post(
            "/embed",
            headers={"X-Voice-Analysis-Key": _API_KEY},
            data={"metadata": json.dumps(_metadata(windows))},
            files={"audio": ("windows.txt", pcm, "text/plain")},
        )
    assert response.status_code == 400


def test_pcm_payload_requires_int16_alignment() -> None:
    pcm = b"\x00" * 101
    windows = [{"window_id": "w", "offset": 0, "length": 100, "kind": "gold"}]
    with TestClient(create_app(config=_config(), backend=_FakeBackend())) as client:
        response = _post(client, _metadata(windows), pcm)
    assert response.status_code == 400


def test_embed_requires_api_key() -> None:
    pcm = np.ones(8_000, dtype="<i2").tobytes()
    windows = [{"window_id": "w", "offset": 0, "length": len(pcm), "kind": "gold"}]
    with TestClient(create_app(config=_config(), backend=_FakeBackend())) as client:
        response = client.post(
            "/embed",
            data={"metadata": json.dumps(_metadata(windows))},
            files={"audio": ("windows.pcm", pcm, "application/octet-stream")},
        )
    assert response.status_code == 401


def test_create_backend_rejects_other_algorithms() -> None:
    try:
        create_backend(ServiceConfig(backend="modelscope_eres2netv2"))
    except ValueError as exc:
        assert "unsupported VOICEPRINT_BACKEND" in str(exc)
    else:
        raise AssertionError("non-ECAPA backend must not be selectable")
