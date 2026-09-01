"""Replaceable in-process delivery boundary for M2."""
from __future__ import annotations

import asyncio
import httpx
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from voice_analysis_engine import AnalysisEngine
from voice_analysis_engine.contracts import AnalysisRequest, SegmentDocument
from voice_analysis_engine.errors import EngineError, internal_error

from .store import TaskStore, now_ms
from .asr import AliyunAsrProvider, AsrError, AsrProvider, TencentAsrProvider


class BackendReservation(Protocol):
    task_id: str


class AnalysisBackend(Protocol):
    async def ready(self) -> bool: ...
    async def reserve(self, task_id: str) -> BackendReservation | None: ...
    async def start(self, reservation: BackendReservation, sensitive: dict | None = None) -> None: ...
    async def release(self, reservation: BackendReservation) -> None: ...
    async def cancel(self, task_id: str) -> bool: ...
    async def recover(self) -> None: ...
    async def close(self) -> None: ...
    def summary(self) -> dict[str, int]: ...


@dataclass
class Reservation:
    task_id: str
    consumed: bool = False


class LocalAnalysisBackend:
    def __init__(self, store: TaskStore, slots: int = 1, engine_factory=AnalysisEngine, ready_urls: tuple[str, ...] = (), shutdown_grace_seconds: float = 10, asr_providers: dict[str, AsrProvider] | None = None) -> None:
        self.store = store
        self.slots = max(1, slots)
        self.engine_factory = engine_factory
        self.ready_urls = ready_urls
        self.shutdown_grace_seconds = max(0.0, shutdown_grace_seconds)
        self._lock = asyncio.Lock()
        self._reserved: dict[str, Reservation] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()
        self._recovery: asyncio.Task | None = None
        self._recovering: set[str] = set()
        self._sensitive: dict[str, dict] = {}
        self.asr_providers = asr_providers or {"tencent": TencentAsrProvider(), "aliyun": AliyunAsrProvider()}

    async def ready(self) -> bool:
        if not self.ready_urls:
            return True
        async with httpx.AsyncClient(timeout=2.0) as client:
            for url in self.ready_urls:
                try:
                    if (await client.get(url)).status_code != 200:
                        return False
                except httpx.HTTPError:
                    return False
        return True

    async def reserve(self, task_id: str) -> Reservation | None:
        async with self._lock:
            if len(self._reserved) + len(self._running) >= self.slots:
                return None
            reservation = Reservation(task_id)
            self._reserved[task_id] = reservation
            return reservation

    async def start(self, reservation: Reservation, sensitive: dict | None = None) -> None:
        async with self._lock:
            current = self._reserved.pop(reservation.task_id, None)
            if current is not reservation:
                raise RuntimeError("invalid backend reservation")
            reservation.consumed = True
            if sensitive is not None:
                self._sensitive[reservation.task_id] = sensitive
            task = asyncio.create_task(self._run(reservation.task_id), name=f"analysis-{reservation.task_id}")
            self._running[reservation.task_id] = task

    async def release(self, reservation: Reservation) -> None:
        async with self._lock:
            self._reserved.pop(reservation.task_id, None)

    async def cancel(self, task_id: str) -> bool:
        self._cancelled.add(task_id)
        if task_id in self._reserved:
            return True
        return task_id in self._running or task_id in self._recovering

    async def recover(self) -> None:
        pending = []
        for task in self.store.list_tasks():
            if task.get("status") not in {"queued", "running"}:
                continue
            if task.get("cancel_requested_at_ms") is not None:
                await self._mark_cancelled(task["task_id"])
            elif task.get("input_mode") == "cloud_asr":
                await self.store.finish(task["task_id"], status="failed", stage="transcribing", finished_at_ms=now_ms(), error={
                    "code": "CREDENTIAL_LOST", "stage": "transcribing",
                    "message": "ephemeral ASR credentials were lost during restart", "retryable": False,
                })
            else:
                pending.append(task["task_id"])
        if pending:
            self._recovering.update(pending)
            self._recovery = asyncio.create_task(self._recover_all(pending), name="analysis-recovery")

    async def _recover_all(self, task_ids: list[str]) -> None:
        for task_id in task_ids:
            try:
                if task_id in self._cancelled:
                    await self._mark_cancelled(task_id)
                    continue
                await self.store.update(task_id, status="queued", stage=None, started_at_ms=None)
                while True:
                    reservation = await self.reserve(task_id)
                    if reservation is not None:
                        await self.start(reservation)
                        running = self._running.get(task_id)
                        if running is not None:
                            await asyncio.shield(running)
                        break
                    await asyncio.sleep(0.1)
            except KeyError:
                continue
            finally:
                self._recovering.discard(task_id)

    async def _run(self, task_id: str) -> None:
        directory = self.store.task_dir(task_id)
        progress_tasks: set[asyncio.Task] = set()
        try:
            if task_id in self._cancelled:
                await self._mark_cancelled(task_id)
                return
            task = await self.store.update(task_id, status="running", started_at_ms=now_ms(), stage="starting")
            if task.get("input_mode") == "cloud_asr":
                sensitive = self._sensitive.get(task_id)
                if sensitive is None:
                    raise AsrError("CREDENTIAL_LOST", "ephemeral ASR credentials are unavailable")
                provider_name = str(task.get("asr_provider") or "")
                provider = self.asr_providers.get(provider_name)
                if provider is None:
                    raise AsrError("ASR_PROVIDER_INVALID", "unsupported ASR provider")
                await self.store.update_active(task_id, stage="transcribing")
                transcript = await provider.transcribe(
                    directory / "input" / "audio", sensitive["credentials"], sensitive["options"],
                    cancelled=lambda: task_id in self._cancelled,
                    deadline_epoch_ms=task.get("deadline_epoch_ms"),
                )
                await self.store.update_active(task_id, stage="normalizing_transcript", transcript_source=transcript.source)
                (directory / "input" / "segments.json").write_text(
                    transcript.document.model_dump_json(exclude_unset=True), encoding="utf-8"
                )
            document = SegmentDocument.model_validate_json((directory / "input" / "segments.json").read_bytes())
            engine = self.engine_factory()

            def progress(event: dict[str, object]) -> None:
                if task_id in self._cancelled:
                    raise EngineError("TASK_CANCELLED", "task cancellation requested", str(event.get("stage") or "analysis"), 22, False)
                update = asyncio.get_running_loop().create_task(self.store.update_active(task_id, stage=event.get("stage")))
                progress_tasks.add(update)
                update.add_done_callback(progress_tasks.discard)

            result = await engine.analyze(
                AnalysisRequest(
                    audio_path=directory / "input" / "audio",
                    document=document,
                    output_dir=directory / "result",
                    deadline_epoch_ms=task.get("deadline_epoch_ms"),
                ),
                progress=progress,
            )
            if progress_tasks:
                await asyncio.gather(*progress_tasks, return_exceptions=True)
            await self.store.update_active(task_id, stage="exporting")
            if task.get("input_mode") == "cloud_asr":
                self._attach_transcript_source(directory / "result" / "result.json", self.store.read(task_id).get("transcript_source"))
            current = self.store.read(task_id)
            if task_id in self._cancelled or current.get("cancel_requested_at_ms") is not None:
                await asyncio.to_thread(self._discard_result, directory / "result")
                await self._mark_cancelled(task_id)
            else:
                await self.store.finish(task_id, status="succeeded", stage="completed", result_status=result.status, finished_at_ms=now_ms())
        except AsrError as exc:
            if exc.code == "TASK_CANCELLED":
                await self._mark_cancelled(task_id)
            else:
                await self.store.finish(task_id, status="failed", stage="transcribing", finished_at_ms=now_ms(), error={
                    "code": exc.code, "stage": "transcribing", "message": exc.message, "retryable": exc.retryable,
                })
        except (EngineError, ValidationError) as exc:
            if isinstance(exc, EngineError) and exc.code == "TASK_CANCELLED":
                await asyncio.to_thread(self._discard_result, directory / "result")
                await self._mark_cancelled(task_id)
            else:
                error = exc if isinstance(exc, EngineError) else internal_error("stored task input is invalid", "input")
                await self.store.finish(task_id, status="failed", stage=error.stage, finished_at_ms=now_ms(), error={
                    "code": error.code, "stage": error.stage, "message": error.message, "retryable": error.retryable,
                })
        except Exception:  # noqa: BLE001
            await self.store.finish(task_id, status="failed", stage="internal", finished_at_ms=now_ms(), error={
                "code": "INTERNAL_ERROR", "stage": "internal", "message": "unexpected internal error", "retryable": False,
            })
        finally:
            async with self._lock:
                self._running.pop(task_id, None)
            self._cancelled.discard(task_id)
            sensitive = self._sensitive.pop(task_id, None)
            if sensitive is not None:
                sensitive.clear()

    async def _mark_cancelled(self, task_id: str) -> None:
        await self.store.finish(task_id, status="cancelled", stage=None, finished_at_ms=now_ms())

    @staticmethod
    def _discard_result(path: Path) -> None:
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _attach_transcript_source(path: Path, source: dict | None) -> None:
        if not source or not path.is_file():
            return
        import json
        import os
        value = json.loads(path.read_text(encoding="utf-8"))
        value["transcript_source"] = source
        partial = path.with_suffix(".json.part")
        partial.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(partial, path)

    def summary(self) -> dict[str, int]:
        return {"slots": self.slots, "occupied": len(self._reserved) + len(self._running)}

    async def close(self) -> None:
        if self._recovery is not None:
            self._recovery.cancel()
            await asyncio.gather(self._recovery, return_exceptions=True)
        running = list(self._running.values())
        if running:
            _, pending = await asyncio.wait(running, timeout=self.shutdown_grace_seconds)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        for sensitive in self._sensitive.values():
            sensitive.clear()
        self._sensitive.clear()
