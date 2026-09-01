from __future__ import annotations

import asyncio
import json
from pathlib import Path

from voice_analysis_api.backend import LocalAnalysisBackend
from voice_analysis_api.store import TaskStore, now_ms


DOCUMENT = {
    "schema_version": "voice_analysis_input_v1",
    "segments": [{"id": "s", "start_ms": 0, "end_ms": 500, "text": "x"}],
}


class FakeResult:
    status = "partial"


class FakeEngine:
    async def analyze(self, request, *, progress):
        progress({"stage": "audio_probe", "status": "started"})
        request.output_dir.mkdir(parents=True)
        (request.output_dir / "result.json").write_text("{}", encoding="utf-8")
        progress({"stage": "audio_probe", "status": "completed"})
        return FakeResult()


class HangingEngine:
    async def analyze(self, request, *, progress):
        await asyncio.Event().wait()


def seed(store: TaskStore, task_id: str, status: str = "queued", **extra):
    directory = store.task_dir(task_id)
    (directory / "input").mkdir(parents=True)
    (directory / "input" / "audio").write_bytes(b"audio")
    (directory / "input" / "segments.json").write_text(json.dumps(DOCUMENT), encoding="utf-8")
    store._write({
        "schema_version": "voice_analysis_task_v1",
        "task_id": task_id,
        "status": status,
        "created_at_ms": now_ms(),
        "updated_at_ms": now_ms(),
        "deadline_epoch_ms": None,
        **extra,
    })


def test_backend_has_atomic_single_slot_and_maps_partial_to_succeeded(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks")
        first = "1" * 32
        second = "2" * 32
        seed(store, first)
        backend = LocalAnalysisBackend(store, slots=1, engine_factory=FakeEngine)
        reservation = await backend.reserve(first)
        assert reservation is not None
        assert await backend.reserve(second) is None
        await backend.start(reservation)
        await backend._running[first]
        assert store.read(first)["status"] == "succeeded"
        assert store.read(first)["result_status"] == "partial"
        assert await backend.reserve(second) is not None

    asyncio.run(scenario())


def test_recovery_cancels_requested_task_without_running_it(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks")
        task_id = "3" * 32
        seed(store, task_id, status="running", cancel_requested_at_ms=now_ms())
        backend = LocalAnalysisBackend(store, slots=1, engine_factory=FakeEngine)
        await backend.recover()
        assert store.read(task_id)["status"] == "cancelled"
        assert backend.summary()["occupied"] == 0

    asyncio.run(scenario())


def test_recovery_fails_cloud_asr_when_ephemeral_credentials_were_lost(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks")
        task_id = "6" * 32
        seed(store, task_id, status="running", input_mode="cloud_asr", asr_provider="tencent")
        backend = LocalAnalysisBackend(store, slots=1, engine_factory=FakeEngine)
        await backend.recover()
        task = store.read(task_id)
        assert task["status"] == "failed"
        assert task["error"]["code"] == "CREDENTIAL_LOST"
        assert backend.summary()["occupied"] == 0

    asyncio.run(scenario())


def test_finish_cannot_publish_success_after_cancel_request(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks")
        task_id = "4" * 32
        seed(store, task_id, status="running")
        assert await store.request_cancel(task_id) is not None
        finished = await store.finish(task_id, status="succeeded", result_status="success", finished_at_ms=now_ms())
        assert finished["status"] == "cancelled"
        assert "result_status" not in finished

    asyncio.run(scenario())


def test_close_is_bounded_and_leaves_running_task_for_recovery(tmp_path):
    async def scenario():
        store = TaskStore(tmp_path / "tasks")
        task_id = "5" * 32
        seed(store, task_id)
        backend = LocalAnalysisBackend(store, slots=1, engine_factory=HangingEngine, shutdown_grace_seconds=0.01)
        reservation = await backend.reserve(task_id)
        await backend.start(reservation)
        await asyncio.sleep(0)
        await asyncio.wait_for(backend.close(), timeout=0.5)
        assert store.read(task_id)["status"] == "running"
        assert backend.summary()["occupied"] == 0

    asyncio.run(scenario())
