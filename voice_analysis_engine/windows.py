"""Time-only candidate discovery and authoritative speech-window scoring."""
from __future__ import annotations

from typing import Any


def discover_candidates(segments: list[dict[str, Any]], *, max_join_gap_ms: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Group only by time; ASR speaker values are deliberately never inspected."""
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_end = 0
    for segment in segments:
        start = int(segment["start_ms"])
        end = int(segment["end_ms"])
        if current and start - current_end > max_join_gap_ms:
            groups.append(current)
            current = []
        current.append(segment)
        current_end = max(current_end, end) if len(current) > 1 else end
    if current:
        groups.append(current)

    candidates: list[dict[str, Any]] = []
    for index, group in enumerate(groups):
        confidences = [float(item["confidence"]) for item in group if item.get("confidence") is not None]
        gaps: list[int] = []
        last_end: int | None = None
        for item in group:
            if last_end is not None:
                gaps.append(max(0, int(item["start_ms"]) - last_end))
            last_end = max(int(item["end_ms"]), last_end or int(item["end_ms"]))
        candidates.append({
            "candidate_id": f"asr_candidate_{index:03d}",
            "start_ms": int(group[0]["start_ms"]),
            "end_ms": max(int(item["end_ms"]) for item in group),
            "source_segment_indices": [int(item["input_index"]) for item in group],
            "segment_count": len(group),
            "max_internal_gap_ms": max(gaps) if gaps else 0,
            "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
            "text_char_count": sum(len(str(item.get("text") or "")) for item in group),
            "flags": [],
        })
    return candidates, {
        "stage": "asr_candidate_discovery",
        "method": "stable_time_only",
        "input_segment_count": len(segments),
        "candidate_count": len(candidates),
        "thresholds": {"max_join_gap_ms": int(max_join_gap_ms)},
        "asr_speaker_used": False,
    }


def score_windows(windows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    scored = [_score_one(dict(window), index) for index, window in enumerate(windows)]
    accepted = [row for row in scored if row["accepted"]]
    tier_counts: dict[str, int] = {}
    reject_counts: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    for row in scored:
        tier_counts[row["tier"]] = tier_counts.get(row["tier"], 0) + 1
        for reason in row["reject_reasons"]:
            reject_counts[reason] = reject_counts.get(reason, 0) + 1
        for flag in row["flags"]:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
    return scored, accepted, {
        "stage": "speech_window_candidate_scoring",
        "input_count": len(windows),
        "scored_count": len(scored),
        "accepted_count": len(accepted),
        "tier_counts": tier_counts,
        "reject_counts": reject_counts,
        "flag_counts": flag_counts,
        "thresholds": {
            "hard_min_duration_ms": 1200,
            "hard_min_speech_ratio": 0.65,
            "hard_min_loudness_db": -50.0,
            "accepted_min_score": 65.0,
            "clean_min_score": 80.0,
        },
    }


def _score_one(row: dict[str, Any], index: int) -> dict[str, Any]:
    duration = int(row.get("duration_ms") or max(0, int(row.get("end_ms") or 0) - int(row.get("start_ms") or 0)))
    speech_ratio = float(row.get("speech_ratio") or 0.0)
    overlap_ms = int(row.get("overlap_ms") or 0)
    change_count = int(row.get("change_point_count") or 0)
    loudness = float(row["loudness_db"]) if isinstance(row.get("loudness_db"), (int, float)) else None
    loudness_std = float(row.get("loudness_std_db") or 0.0)
    flags = [str(value) for value in row.get("flags") or []]
    penalties: list[dict[str, Any]] = []
    reasons: list[str] = []
    rejects: list[str] = []
    if duration < 1200:
        rejects.append("duration_too_short")
    if speech_ratio < 0.65:
        rejects.append("speech_ratio_too_low")
    if overlap_ms > 0 or "residual_overlap" in flags:
        rejects.append("overlap_detected")
    if change_count > 0 or "residual_speaker_change" in flags:
        rejects.append("speaker_change_detected")
    if loudness is None or loudness < -50.0:
        rejects.append("loudness_too_low")
    if duration >= 3000:
        reasons.append("duration_ok")
    elif duration >= 2000:
        penalties.append({"name": "duration_2000_3000ms", "value": 5.0})
    elif duration >= 1200:
        penalties.append({"name": "duration_1200_2000ms", "value": 15.0})
    if speech_ratio >= 0.85:
        reasons.append("speech_ratio_ok")
    elif speech_ratio >= 0.75:
        penalties.append({"name": "speech_ratio_075_085", "value": 8.0})
    elif speech_ratio >= 0.65:
        penalties.append({"name": "speech_ratio_065_075", "value": 18.0})
    if loudness_std <= 8.0:
        reasons.append("loudness_stability_ok")
    elif loudness_std <= 12.0:
        penalties.append({"name": "loudness_std_8_12db", "value": 8.0})
    elif loudness_std <= 16.0:
        penalties.append({"name": "loudness_std_12_16db", "value": 18.0})
    else:
        penalties.append({"name": "loudness_std_gt_16db", "value": 30.0})
    if int(row.get("boundary_left_silence_ms") or 0) < 80:
        penalties.append({"name": "left_boundary_tight", "value": 3.0})
    if int(row.get("boundary_right_silence_ms") or 0) < 80:
        penalties.append({"name": "right_boundary_tight", "value": 3.0})
    for flag, value in {
        "tiny_interval": 15,
        "low_speech_ratio": 18,
        "unstable_loudness": 10,
        "rms_vad_fallback_no_overlap_detection": 10,
    }.items():
        if flag in flags:
            penalties.append({"name": flag, "value": float(value)})
    score = max(0.0, 100.0 - sum(float(item["value"]) for item in penalties))
    if rejects:
        tier, accepted = "rejected", False
    elif score >= 80.0:
        tier, accepted = "clean", True
    elif score >= 65.0:
        tier, accepted = "usable", True
    elif score >= 50.0:
        tier, accepted = "weak", False
    else:
        tier, accepted = "rejected", False
    return {
        **row,
        "window_id": f"scored_speech_window_{index:03d}",
        "source_window_id": str(row.get("window_id") or f"speech_window_{index:03d}"),
        "duration_ms": duration,
        "score": round(score, 2),
        "tier": tier,
        "accepted": accepted,
        "speech_ratio": round(speech_ratio, 4),
        "overlap_ms": overlap_ms,
        "change_point_count": change_count,
        "loudness_db": loudness,
        "loudness_std_db": round(loudness_std, 4),
        "flags": flags,
        "score_reasons": reasons,
        "penalties": penalties,
        "reject_reasons": rejects,
    }
