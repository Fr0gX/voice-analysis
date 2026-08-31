"""Single-owner, bounded, cross-request ECAPA batch scheduler."""
from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
import logging
from typing import Any, Protocol

import numpy as np

from .config import ServiceConfig

logger = logging.getLogger(__name__)


class BatchBackend(Protocol):
    @property
    def available(self) -> bool: ...
    def extract_batch(
        self, audio_batch: list[np.ndarray], sample_rate: int
    ) -> tuple[np.ndarray, dict[str, Any]]: ...


class QueueOverloaded(RuntimeError):
    pass


@dataclass(frozen=True)
class InferenceWindow:
    window_id: str
    audio: np.ndarray


@dataclass
class InferenceRequest:
    request_id: str
    windows: list[InferenceWindow]
    pcm_bytes: int
    deadline_ms: int
    future: Future = field(default_factory=Future)


class InferenceScheduler:
    def __init__(self, backend: BatchBackend, cfg: ServiceConfig) -> None:
        self.backend = backend
        self.cfg = cfg
        self._queue: queue.Queue[InferenceRequest | None] = queue.Queue(cfg.max_queue_requests)
        self._lock = threading.Lock()
        self._pending_bytes = 0
        self._thread: threading.Thread | None = None
        self._stopping = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="voiceprint-model-owner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=5)

    def submit(self, request: InferenceRequest) -> Future:
        with self._lock:
            if self._stopping:
                raise RuntimeError("voiceprint scheduler is stopping")
            if self._queue.full() or self._pending_bytes + request.pcm_bytes > self.cfg.max_queue_bytes:
                raise QueueOverloaded("voiceprint inference queue is full")
            self._pending_bytes += request.pcm_bytes
            try:
                self._queue.put_nowait(request)
            except queue.Full as exc:
                self._pending_bytes -= request.pcm_bytes
                raise QueueOverloaded("voiceprint inference queue is full") from exc
        return request.future

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"queued_requests": self._queue.qsize(), "queued_pcm_bytes": self._pending_bytes}

    def _release(self, request: InferenceRequest) -> None:
        with self._lock:
            self._pending_bytes = max(0, self._pending_bytes - request.pcm_bytes)

    def _run(self) -> None:
        while not self._stopping:
            first = self._queue.get()
            if first is None:
                return
            requests = [first]
            cutoff = time.monotonic() + self.cfg.microbatch_wait_ms / 1000.0
            while len(requests) < self.cfg.max_queue_requests:
                remaining = cutoff - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = self._queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if item is None:
                    self._stopping = True
                    break
                requests.append(item)
            try:
                self._process(requests)
            except Exception as exc:  # noqa: BLE001
                logger.exception("voiceprint scheduler request group failed")
                for request in requests:
                    if not request.future.done():
                        request.future.set_exception(RuntimeError(str(exc)))
            finally:
                for request in requests:
                    self._release(request)

    def _process(self, requests: list[InferenceRequest]) -> None:
        now_ms = int(time.time() * 1000)
        # Concurrent retries may legitimately reuse a task request_id. Keep
        # scheduling state isolated by request object rather than by that
        # externally supplied identifier.
        results: dict[int, dict[str, dict[str, Any]]] = {id(req): {} for req in requests}
        active: list[tuple[InferenceRequest, InferenceWindow]] = []
        for request in requests:
            if request.future.cancelled():
                continue
            if now_ms >= request.deadline_ms:
                if not request.future.done():
                    request.future.set_result({"deadline_exceeded": True, "items": {}})
                continue
            for window in request.windows:
                active.append((request, window))

        # Length buckets reduce padding while stable indexes retain exact mapping.
        active.sort(key=lambda pair: len(pair[1].audio))
        pos = 0
        max_samples = int(self.cfg.max_batch_audio_seconds * self.cfg.sample_rate)
        while pos < len(active):
            batch: list[tuple[InferenceRequest, InferenceWindow]] = []
            samples = 0
            while pos < len(active) and len(batch) < self.cfg.max_batch_windows:
                candidate = active[pos]
                candidate_samples = len(candidate[1].audio)
                if batch and samples + candidate_samples > max_samples:
                    break
                batch.append(candidate)
                samples += candidate_samples
                pos += 1
            self._infer_batch(batch, results)

        finished_ms = int(time.time() * 1000)
        for request in requests:
            if request.future.done():
                continue
            if finished_ms >= request.deadline_ms:
                request.future.set_result({"deadline_exceeded": True, "items": results[id(request)]})
            else:
                request.future.set_result({"deadline_exceeded": False, "items": results[id(request)]})

    def _infer_batch(
        self,
        batch: list[tuple[InferenceRequest, InferenceWindow]],
        results: dict[int, dict[str, dict[str, Any]]],
    ) -> None:
        """Run a batch, bisecting only on failure to isolate a bad window."""
        batch = [
            item
            for item in batch
            if not item[0].future.cancelled()
            and int(time.time() * 1000) < item[0].deadline_ms
        ]
        if not batch:
            return
        try:
            matrix, _meta = self.backend.extract_batch(
                [item.audio for _, item in batch], self.cfg.sample_rate
            )
            if len(matrix) != len(batch):
                raise RuntimeError("batch embedding count mismatch")
            for (request, window), vector in zip(batch, matrix):
                results[id(request)][window.window_id] = {
                    "status": "success",
                    "embedding": [float(value) for value in vector],
                }
        except Exception as exc:  # noqa: BLE001
            if len(batch) > 1:
                midpoint = len(batch) // 2
                self._infer_batch(batch[:midpoint], results)
                self._infer_batch(batch[midpoint:], results)
                return
            request, window = batch[0]
            results[id(request)][window.window_id] = {
                "status": "inference_failed",
                "error": str(exc)[:300],
            }
