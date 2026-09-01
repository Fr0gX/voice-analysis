from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from voice_analysis_api.app import create_app
from voice_analysis_api.config import ApiConfig
from voice_analysis_api.store import TaskStore, now_ms


KEY = "test-key"
SEGMENTS = json.dumps({
    "schema_version": "voice_analysis_input_v1",
    "segments": [{"id": "s1", "start_ms": 0, "end_ms": 500, "text": "hello"}],
}).encode()


@dataclass
class FakeReservation:
    task_id: str


class FakeBackend:
    def __init__(self, store: TaskStore, busy: bool = False) -> None:
        self.store = store
        self.busy = busy
        self.cancelled: set[str] = set()

    async def ready(self):
        return not self.busy

    async def reserve(self, task_id):
        return None if self.busy else FakeReservation(task_id)

    async def start(self, reservation, sensitive=None):
        if sensitive is not None:
            sensitive.clear()
        directory = self.store.task_dir(reservation.task_id) / "result"
        directory.mkdir()
        (directory / "result.json").write_text('{"status":"partial"}\n', encoding="utf-8")
        for name in ("transcript.txt", "transcript.srt", "transcript.vtt"):
            (directory / name).write_text("result\n", encoding="utf-8")
        await self.store.update(
            reservation.task_id,
            status="succeeded",
            result_status="partial",
            stage="completed",
            started_at_ms=now_ms(),
            finished_at_ms=now_ms(),
        )

    async def release(self, reservation):
        return None

    async def cancel(self, task_id):
        self.cancelled.add(task_id)
        await self.store.update(task_id, status="cancelled", finished_at_ms=now_ms())
        return True

    async def recover(self):
        return None

    async def close(self):
        return None

    def summary(self):
        return {"slots": 1, "occupied": int(self.busy)}


def config(root: Path) -> ApiConfig:
    return ApiConfig(
        task_root=root,
        retention_seconds=86400,
        expired_metadata_seconds=86400,
        cleanup_interval_seconds=3600,
        max_segments_bytes=1024 * 1024,
        max_audio_bytes=1024 * 1024,
        backend_slots=1,
        shutdown_grace_seconds=0.1,
        api_key=KEY,
        embedding_ready_url="http://127.0.0.1:1/ready",
        refine_ready_url="http://127.0.0.1:1/ready",
    )


def make_client(tmp_path: Path, *, busy: bool = False):
    cfg = config(tmp_path / "tasks")
    store = TaskStore(cfg.task_root)
    backend = FakeBackend(store, busy=busy)
    return TestClient(create_app(cfg, backend)), store, backend


def create(client: TestClient, **headers):
    return client.post(
        "/v1/tasks",
        files={"audio": ("evil/../recording.wav", b"RIFFfake", "audio/wav"), "segments": ("segments.json", SEGMENTS, "application/json")},
        headers={"X-Voice-Analysis-Key": KEY, **headers},
    )


def create_cloud(client: TestClient, provider="tencent"):
    credentials = ({"secret_id": "secret-id", "secret_key": "secret-key", "app_id": "app"}
                   if provider == "tencent" else
                   {"access_key_id": "access-id", "access_key_secret": "access-secret", "app_key": "app"})
    return client.post("/v1/tasks", data={
        "input_mode": "cloud_asr", "asr_provider": provider,
        "asr_credentials": json.dumps(credentials), "asr_options": json.dumps({"model": "test"}),
    }, files={"audio": ("recording.wav", b"RIFFfake", "audio/wav")})


def test_local_create_result_export_and_delete_without_auth(tmp_path):
    client, store, _ = make_client(tmp_path)
    with client:
        assert client.get("/v1/tasks/" + "0" * 32).status_code == 404
        response = create(client)
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        status = client.get(f"/v1/tasks/{task_id}", headers={"X-Voice-Analysis-Key": KEY})
        assert status.json()["status"] == "succeeded"
        assert status.json()["result_status"] == "partial"
        assert client.get(f"/v1/tasks/{task_id}/result", headers={"X-Voice-Analysis-Key": KEY}).status_code == 200
        assert client.get(f"/v1/tasks/{task_id}/exports/txt", headers={"X-Voice-Analysis-Key": KEY}).status_code == 200
        assert not (store.task_dir(task_id) / "input" / "evil").exists()
        assert client.delete(f"/v1/tasks/{task_id}", headers={"X-Voice-Analysis-Key": KEY}).status_code == 204
        assert client.get(f"/v1/tasks/{task_id}", headers={"X-Voice-Analysis-Key": KEY}).status_code == 404


def test_busy_rejects_without_task_directory(tmp_path):
    client, store, _ = make_client(tmp_path, busy=True)
    with client:
        response = create(client)
        assert response.status_code == 429
        assert store.list_tasks() == []


def test_idempotency_reuses_and_rejects_changed_input(tmp_path):
    client, store, _ = make_client(tmp_path)
    with client:
        first = create(client, **{"Idempotency-Key": "same"})
        second = create(client, **{"Idempotency-Key": "same"})
        assert second.status_code == 200
        assert second.json()["task_id"] == first.json()["task_id"]
        changed = client.post(
            "/v1/tasks",
            files={"audio": ("audio", b"different", "application/octet-stream"), "segments": ("segments", SEGMENTS, "application/json")},
            headers={"X-Voice-Analysis-Key": KEY, "Idempotency-Key": "same"},
        )
        assert changed.status_code == 409
        assert len(store.list_tasks()) == 1


def test_validation_happens_before_admission(tmp_path):
    client, store, _ = make_client(tmp_path)
    with client:
        response = client.post(
            "/v1/tasks",
            files={"audio": ("audio", b"x"), "segments": ("segments", b"{}")},
            headers={"X-Voice-Analysis-Key": KEY},
        )
        assert response.status_code == 400
        assert store.list_tasks() == []


def test_cloud_asr_credentials_are_never_persisted(tmp_path):
    client, store, _ = make_client(tmp_path)
    with client:
        response = create_cloud(client)
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        assert response.json()["input_mode"] == "cloud_asr"
        persisted = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in store.task_dir(task_id).rglob("*") if p.is_file())
        assert "secret-id" not in persisted
        assert "secret-key" not in persisted
        assert not (store.task_dir(task_id) / "input" / "segments.json").exists()


def test_input_modes_are_mutually_exclusive(tmp_path):
    client, store, _ = make_client(tmp_path)
    with client:
        response = client.post("/v1/tasks", data={"input_mode": "cloud_asr", "asr_provider": "tencent", "asr_credentials": "{}", "asr_options": "{}"}, files={"audio": ("a", b"x"), "segments": ("s", SEGMENTS)})
        assert response.status_code == 400
        assert store.list_tasks() == []


def test_non_finite_deadline_is_rejected(tmp_path):
    client, store, _ = make_client(tmp_path)
    with client:
        response = client.post(
            "/v1/tasks",
            data={"deadline_sec": "NaN"},
            files={"audio": ("audio", b"x"), "segments": ("segments", SEGMENTS)},
            headers={"X-Voice-Analysis-Key": KEY},
        )
        assert response.status_code == 400
        assert store.list_tasks() == []


def test_cleanup_keeps_short_expired_fact_then_deletes(tmp_path):
    client, store, _ = make_client(tmp_path)
    with client:
        task_id = create(client).json()["task_id"]
        task = store.read(task_id)
        task["finished_at_ms"] = 1
        store._write(task)
        import asyncio
        asyncio.run(store.cleanup(1, 86400))
        expired = store.read(task_id)
        assert expired["status"] == "expired"
        assert not (store.task_dir(task_id) / "input").exists()
        expired["expired_at_ms"] = 1
        store._write(expired)
        asyncio.run(store.cleanup(1, 1))
        assert store.list_tasks() == []
