from voice_analysis_engine.adapters import _PackedWindow, plan_embedding_batches


def _window(index: int, duration_ms: int) -> _PackedWindow:
    start_ms = index * 100_000
    return _PackedWindow(
        window_id=f"window-{index}",
        start_ms=start_ms,
        end_ms=start_ms + duration_ms,
        kind="segment",
    )


def test_embedding_batch_plan_respects_all_protocol_limits():
    limits = {
        "batch_windows": 16,
        "batch_audio_seconds": 60,
        "maximum_request_bytes": 32 * 1024 * 1024,
    }
    windows = [_window(index, duration_ms) for index, duration_ms in enumerate([30_000] * 17 + [1_000] * 20)]

    batches = plan_embedding_batches(windows, limits)

    assert [item.window_id for batch in batches for item in batch] == [item.window_id for item in windows]
    assert all(len(batch) <= 16 for batch in batches)
    assert all(sum(item.duration_ms for item in batch) <= 60_000 for batch in batches)
    assert all(sum(item.duration_ms * 16_000 * 2 // 1000 for item in batch) <= 32 * 1024 * 1024 for batch in batches)


def test_embedding_batch_plan_splits_at_pcm_byte_limit():
    limits = {
        "batch_windows": 16,
        "batch_audio_seconds": 60,
        "maximum_request_bytes": 640_000,
    }
    windows = [_window(index, 10_000) for index in range(5)]

    batches = plan_embedding_batches(windows, limits)

    assert [len(batch) for batch in batches] == [2, 2, 1]
    assert plan_embedding_batches([], limits) == []
