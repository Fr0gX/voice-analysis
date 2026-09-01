from __future__ import annotations

from types import MethodType

from window_refine_service.config import ServiceConfig
from window_refine_service.pyannote_backend import PyannoteWindowRefineBackend, SegmentationFrame


def test_candidate_inference_failure_is_isolated(monkeypatch):
    backend = PyannoteWindowRefineBackend(ServiceConfig())
    backend.loaded = True

    def timeline(self, _path, _duration, candidates):
        if candidates[0]["candidate_id"] == "bad":
            raise RuntimeError("synthetic failure")
        return [SegmentationFrame(0, 1000, 1, "speaker_0", 1.0)], [{"core_start_ms": 0}], 10000

    backend._timeline_path = MethodType(timeline, backend)
    monkeypatch.setattr("window_refine_service.pyannote_backend.audio_duration_ms", lambda _path: 2000)
    monkeypatch.setattr(
        "window_refine_service.pyannote_backend._split_candidates",
        lambda _path, candidates, _frames, **_kwargs: ([{
            "window_id": "speech_window_000",
            "source_candidate_id": candidates[0]["candidate_id"],
        }], [], {}),
    )

    windows, audit = backend.segment(
        audio_path="ignored.wav",
        asr_candidate_windows=[
            {"candidate_id": "good", "start_ms": 0, "end_ms": 1000},
            {"candidate_id": "bad", "start_ms": 1000, "end_ms": 2000},
        ],
        profile={},
        speech_db_threshold=-45.0,
    )

    assert len(windows) == 1
    assert audit["candidate_results"] == [
        {"candidate_id": "good", "status": "success", "window_count": 1},
        {"candidate_id": "bad", "status": "inference_failed", "window_count": 0, "error": "RuntimeError"},
    ]
