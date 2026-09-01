"""Atomic local task facts and controlled task assets."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"succeeded", "failed", "cancelled", "expired"}


def now_ms() -> int:
    return int(time.time() * 1000)


class TaskStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._locks: dict[str, asyncio.Lock] = {}
        self.root.mkdir(parents=True, exist_ok=True)

    def task_dir(self, task_id: str) -> Path:
        if len(task_id) != 32 or any(ch not in "0123456789abcdef" for ch in task_id):
            raise KeyError(task_id)
        path = (self.root / task_id).resolve()
        if path.parent != self.root:
            raise KeyError(task_id)
        return path

    def read(self, task_id: str) -> dict[str, Any]:
        path = self.task_dir(task_id) / "task.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KeyError(task_id) from exc
        if not isinstance(value, dict) or value.get("task_id") != task_id:
            raise KeyError(task_id)
        return value

    def list_tasks(self) -> list[dict[str, Any]]:
        tasks = []
        for path in self.root.iterdir():
            if path.is_dir():
                try:
                    tasks.append(self.read(path.name))
                except KeyError:
                    continue
        return tasks

    def find_idempotency(self, key: str) -> dict[str, Any] | None:
        for task in self.list_tasks():
            if task.get("idempotency_key") == key and task.get("status") != "expired":
                return task
        return None

    async def create(self, task: dict[str, Any], audio, segments_raw: bytes | None) -> None:
        directory = self.task_dir(task["task_id"])
        input_dir = directory / "input"
        input_dir.mkdir(parents=True, exist_ok=False)
        try:
            await asyncio.to_thread(self._copy_upload, audio, input_dir / "audio")
            if segments_raw is not None:
                (input_dir / "segments.json").write_bytes(segments_raw)
            self._write(task)
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    @staticmethod
    def _copy_upload(upload, destination: Path) -> None:
        upload.file.seek(0)
        with destination.open("xb") as target:
            shutil.copyfileobj(upload.file, target, length=1024 * 1024)

    async def update(self, task_id: str, **changes: Any) -> dict[str, Any]:
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            task = self.read(task_id)
            task.update(changes)
            task["updated_at_ms"] = now_ms()
            self._write(task)
            return task

    async def update_active(self, task_id: str, **changes: Any) -> dict[str, Any] | None:
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            try:
                task = self.read(task_id)
            except KeyError:
                return None
            if task.get("status") not in {"queued", "running"}:
                return None
            task.update(changes)
            task["updated_at_ms"] = now_ms()
            self._write(task)
            return task

    async def request_cancel(self, task_id: str) -> dict[str, Any] | None:
        return await self.update_active(task_id, cancel_requested_at_ms=now_ms())

    async def finish(self, task_id: str, **changes: Any) -> dict[str, Any] | None:
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            try:
                task = self.read(task_id)
            except KeyError:
                return None
            if task.get("status") not in {"queued", "running"}:
                return task
            if task.get("cancel_requested_at_ms") is not None:
                changes = {"status": "cancelled", "stage": None, "finished_at_ms": now_ms()}
            task.update(changes)
            task["updated_at_ms"] = now_ms()
            self._write(task)
            return task

    def _write(self, task: dict[str, Any]) -> None:
        path = self.task_dir(task["task_id"]) / "task.json"
        partial = path.with_name("task.json.part")
        partial.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(partial, path)

    async def delete(self, task_id: str) -> None:
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            directory = self.task_dir(task_id)
            if not (directory / "task.json").is_file():
                raise KeyError(task_id)
            await asyncio.to_thread(shutil.rmtree, directory)
        self._locks.pop(task_id, None)

    async def cleanup(self, retention_seconds: int, expired_metadata_seconds: int) -> None:
        current = now_ms()
        for task in self.list_tasks():
            task_id = task["task_id"]
            try:
                if task.get("status") == "expired":
                    if current - int(task.get("expired_at_ms") or current) >= expired_metadata_seconds * 1000:
                        await self.delete(task_id)
                    continue
                if task.get("status") not in TERMINAL_STATES:
                    continue
                finished = int(task.get("finished_at_ms") or task.get("updated_at_ms") or current)
                if current - finished < retention_seconds * 1000:
                    continue
                directory = self.task_dir(task_id)
                await asyncio.to_thread(shutil.rmtree, directory / "input", True)
                await asyncio.to_thread(shutil.rmtree, directory / "result", True)
                minimal = {
                    "schema_version": "voice_analysis_task_v1",
                    "task_id": task_id,
                    "status": "expired",
                    "created_at_ms": task.get("created_at_ms"),
                    "updated_at_ms": current,
                    "expired_at_ms": current,
                }
                self._write(minimal)
            except (KeyError, OSError):
                continue
