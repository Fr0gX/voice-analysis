import pytest

from voice_analysis_engine.recording2_dataset import (
    _assert_runtime_evaluation_target,
    _scenarios,
    choose_boundaries,
    normalize_speaker,
    parse_transcript,
)


def test_transcript_parser_repairs_seconds_and_preserves_provenance(tmp_path):
    transcript = tmp_path / "sample.md"
    transcript.write_text(
        "# sample\n\n"
        "- 转写段数：2\n\n"
        "**[00:00:01–00:00:01] 顾客 · speaker_SPEAKER_00_overlap_overlap**\n\n"
        "第一句\n\n"
        "**[00:00:03–00:00:05] 医生 · speaker_SPEAKER_01**\n\n"
        "第二句\n",
        encoding="utf-8",
    )

    rows, audit = parse_transcript(transcript, audio_duration_ms=10_000)

    assert len(rows) == 2
    assert rows[0].end_ms == 2000
    assert rows[0].reference_speaker == "speaker_SPEAKER_00"
    assert rows[0].raw_speaker == "speaker_SPEAKER_00_overlap_overlap"
    assert rows[0].text == "第一句"
    assert audit["declared_count_matches"] is True
    assert audit["zero_duration_repair_count"] == 1
    assert audit["overlap_suffix_count"] == 1


def test_boundaries_are_stable_and_cover_whole_audio(tmp_path):
    transcript = tmp_path / "sample.md"
    transcript.write_text(
        "- 转写段数：4\n\n" + "\n\n".join([
            f"**[00:00:{start:02d}–00:00:{end:02d}] 角色 · speaker_SPEAKER_00**\n\nline-{index}"
            for index, (start, end) in enumerate([(1, 5), (20, 25), (40, 45), (55, 59)])
        ]),
        encoding="utf-8",
    )
    rows, _audit = parse_transcript(transcript, audio_duration_ms=60_000)

    boundaries = choose_boundaries(rows, 60_000, 3)

    assert boundaries == [0, 20_000, 40_000, 60_000]
    assert normalize_speaker("speaker_SPEAKER_01_overlap_overlap") == "speaker_SPEAKER_01"


def test_out_of_bounds_annotation_is_reported_as_unusable(tmp_path):
    transcript = tmp_path / "sample.md"
    transcript.write_text(
        "- 转写段数：1\n\n"
        "**[00:00:11–00:00:12] 角色 · speaker_SPEAKER_00**\n\nlate\n",
        encoding="utf-8",
    )

    rows, audit = parse_transcript(transcript, audio_duration_ms=10_000)

    assert rows == []
    assert audit["declared_count_matches"] is True
    assert audit["usable_count_matches_declared"] is False
    assert audit["dropped_start_out_of_bounds_count"] == 1


def test_scenario_labels_are_explicit_weak_proxies():
    references = [
        {"start_ms": 0, "end_ms": 2000, "speaker": "speaker_0", "semantic_role": "现场人员", "annotation_flags": []},
        {"start_ms": 1000, "end_ms": 3000, "speaker": "speaker_1", "semantic_role": "顾客", "annotation_flags": ["overlap_suffix_normalized"]},
    ]

    scenarios = _scenarios(references, ["speaker_0", "speaker_1"], ["现场人员", "顾客"])

    assert scenarios == ["two_speaker", "overlap", "noisy_or_far_field"]


def test_force_replacement_rejects_path_outside_runtime_evaluation(tmp_path):
    with pytest.raises(ValueError, match="refusing to replace"):
        _assert_runtime_evaluation_target(tmp_path / "dataset")
