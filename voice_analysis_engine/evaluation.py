"""M1 evaluation runner driven by config/evaluation.yaml."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.optimize import linear_sum_assignment

from .contracts import AnalysisRequest, SegmentDocument
from .engine import AnalysisEngine
from .errors import EngineError, input_error


async def evaluate_manifest(manifest_path: Path, report_path: Path, evaluation_config: Path) -> dict[str, Any]:
    profile_raw = evaluation_config.read_bytes()
    try:
        config = yaml.safe_load(profile_raw)
    except yaml.YAMLError as exc:
        raise input_error("EVALUATION_CONFIG_INVALID", f"invalid evaluation config: {exc}", "evaluation") from exc
    if not isinstance(config, dict) or int(config.get("schema_version") or 0) != 1:
        raise input_error("EVALUATION_CONFIG_INVALID", "evaluation config schema_version must be 1", "evaluation")
    records = _read_manifest(manifest_path)
    engine = AnalysisEngine()
    rows: list[dict[str, Any]] = []
    scenario_counts: dict[str, int] = {}
    source_recording_ids: set[str] = set()
    annotation_grades: set[str] = set()
    total_duration = 0
    for spec in records:
        for scenario in spec.get("scenarios") or []:
            scenario_counts[str(scenario)] = scenario_counts.get(str(scenario), 0) + 1
        result: dict[str, Any]
        try:
            reference_value = _load_json(_resolve(manifest_path, spec["reference_path"]))
            source_recording_id, annotation_grade = _effective_reference_provenance(spec, reference_value)
            source_recording_ids.add(source_recording_id)
            if annotation_grade:
                annotation_grades.add(annotation_grade)
            if spec.get("result_path"):
                result = _load_json(_resolve(manifest_path, spec["result_path"]))
            else:
                document = SegmentDocument.model_validate(_load_json(_resolve(manifest_path, spec["segments_path"])))
                analyzed = await engine.analyze(AnalysisRequest(
                    audio_path=_resolve(manifest_path, spec["audio_path"]),
                    document=document,
                    deadline_epoch_ms=None,
                ))
                result = analyzed.model_dump(mode="json")
            reference = _reference_segments(reference_value)
            metrics = score_recording(result, reference, config["scoring"])
            duration = int(result.get("audio", {}).get("duration_ms") or max((row["end_ms"] for row in reference), default=0))
            total_duration += duration
            rows.append({
                "id": str(spec["id"]),
                "status": "scored",
                "analysis_status": str(result.get("status") or "not_reported"),
                "duration_ms": duration,
                "scenarios": spec.get("scenarios") or [],
                "metrics": metrics,
            })
        except (EngineError, OSError, ValueError, KeyError) as exc:
            error = exc.code if isinstance(exc, EngineError) else type(exc).__name__
            rows.append({"id": str(spec.get("id") or "unknown"), "status": "failed", "error": error, "scenarios": spec.get("scenarios") or []})
    dataset = config["dataset"]
    missing_scenarios = {
        name: int(required) - scenario_counts.get(name, 0)
        for name, required in dataset["required_scenarios"].items()
        if scenario_counts.get(name, 0) < int(required)
    }
    eligibility_reasons: list[str] = []
    if len(source_recording_ids) < int(dataset["minimum_recordings"]):
        eligibility_reasons.append("insufficient_independent_recordings")
    weak_grades = sorted(grade for grade in annotation_grades if grade.startswith("weak_"))
    if weak_grades:
        eligibility_reasons.append("weak_reference_annotations")
    sufficient = (
        len(source_recording_ids) >= int(dataset["minimum_recordings"])
        and total_duration >= int(dataset["minimum_total_duration_ms"])
        and not missing_scenarios
        and not weak_grades
    )
    successful = [row["metrics"] for row in rows if row.get("status") == "scored"]
    aggregate = _macro(successful)
    gate_results = _gates(aggregate, config["gates"]) if aggregate else {}
    if not sufficient:
        status = str(config["reporting"]["insufficient_dataset_status"])
    elif len(successful) != len(records):
        status = "failed"
    else:
        status = "passed" if all(item["passed"] for item in gate_results.values()) else "failed"
    report = {
        "schema_version": 1,
        "status": status,
        "profile": config["profile"],
        "config_digest": hashlib.sha256(profile_raw).hexdigest(),
        "dataset": {
            "recording_count": len(records),
            "independent_recording_count": len(source_recording_ids),
            "scored_recording_count": len(successful),
            "total_duration_ms": total_duration,
            "scenario_counts": scenario_counts,
            "missing_scenarios": missing_scenarios,
            "annotation_grades": sorted(annotation_grades),
            "formal_gate_eligible": not eligibility_reasons,
            "eligibility_reasons": eligibility_reasons,
        },
        "metrics": aggregate,
        "gates": gate_results,
        "recordings": rows,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    partial = report_path.with_name(report_path.name + ".part")
    partial.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial.replace(report_path)
    return report


def score_recording(result: dict[str, Any], reference: list[dict[str, Any]], scoring: dict[str, Any]) -> dict[str, float]:
    frame_ms = int(scoring["frame_ms"])
    collar = int(scoring["collar_ms"])
    predictions = [
        {
            "start_ms": int(row["normalized"]["start_ms"]),
            "end_ms": int(row["normalized"]["end_ms"]),
            "speaker": str(row["assignment"]["label"]),
        }
        for row in result["segments"]
    ]
    ref_speakers = sorted({str(row["speaker"]) for row in reference})
    hyp_speakers = sorted({row["speaker"] for row in predictions if row["speaker"] != "unknown"})
    mapping = _optimal_mapping(reference, predictions, ref_speakers, hyp_speakers, frame_ms)
    duration = max(
        max((row["end_ms"] for row in reference), default=0),
        max((row["end_ms"] for row in predictions), default=0),
    )
    boundaries = [value for row in reference for value in (row["start_ms"], row["end_ms"])]
    miss = false_alarm = confusion = denominator = 0
    frames: list[tuple[set[str], set[str]]] = []
    for start in range(0, duration, frame_ms):
        center = start + frame_ms // 2
        if any(abs(center - boundary) <= collar for boundary in boundaries):
            continue
        refs = {str(row["speaker"]) for row in reference if row["start_ms"] <= center < row["end_ms"]}
        hyps = {
            mapping.get(row["speaker"], row["speaker"])
            for row in predictions
            if row["speaker"] != "unknown" and row["start_ms"] <= center < row["end_ms"]
        }
        frames.append((refs, hyps))
        denominator += len(refs)
        matched = len(refs & hyps)
        miss += max(0, len(refs) - len(hyps))
        false_alarm += max(0, len(hyps) - len(refs))
        confusion += max(0, min(len(refs), len(hyps)) - matched)
    der = (miss + false_alarm + confusion) / max(1, denominator)
    jer_values: list[float] = []
    for speaker in ref_speakers:
        ref_frames = {index for index, (refs, _hyps) in enumerate(frames) if speaker in refs}
        hyp_frames = {index for index, (_refs, hyps) in enumerate(frames) if speaker in hyps}
        union = len(ref_frames | hyp_frames)
        jer_values.append(1.0 - len(ref_frames & hyp_frames) / max(1, union))
    expected_count = len(ref_speakers)
    predicted_count = len(hyp_speakers)
    assigned = [row for row in predictions if row["speaker"] != "unknown"]
    correct = 0
    for row in assigned:
        truth = _dominant_reference(reference, row["start_ms"], row["end_ms"])
        if truth is not None and mapping.get(row["speaker"], row["speaker"]) == truth:
            correct += 1
    segment_count = len(predictions)
    return {
        "der": round(der, 6),
        "jer": round(sum(jer_values) / max(1, len(jer_values)), 6),
        "speaker_count_exact_accuracy": 1.0 if expected_count == predicted_count else 0.0,
        "speaker_count_mae": float(abs(expected_count - predicted_count)),
        "assigned_segment_accuracy": round(correct / max(1, len(assigned)), 6),
        "non_unknown_coverage": round(len(assigned) / max(1, segment_count), 6),
        "unknown_rate": round((segment_count - len(assigned)) / max(1, segment_count), 6),
    }


def _optimal_mapping(reference, predictions, refs, hyps, frame_ms):
    if not refs or not hyps:
        return {}
    matrix = np.zeros((len(hyps), len(refs)), dtype=np.float64)
    duration = max(max(row["end_ms"] for row in reference), max(row["end_ms"] for row in predictions))
    for center in range(frame_ms // 2, duration, frame_ms):
        active_refs = {str(row["speaker"]) for row in reference if row["start_ms"] <= center < row["end_ms"]}
        active_hyps = {row["speaker"] for row in predictions if row["speaker"] != "unknown" and row["start_ms"] <= center < row["end_ms"]}
        for hyp in active_hyps:
            for ref in active_refs:
                matrix[hyps.index(hyp), refs.index(ref)] += 1
    hyp_indices, ref_indices = linear_sum_assignment(-matrix)
    return {hyps[hyp]: refs[ref] for hyp, ref in zip(hyp_indices, ref_indices)}


def _dominant_reference(reference, start, end):
    overlap: dict[str, int] = {}
    for row in reference:
        value = max(0, min(end, row["end_ms"]) - max(start, row["start_ms"]))
        overlap[str(row["speaker"])] = overlap.get(str(row["speaker"]), 0) + value
    return max(overlap, key=lambda key: (overlap[key], key)) if overlap and max(overlap.values()) > 0 else None


def _macro(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {}
    return {key: round(sum(row[key] for row in rows) / len(rows), 6) for key in rows[0]}


def _gates(metrics: dict[str, float], gates: dict[str, Any]) -> dict[str, Any]:
    definitions = {
        "der": ("der_max", "max"),
        "jer": ("jer_max", "max"),
        "speaker_count_exact_accuracy": ("speaker_count_exact_accuracy_min", "min"),
        "speaker_count_mae": ("speaker_count_mae_max", "max"),
        "assigned_segment_accuracy": ("assigned_segment_accuracy_min", "min"),
        "non_unknown_coverage": ("non_unknown_coverage_min", "min"),
        "unknown_rate": ("unknown_rate_max", "max"),
    }
    result = {}
    for metric, (gate_key, direction) in definitions.items():
        actual, threshold = float(metrics[metric]), float(gates[gate_key])
        result[metric] = {
            "actual": actual,
            "threshold": threshold,
            "operator": "<=" if direction == "max" else ">=",
            "passed": actual <= threshold if direction == "max" else actual >= threshold,
        }
    return result


def _read_manifest(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or not value.get("id") or not value.get("reference_path"):
            raise input_error("EVALUATION_MANIFEST_INVALID", f"invalid manifest row {line_number}", "evaluation")
        rows.append(value)
    return rows


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(manifest: Path, value: str) -> Path:
    root = manifest.resolve().parent
    path = Path(str(value))
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        raise input_error("EVALUATION_PATH_OUTSIDE_ROOT", "evaluation path must stay inside the manifest directory", "evaluation")
    return resolved


def _effective_reference_provenance(spec: dict[str, Any], reference_value: Any) -> tuple[str, str | None]:
    reference_grade = None
    reference_source = None
    if isinstance(reference_value, dict):
        if reference_value.get("annotation_grade") is not None:
            reference_grade = str(reference_value["annotation_grade"])
        provenance = reference_value.get("provenance")
        if isinstance(provenance, dict) and provenance.get("source_recording_id") is not None:
            reference_source = str(provenance["source_recording_id"])
    manifest_grade = str(spec["annotation_grade"]) if spec.get("annotation_grade") is not None else None
    manifest_source = str(spec["source_recording_id"]) if spec.get("source_recording_id") is not None else None
    if manifest_grade and reference_grade and manifest_grade != reference_grade:
        raise input_error("EVALUATION_PROVENANCE_MISMATCH", "manifest and reference annotation grades differ", "evaluation")
    if manifest_source and reference_source and manifest_source != reference_source:
        raise input_error("EVALUATION_PROVENANCE_MISMATCH", "manifest and reference source recording ids differ", "evaluation")
    return manifest_source or reference_source or str(spec["id"]), manifest_grade or reference_grade


def _reference_segments(value: Any) -> list[dict[str, Any]]:
    rows = value.get("segments") if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise ValueError("reference must be an array or contain segments")
    return [
        {"start_ms": int(row["start_ms"]), "end_ms": int(row["end_ms"]), "speaker": str(row["speaker"])}
        for row in rows
    ]
