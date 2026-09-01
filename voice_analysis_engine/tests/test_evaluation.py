from __future__ import annotations

import asyncio
import json

from voice_analysis_engine.evaluation import evaluate_manifest


def test_small_manifest_is_insufficient_dataset(tmp_path):
    result = {
        "audio": {"duration_ms": 2000},
        "segments": [{
            "normalized": {"start_ms": 0, "end_ms": 2000},
            "assignment": {"label": "local_spk_0"},
        }],
    }
    reference = {"segments": [{"start_ms": 0, "end_ms": 2000, "speaker": "speaker_a"}]}
    (tmp_path / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (tmp_path / "reference.json").write_text(json.dumps(reference), encoding="utf-8")
    (tmp_path / "manifest.jsonl").write_text(json.dumps({
        "id": "one",
        "result_path": "result.json",
        "reference_path": "reference.json",
        "scenarios": ["single_speaker"],
    }) + "\n", encoding="utf-8")
    config = __import__("pathlib").Path(__file__).resolve().parents[2] / "config" / "evaluation.yaml"
    report = asyncio.run(evaluate_manifest(tmp_path / "manifest.jsonl", tmp_path / "report.json", config))
    assert report["status"] == "insufficient_dataset"
    assert report["metrics"]["der"] == 0.0
    assert report["recordings"][0]["status"] == "scored"
    assert report["recordings"][0]["analysis_status"] == "not_reported"
    assert report["gates"]["der"] == {
        "actual": 0.0,
        "threshold": 0.3,
        "operator": "<=",
        "passed": True,
    }


def test_derived_weak_samples_do_not_count_as_independent_formal_recordings(tmp_path):
    result = {
        "audio": {"duration_ms": 3_600_000},
        "segments": [{
            "normalized": {"start_ms": 0, "end_ms": 1000},
            "assignment": {"label": "local_spk_0"},
        }],
    }
    reference = {"segments": [{"start_ms": 0, "end_ms": 1000, "speaker": "speaker_a"}]}
    (tmp_path / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (tmp_path / "reference.json").write_text(json.dumps(reference), encoding="utf-8")
    scenarios = ["single_speaker", "two_speaker", "multi_speaker", "overlap", "noisy_or_far_field"]
    rows = [
        json.dumps({
            "id": f"derived-{index}",
            "source_recording_id": f"source-{index % 12}",
            "annotation_grade": "weak_automatic_reviewed",
            "result_path": "result.json",
            "reference_path": "reference.json",
            "scenarios": scenarios,
        })
        for index in range(20)
    ]
    (tmp_path / "manifest.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    config = __import__("pathlib").Path(__file__).resolve().parents[2] / "config" / "evaluation.yaml"

    report = asyncio.run(evaluate_manifest(tmp_path / "manifest.jsonl", tmp_path / "report.json", config))

    assert report["status"] == "insufficient_dataset"
    assert report["dataset"]["recording_count"] == 20
    assert report["dataset"]["independent_recording_count"] == 12
    assert report["dataset"]["formal_gate_eligible"] is False
    assert report["gates"]
    assert report["dataset"]["eligibility_reasons"] == [
        "insufficient_independent_recordings",
        "weak_reference_annotations",
    ]


def test_reference_weak_grade_applies_when_manifest_omits_it(tmp_path):
    result = {
        "audio": {"duration_ms": 3_600_000},
        "segments": [{"normalized": {"start_ms": 0, "end_ms": 1000}, "assignment": {"label": "local_spk_0"}}],
    }
    reference = {
        "annotation_grade": "weak_automatic_reviewed",
        "provenance": {"source_recording_id": "shared-source"},
        "segments": [{"start_ms": 0, "end_ms": 1000, "speaker": "speaker_a"}],
    }
    (tmp_path / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (tmp_path / "reference.json").write_text(json.dumps(reference), encoding="utf-8")
    rows = [json.dumps({
        "id": f"derived-{index}",
        "result_path": "result.json",
        "reference_path": "reference.json",
        "scenarios": ["single_speaker", "two_speaker", "multi_speaker", "overlap", "noisy_or_far_field"],
    }) for index in range(20)]
    (tmp_path / "manifest.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    config = __import__("pathlib").Path(__file__).resolve().parents[2] / "config" / "evaluation.yaml"

    report = asyncio.run(evaluate_manifest(tmp_path / "manifest.jsonl", tmp_path / "report.json", config))

    assert report["dataset"]["independent_recording_count"] == 1
    assert report["dataset"]["annotation_grades"] == ["weak_automatic_reviewed"]
    assert report["status"] == "insufficient_dataset"


def test_manifest_cannot_read_reference_outside_dataset_root(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (tmp_path / "outside-reference.json").write_text(
        json.dumps({"segments": [{"start_ms": 0, "end_ms": 1000, "speaker": "speaker_a"}]}),
        encoding="utf-8",
    )
    (dataset / "manifest.jsonl").write_text(json.dumps({
        "id": "outside",
        "result_path": "result.json",
        "reference_path": "../outside-reference.json",
        "scenarios": [],
    }) + "\n", encoding="utf-8")
    config = __import__("pathlib").Path(__file__).resolve().parents[2] / "config" / "evaluation.yaml"

    report = asyncio.run(evaluate_manifest(dataset / "manifest.jsonl", dataset / "report.json", config))

    assert report["recordings"][0]["status"] == "failed"
    assert report["recordings"][0]["error"] == "EVALUATION_PATH_OUTSIDE_ROOT"
