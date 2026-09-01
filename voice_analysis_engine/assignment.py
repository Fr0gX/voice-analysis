"""Authoritative segment-to-speaker assignment and risk-aware recall."""
from __future__ import annotations

from typing import Any

from .clustering import SpeakerAnchor, cosine, valid_vector


def assign_segments(
    segments: list[dict[str, Any]],
    speakers: list[SpeakerAnchor],
    embeddings: dict[int, list[float]],
    qualities: dict[int, dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for segment in segments:
        index = int(segment["input_index"])
        row: dict[str, Any] = {
            "segment_index": index,
            "start_ms": int(segment["start_ms"]),
            "end_ms": int(segment["end_ms"]),
            "duration_ms": max(1, int(segment["end_ms"]) - int(segment["start_ms"])),
            "text": str(segment.get("text") or ""),
            "quality": dict(qualities.get(index) or {}),
        }
        vector = valid_vector(embeddings.get(index))
        if vector is None:
            row.update(_unknown(row, "missing_segment_embedding"))
            rows.append(row)
            continue
        if not speakers:
            row.update(_unknown(row, "no_gold_window_speakers"))
            rows.append(row)
            continue
        scored = sorted((_score_anchor(vector, speaker) for speaker in speakers), key=lambda item: item["distance"])
        best = scored[0]
        second_distance = float(scored[1]["distance"]) if len(scored) > 1 else 1.0
        best_distance = float(best["distance"])
        margin = max(0.0, second_distance - best_distance)
        relative_margin = margin / max(best_distance, 0.001)
        level, reason = "unknown", "outside_accept_radius"
        if best_distance <= best["core_radius"] + 1e-9 and relative_margin >= 0.35:
            level, reason = "strong", "inside_core_with_clear_margin"
        elif best_distance <= best["boundary_radius"] + 1e-9 and relative_margin >= 0.20:
            level, reason = "normal", "inside_boundary_with_clear_margin"
        elif best_distance <= best["accept_radius"] + 1e-9 and relative_margin >= 0.12:
            level, reason = "tentative", "inside_accept_radius_tentative"
        elif relative_margin < 0.12:
            reason = "margin_too_low"
        elif best_distance <= best["accept_radius"] + 1e-9:
            reason = "inside_radius_but_margin_too_low"
        assigned = level in {"strong", "normal"}
        row.update({
            "assigned": assigned,
            "label": best["label"] if assigned else "unknown",
            "candidate_speaker": best["label"],
            "assignment_level": level,
            "strict_assignment_level": level,
            "confidence": _strict_confidence(level, relative_margin),
            "reason": f"segment_embedding_{level}" if assigned else reason,
            "strict_reason": f"segment_embedding_{level}" if assigned else reason,
            "best_distance": round(best_distance, 6),
            "second_distance": round(second_distance, 6),
            "margin": round(margin, 6),
            "relative_margin": round(relative_margin, 6),
            "center_distance": round(float(best["center_distance"]), 6),
            "medoid_distance": round(float(best["medoid_distance"]), 6),
            "speaker_core_radius": round(float(best["core_radius"]), 6),
            "speaker_boundary_radius": round(float(best["boundary_radius"]), 6),
            "speaker_accept_radius": round(float(best["accept_radius"]), 6),
            "speaker_margin": best["speaker_margin"],
            "speaker_purity_score": best["speaker_purity_score"],
            "speaker_concentration": best["speaker_concentration"],
            "speaker_is_reliable": True,
            "speaker_quality_flags": list(best["speaker_quality_flags"]),
        })
        if assigned:
            row["risk_flags"] = []
            row["assignment"] = _build_assignment(
                row,
                label=best["label"],
                source="first_pass",
                level=level,
                confidence=row["confidence"],
                risk_score=0.05 if level == "strong" else 0.16,
                risk_level="low",
                reason=row["reason"],
                risk_flags=[],
            )
        else:
            row.update(_unknown(row, reason))
        rows.append(row)
    recall_audit = _recall(rows)
    assignments = {int(row["segment_index"]): dict(row["assignment"]) for row in rows}
    level_counts: dict[str, int] = {}
    for row in rows:
        level = str(row.get("assignment_level") or "unknown")
        level_counts[level] = level_counts.get(level, 0) + 1
    return assignments, {
        "stage": "segment_speaker_assignment",
        "method": "segment_embedding_distance",
        "policy_version": "segment_assignment_v2",
        "segment_count": len(rows),
        "assigned_count": sum(1 for row in rows if row.get("assigned")),
        "unknown_count": sum(1 for row in rows if not row.get("assigned")),
        "level_counts": level_counts,
        "recall_enabled": True,
        "recalled_count": int(recall_audit["recalled_count"]),
        "thresholds": {
            "strong_relative_margin": 0.35,
            "normal_relative_margin": 0.20,
            "tentative_relative_margin": 0.12,
            "medoid_penalty": 0.02,
        },
        "recall": recall_audit,
        "segments": rows,
    }


def _score_anchor(vector: list[float], speaker: SpeakerAnchor) -> dict[str, Any]:
    center_distance = 1.0 - cosine(vector, speaker.vector)
    medoid_distance = 1.0 - cosine(vector, speaker.medoid_vector)
    return {
        "label": speaker.local_label,
        "distance": min(center_distance, medoid_distance + 0.02),
        "center_distance": center_distance,
        "medoid_distance": medoid_distance,
        "core_radius": speaker.core_radius,
        "boundary_radius": max(speaker.core_radius, speaker.boundary_radius),
        "accept_radius": max(speaker.core_radius, speaker.boundary_radius, speaker.accept_radius),
        "speaker_margin": speaker.margin,
        "speaker_purity_score": speaker.purity_score,
        "speaker_concentration": speaker.concentration,
        "speaker_quality_flags": speaker.quality_flags,
    }


def _recall(rows: list[dict[str, Any]]) -> dict[str, Any]:
    audit_rows: list[dict[str, Any]] = []
    for row in rows:
        if row.get("assigned") or row.get("label") != "unknown":
            continue
        audit = {
            "segment_index": row["segment_index"],
            "candidate_speaker": row.get("candidate_speaker"),
            "duration_ms": row["duration_ms"],
            "best_distance": row.get("best_distance"),
            "margin": row.get("margin"),
            "relative_margin": row.get("relative_margin"),
            "original_reason": row.get("strict_reason") or row.get("reason"),
            "quality": row.get("quality") or {},
            "recalled": False,
        }
        if not row.get("candidate_speaker"):
            audit["reject_reason"] = "no_candidate_speaker"
            audit_rows.append(audit)
            continue
        quality_band, quality_reason = _quality_band(row["quality"], row["duration_ms"])
        audit["quality_band"] = quality_band
        if quality_band == "poor":
            audit["reject_reason"] = quality_reason
            audit_rows.append(audit)
            continue
        if float(row.get("best_distance") or 1.0) > 0.70:
            audit["reject_reason"] = "distance_over_hard_cap"
            audit_rows.append(audit)
            continue
        decision = _recall_decision(row, quality_band)
        if not decision["recall"]:
            audit["reject_reason"] = decision["reject_reason"]
            audit["risk_flags"] = decision.get("risk_flags", [])
            audit_rows.append(audit)
            continue
        flags = list(decision.get("risk_flags") or [])
        risk_level = str(decision["risk_level"])
        confidence = _recall_confidence(float(row.get("relative_margin") or 0.0), float(row.get("margin") or 0.0), risk_level, flags)
        risk_score = _risk_score(risk_level, flags)
        row.update({
            "assigned": True,
            "label": row["candidate_speaker"],
            "assignment_level": "recalled",
            "confidence": confidence,
            "reason": decision["reason"],
            "risk_flags": flags,
            "quality_band": quality_band,
        })
        row["assignment"] = _build_assignment(
            row,
            label=row["candidate_speaker"],
            source="recall",
            level="recalled",
            confidence=confidence,
            risk_score=risk_score,
            risk_level=risk_level,
            reason=decision["reason"],
            risk_flags=flags,
        )
        audit.update({
            "recalled": True,
            "label": row["label"],
            "confidence": confidence,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "risk_flags": flags,
            "reason": row["reason"],
        })
        audit_rows.append(audit)
    recalled = sum(1 for row in audit_rows if row.get("recalled"))
    return {
        "stage": "segment_speaker_recall",
        "enabled": True,
        "method": "quality_band_relaxed_margin_with_risk",
        "policy_version": "segment_assignment_v2",
        "input_unknown_count": len(audit_rows),
        "recalled_count": recalled,
        "still_unknown_count": len(audit_rows) - recalled,
        "thresholds": _recall_thresholds(),
        "rows": audit_rows,
    }


def _quality_band(quality: dict[str, Any], duration_ms: int) -> tuple[str, str]:
    if duration_ms < 2000:
        return "poor", "duration_too_short_for_recall"
    speech_ratio = quality.get("speech_ratio")
    if isinstance(speech_ratio, (int, float)) and float(speech_ratio) < 0.45:
        return "poor", "speech_ratio_too_low_for_recall"
    loudness_std = quality.get("loudness_std_db")
    if isinstance(loudness_std, (int, float)) and float(loudness_std) > 18.0:
        return "poor", "loudness_too_unstable_for_recall"
    loudness = quality.get("loudness_db")
    if isinstance(loudness, (int, float)) and float(loudness) < -55.0:
        return "poor", "loudness_too_low_for_recall"
    good = duration_ms >= 5000
    if isinstance(speech_ratio, (int, float)):
        good = good and float(speech_ratio) >= 0.65
    if isinstance(loudness_std, (int, float)):
        good = good and float(loudness_std) <= 12.0
    return ("good", "quality_good") if good else ("medium", "quality_medium")


def _recall_decision(row: dict[str, Any], quality_band: str) -> dict[str, Any]:
    distance = float(row.get("best_distance") or 1.0)
    relative = float(row.get("relative_margin") or 0.0)
    margin = float(row.get("margin") or 0.0)
    radius = float(row.get("speaker_accept_radius") or 0.0)
    reason = str(row.get("strict_reason") or row.get("reason") or "")
    level = str(row.get("strict_assignment_level") or "")
    soft_ok = relative >= 0.18 or margin >= 0.08
    high_risk_ok = relative >= 0.10 or margin >= 0.04
    within_soft = distance <= 0.62 + 1e-9
    inside_slack = distance <= radius + 0.08 + 1e-9
    if (level == "tentative" or reason == "inside_accept_radius_tentative") and distance <= radius + 1e-9:
        return {"recall": True, "risk_level": "medium", "reason": "segment_embedding_tentative_recall", "risk_flags": _risk_flags(row, quality_band, ["tentative_margin"])}
    if reason == "outside_accept_radius" or distance > radius + 1e-9:
        if within_soft and inside_slack and high_risk_ok:
            return {"recall": True, "risk_level": "high", "reason": "segment_embedding_outside_radius_recall", "risk_flags": _risk_flags(row, quality_band, ["outside_accept_radius", "relaxed_margin_recall"])}
        return {"recall": False, "reject_reason": "outside_accept_radius_too_far", "risk_flags": ["outside_accept_radius"]}
    if within_soft and soft_ok:
        return {"recall": True, "risk_level": "medium", "reason": "segment_embedding_relaxed_margin_recall", "risk_flags": _risk_flags(row, quality_band, ["relaxed_margin_recall"])}
    if within_soft and high_risk_ok:
        return {"recall": True, "risk_level": "high", "reason": "segment_embedding_high_risk_margin_recall", "risk_flags": _risk_flags(row, quality_band, ["relaxed_margin_recall"])}
    return {"recall": False, "reject_reason": "no_clear_top1_preference", "risk_flags": ["low_relative_margin", "small_absolute_margin"]}


def _risk_flags(row: dict[str, Any], quality_band: str, base: list[str]) -> list[str]:
    flags = list(base)
    quality = row.get("quality") or {}
    if float(row.get("best_distance") or 1.0) > 0.42:
        flags.append("high_distance_recall")
    if float(row.get("relative_margin") or 0.0) < 0.18:
        flags.append("low_relative_margin")
    if float(row.get("margin") or 0.0) < 0.08:
        flags.append("small_absolute_margin")
    if quality_band == "medium":
        flags.append("medium_quality_recall")
    if isinstance(quality.get("speech_ratio"), (int, float)) and quality["speech_ratio"] < 0.65:
        flags.append("low_density_recall")
    if isinstance(quality.get("loudness_std_db"), (int, float)) and quality["loudness_std_db"] > 12.0:
        flags.append("unstable_loudness_recall")
    if isinstance(quality.get("loudness_db"), (int, float)) and quality["loudness_db"] < -50.0:
        flags.append("low_loudness_recall")
    if (
        isinstance(row.get("speaker_margin"), (int, float)) and row["speaker_margin"] < 0.08
        or isinstance(row.get("speaker_purity_score"), (int, float)) and row["speaker_purity_score"] < 0.30
        or isinstance(row.get("speaker_concentration"), (int, float)) and row["speaker_concentration"] < 0.60
    ):
        flags.append("weak_speaker_anchor")
    return list(dict.fromkeys(flags))


def _unknown(row: dict[str, Any], reason: str) -> dict[str, Any]:
    flags = {
        "missing_segment_embedding": ["no_embedding"],
        "no_gold_window_speakers": ["no_anchor_speaker"],
        "outside_accept_radius": ["outside_accept_radius"],
        "margin_too_low": ["low_relative_margin"],
        "inside_radius_but_margin_too_low": ["low_relative_margin"],
    }.get(reason, [])
    assignment = _build_assignment(
        row,
        label="unknown",
        source="unknown",
        level="unknown",
        confidence=0.0,
        risk_score=1.0,
        risk_level="unassigned",
        reason=reason,
        risk_flags=flags,
    )
    return {
        "assigned": False,
        "label": "unknown",
        "assignment_level": "unknown",
        "confidence": 0.0,
        "reason": reason,
        "risk_flags": flags,
        "assignment": assignment,
    }


def _build_assignment(row, *, label, source, level, confidence, risk_score, risk_level, reason, risk_flags):
    evidence_fields = [
        "candidate_speaker", "best_distance", "second_distance", "margin", "relative_margin",
        "center_distance", "medoid_distance", "speaker_core_radius", "speaker_boundary_radius",
        "speaker_accept_radius", "speaker_margin", "speaker_purity_score", "speaker_concentration",
        "speaker_is_reliable", "duration_ms", "quality_band", "strict_assignment_level", "strict_reason",
    ]
    evidence = {key: row.get(key) for key in evidence_fields if key in row}
    if isinstance(row.get("quality"), dict):
        evidence["quality"] = dict(row["quality"])
    return {
        "label": str(label),
        "status": "assigned" if label != "unknown" else "unknown",
        "source": source,
        "level": level,
        "confidence": round(float(confidence), 4),
        "reason": reason,
        "risk": {
            "score": round(float(risk_score), 4),
            "level": risk_level,
            "flags": list(risk_flags),
        },
        "evidence": evidence,
        "policy_version": "segment_assignment_v2",
    }


def _strict_confidence(level: str, relative_margin: float) -> float:
    if level == "strong":
        return round(min(1.0, 0.85 + min(0.15, max(0.0, relative_margin) * 0.08)), 4)
    if level == "normal":
        return round(min(0.85, 0.65 + min(0.20, max(0.0, relative_margin) * 0.10)), 4)
    return 0.0


def _recall_confidence(relative: float, margin: float, risk_level: str, flags: list[str]) -> float:
    if risk_level == "high":
        base = 0.48 + min(0.06, max(0.0, relative) * 0.10) + min(0.03, max(0.0, margin) * 0.25)
        return round(max(0.42, min(0.58, base - 0.01 * max(0, len(flags) - 2))), 4)
    base = 0.58 + min(0.07, max(0.0, relative) * 0.12) + min(0.04, max(0.0, margin) * 0.25)
    return round(max(0.52, min(0.70, base - 0.01 * max(0, len(flags) - 2))), 4)


def _risk_score(level: str, flags: list[str]) -> float:
    if level == "high":
        return round(min(0.85, 0.61 + 0.03 * len(flags)), 4)
    return round(min(0.60, 0.36 + 0.03 * len(flags)), 4)


def _recall_thresholds() -> dict[str, Any]:
    return {
        "medium_min_duration_ms": 2000,
        "medium_min_speech_ratio": 0.45,
        "medium_max_loudness_std_db": 18.0,
        "min_loudness_db": -55.0,
        "good_min_duration_ms": 5000,
        "good_min_speech_ratio": 0.65,
        "good_max_loudness_std_db": 12.0,
        "soft_relative_margin": 0.18,
        "soft_margin": 0.08,
        "high_risk_relative_margin": 0.10,
        "high_risk_margin": 0.04,
        "accept_radius_slack": 0.08,
        "soft_max_distance": 0.62,
        "hard_max_distance": 0.70,
        "risk_distance": 0.42,
    }
