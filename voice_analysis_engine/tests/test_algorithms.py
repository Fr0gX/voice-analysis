from __future__ import annotations

import math

import pytest

from voice_analysis_engine.assignment import assign_segments
from voice_analysis_engine.clustering import GoldWindowSpec, SpeakerAnchor, build_speakers, estimate_k_nme
from voice_analysis_engine.windows import score_windows
import voice_analysis_engine.clustering as clustering_module


def _vector(index: int, offset: float = 0.0) -> list[float]:
    values = [0.0] * 192
    values[index] = 1.0
    values[(index + 1) % 192] = offset
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


def test_window_score_matches_authoritative_tiers():
    scored, accepted, audit = score_windows([
        {
            "window_id": "clean",
            "start_ms": 0,
            "end_ms": 3000,
            "speech_ratio": 0.9,
            "overlap_ms": 0,
            "change_point_count": 0,
            "loudness_db": -20.0,
            "loudness_std_db": 4.0,
            "boundary_left_silence_ms": 100,
            "boundary_right_silence_ms": 100,
            "flags": [],
        },
        {
            "window_id": "rejected",
            "start_ms": 0,
            "end_ms": 1000,
            "speech_ratio": 0.5,
            "overlap_ms": 0,
            "change_point_count": 0,
            "loudness_db": -60.0,
            "loudness_std_db": 4.0,
            "flags": [],
        },
    ])
    assert scored[0]["score"] == 100.0
    assert scored[0]["tier"] == "clean"
    assert scored[1]["tier"] == "rejected"
    assert len(accepted) == 1
    assert audit["thresholds"]["clean_min_score"] == 80.0


def test_clustering_is_deterministic_and_builds_quantile_radii():
    specs = [
        GoldWindowSpec(f"w{i}", i * 2000, i * 2000 + 1800, "clean", 100.0, 1.0)
        for i in range(6)
    ]
    vectors = [_vector(0, 0.01 * i) for i in range(3)] + [_vector(10, 0.01 * i) for i in range(3)]
    first, first_audit = build_speakers(specs, vectors, dense_nme_max_bytes=64 * 1024 * 1024)
    second, second_audit = build_speakers(specs, vectors, dense_nme_max_bytes=64 * 1024 * 1024)
    assert [speaker.local_label for speaker in first] == [speaker.local_label for speaker in second]
    assert [speaker.source_window_ids for speaker in first] == [speaker.source_window_ids for speaker in second]
    assert first_audit["selected_k"] == second_audit["selected_k"]
    assert all(speaker.core_radius <= speaker.boundary_radius for speaker in first)


def test_sparse_and_dense_nme_select_same_k_on_overlap_fixture():
    vectors = []
    for cluster in (0, 10, 20):
        for offset in range(8):
            vector = [0.0] * 192
            vector[cluster] = 1.0
            vector[cluster + 1] = offset * 0.005
            norm = math.sqrt(sum(value * value for value in vector))
            vectors.append([value / norm for value in vector])
    weights = [1.0] * len(vectors)
    dense_k, _dense = estimate_k_nme(
        vectors, weights, min_k=1, max_k=6, dense_nme_max_bytes=1024 * 1024 * 1024
    )
    sparse_k, _sparse = estimate_k_nme(
        vectors, weights, min_k=1, max_k=6, dense_nme_max_bytes=1
    )
    assert sparse_k == dense_k


def test_sparse_nme_failure_never_materializes_dense_matrix(monkeypatch):
    vectors = [_vector(index % 3, index * 0.001) for index in range(12)]

    def fail_sparse_solver(*args, **kwargs):
        raise RuntimeError("forced eigensolver failure")

    monkeypatch.setattr(clustering_module, "eigsh", fail_sparse_solver)
    monkeypatch.setattr(
        clustering_module.sparse.csr_matrix,
        "toarray",
        lambda self: (_ for _ in ()).throw(AssertionError("dense materialization is forbidden")),
        raising=False,
    )
    with pytest.raises(RuntimeError, match="sparse NME eigensolver failed"):
        estimate_k_nme(vectors, [1.0] * len(vectors), min_k=1, max_k=6, dense_nme_max_bytes=1)


def test_strict_assignment_and_missing_embedding_unknown():
    speaker = SpeakerAnchor(
        local_label="local_spk_0",
        vector=_vector(0),
        medoid_vector=_vector(0),
        source_window_ids=["w0"],
        source_window_starts=[0],
        speech_ms=2000,
        sample_count=1,
        loudness_db=-20.0,
        intra_mean_dist=0.0,
        nearest_other_dist=None,
        margin=None,
        purity_score=1.0,
        core_radius=0.01,
        boundary_radius=0.02,
        accept_radius=0.02,
        concentration=1.0,
    )
    segments = [
        {"input_index": 0, "start_ms": 0, "end_ms": 2000, "text": "a"},
        {"input_index": 1, "start_ms": 2000, "end_ms": 4000, "text": "b"},
    ]
    assignments, audit = assign_segments(
        segments,
        [speaker],
        {0: _vector(0)},
        {0: {"speech_ratio": 1.0}, 1: {"speech_ratio": 1.0}},
    )
    assert assignments[0]["label"] == "local_spk_0"
    assert assignments[0]["source"] == "first_pass"
    assert assignments[1]["label"] == "unknown"
    assert assignments[1]["risk"]["flags"] == ["no_embedding"]
    assert audit["policy_version"] == "segment_assignment_v2"
