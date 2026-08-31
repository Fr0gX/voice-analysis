from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from voice_embedding_service.config import ServiceConfig
from voice_embedding_service.scheduler import (
    InferenceRequest,
    InferenceScheduler,
    InferenceWindow,
    QueueOverloaded,
)


class _BatchBackend:
    available = True
    loaded = True

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def extract_batch(self, audio_batch, sample_rate):
        self.batch_sizes.append(len(audio_batch))
        rows = []
        for index, _audio in enumerate(audio_batch):
            vector = np.zeros(192, dtype=np.float32)
            vector[index] = 1.0
            rows.append(vector)
        return np.stack(rows), {}


def _request(request_id: str, count: int = 1, deadline_delta_ms: int = 10_000):
    windows = [
        InferenceWindow(f"{request_id}:{index}", np.ones(8_000 + index, dtype=np.float32))
        for index in range(count)
    ]
    return InferenceRequest(
        request_id=request_id,
        windows=windows,
        pcm_bytes=sum(len(window.audio) * 2 for window in windows),
        deadline_ms=int(time.time() * 1000) + deadline_delta_ms,
    )


def test_scheduler_uses_real_multi_window_batch_and_maps_items() -> None:
    backend = _BatchBackend()
    scheduler = InferenceScheduler(backend, ServiceConfig(microbatch_wait_ms=1))
    scheduler.start()
    try:
        result = scheduler.submit(_request("r", count=3)).result(timeout=2)
    finally:
        scheduler.stop()
    assert backend.batch_sizes == [3]
    assert list(result["items"]) == ["r:0", "r:1", "r:2"]
    assert all(item["status"] == "success" for item in result["items"].values())


def test_expired_request_never_calls_model() -> None:
    backend = _BatchBackend()
    scheduler = InferenceScheduler(backend, ServiceConfig(microbatch_wait_ms=1))
    scheduler.start()
    try:
        result = scheduler.submit(_request("expired", deadline_delta_ms=-1)).result(timeout=2)
    finally:
        scheduler.stop()
    assert result["deadline_exceeded"] is True
    assert backend.batch_sizes == []


def test_queue_is_bounded_by_request_count() -> None:
    entered = threading.Event()
    release = threading.Event()

    class _BlockingBackend(_BatchBackend):
        def extract_batch(self, audio_batch, sample_rate):
            entered.set()
            release.wait(timeout=2)
            return super().extract_batch(audio_batch, sample_rate)

    scheduler = InferenceScheduler(
        _BlockingBackend(), ServiceConfig(max_queue_requests=1, microbatch_wait_ms=0)
    )
    scheduler.start()
    try:
        first = scheduler.submit(_request("first"))
        assert entered.wait(timeout=1)
        second = scheduler.submit(_request("second"))
        with pytest.raises(QueueOverloaded):
            scheduler.submit(_request("third"))
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)
    finally:
        release.set()
        scheduler.stop()


def test_queue_is_bounded_by_pcm_bytes() -> None:
    scheduler = InferenceScheduler(
        _BatchBackend(), ServiceConfig(max_queue_bytes=100, microbatch_wait_ms=0)
    )
    request = _request("too-large")
    assert request.pcm_bytes > 100
    with pytest.raises(QueueOverloaded):
        scheduler.submit(request)


def test_same_request_ids_do_not_share_results() -> None:
    backend = _BatchBackend()
    scheduler = InferenceScheduler(backend, ServiceConfig(microbatch_wait_ms=10))
    scheduler.start()
    try:
        first = scheduler.submit(_request("same", count=1))
        second = scheduler.submit(_request("same", count=2))
        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)
    finally:
        scheduler.stop()
    assert list(first_result["items"]) == ["same:0"]
    assert list(second_result["items"]) == ["same:0", "same:1"]


def test_batch_failure_isolated_to_bad_window() -> None:
    class _OneBadWindowBackend(_BatchBackend):
        def extract_batch(self, audio_batch, sample_rate):
            if any(len(audio) == 8_001 for audio in audio_batch):
                raise RuntimeError("bad window")
            return super().extract_batch(audio_batch, sample_rate)

    scheduler = InferenceScheduler(
        _OneBadWindowBackend(), ServiceConfig(microbatch_wait_ms=0)
    )
    scheduler.start()
    try:
        result = scheduler.submit(_request("r", count=3)).result(timeout=2)
    finally:
        scheduler.stop()
    assert result["items"]["r:0"]["status"] == "success"
    assert result["items"]["r:1"]["status"] == "inference_failed"
    assert result["items"]["r:2"]["status"] == "success"
