"""Build a traceable weak-label evaluation set from Smart Badge ``录音2`` assets."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import SegmentDocument


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SOURCE = _REPO_ROOT.parent.parent / "smart_badge-main" / "refer" / "录音2"
_DEFAULT_OUTPUT = _REPO_ROOT / "runtime" / "evaluation" / "recording2_weak_v1"
_ANNOTATION_GRADE = "weak_automatic_reviewed"
_HEADING = re.compile(
    r"^\*\*\[(?P<start>\d{2}:\d{2}:\d{2})[–-](?P<end>\d{2}:\d{2}:\d{2})\] "
    r"(?P<role>.+?) · (?P<speaker>speaker_[A-Za-z0-9_]+)\*\*\s*$",
    re.MULTILINE,
)
_PARTS_BY_SOURCE = {
    "0518_144133": 1,
    "0518_174346": 2,
    "0518_184528": 3,
    "0519_100342": 1,
    "0520_123824": 2,
    "0525_122608": 2,
    "0525_150638": 2,
    "0528_160453": 2,
    "0529_105800": 2,
    "0529_141846": 1,
    "0529_150002": 1,
    "0529_162003": 1,
}


@dataclass(frozen=True)
class SourceSegment:
    source_index: int
    start_ms: int
    end_ms: int
    original_end_ms: int
    text: str
    semantic_role: str
    raw_speaker: str
    reference_speaker: str
    flags: tuple[str, ...]


def parse_transcript(path: Path, *, audio_duration_ms: int) -> tuple[list[SourceSegment], dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    matches = list(_HEADING.finditer(raw))
    declared_match = re.search(r"(?m)^- 转写段数：(\d+)\s*$", raw)
    declared_count = int(declared_match.group(1)) if declared_match else None
    segments: list[SourceSegment] = []
    dropped_start_out_of_bounds = 0
    dropped_nonpositive_after_repair = 0
    for index, match in enumerate(matches):
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        text = "\n".join(line.strip() for line in raw[match.end() : body_end].splitlines() if line.strip())
        start_ms = _clock_ms(match.group("start"))
        original_end_ms = _clock_ms(match.group("end"))
        flags: list[str] = []
        end_ms = original_end_ms
        if end_ms <= start_ms:
            end_ms = min(audio_duration_ms, start_ms + 1000)
            flags.append("zero_duration_expanded_to_1000ms")
        if end_ms > audio_duration_ms:
            end_ms = audio_duration_ms
            flags.append("end_clamped_to_audio")
        if start_ms >= audio_duration_ms:
            dropped_start_out_of_bounds += 1
            continue
        if end_ms <= start_ms:
            dropped_nonpositive_after_repair += 1
            continue
        raw_speaker = match.group("speaker")
        reference_speaker = normalize_speaker(raw_speaker)
        if reference_speaker != raw_speaker:
            flags.append("overlap_suffix_normalized")
        role = match.group("role").strip()
        if "ASR混合" in role or "未分离" in role:
            flags.append("ambiguous_semantic_role")
        segments.append(SourceSegment(
            source_index=index,
            start_ms=start_ms,
            end_ms=end_ms,
            original_end_ms=original_end_ms,
            text=text,
            semantic_role=role,
            raw_speaker=raw_speaker,
            reference_speaker=reference_speaker,
            flags=tuple(flags),
        ))
    return segments, {
        "declared_segment_count": declared_count,
        "parsed_heading_count": len(matches),
        "usable_segment_count": len(segments),
        "dropped_out_of_bounds_count": len(matches) - len(segments),
        "dropped_start_out_of_bounds_count": dropped_start_out_of_bounds,
        "dropped_nonpositive_after_repair_count": dropped_nonpositive_after_repair,
        "declared_count_matches": declared_count == len(matches),
        "usable_count_matches_declared": declared_count == len(segments),
        "zero_duration_repair_count": sum("zero_duration_expanded_to_1000ms" in row.flags for row in segments),
        "overlap_suffix_count": sum("overlap_suffix_normalized" in row.flags for row in segments),
        "ambiguous_role_count": sum("ambiguous_semantic_role" in row.flags for row in segments),
    }


def normalize_speaker(value: str) -> str:
    return re.sub(r"(?:_overlap)+$", "", value)


def choose_boundaries(segments: list[SourceSegment], duration_ms: int, parts: int) -> list[int]:
    if parts <= 1:
        return [0, duration_ms]
    candidates: list[tuple[int, bool]] = []
    for previous, current in zip(segments, segments[1:]):
        candidates.append((current.start_ms, previous.end_ms <= current.start_ms))
    selected: list[int] = []
    minimum_spacing = min(30_000, duration_ms // max(2, parts * 3))
    for part in range(1, parts):
        target = duration_ms * part // parts
        lower = (selected[-1] if selected else 0) + minimum_spacing
        upper = duration_ms - (parts - part) * minimum_spacing
        available = [(point, clean) for point, clean in candidates if lower <= point <= upper and point not in selected]
        clean = [point for point, is_clean in available if is_clean]
        pool = clean or [point for point, _is_clean in available]
        if not pool:
            point = max(lower, min(upper, target))
        else:
            point = min(pool, key=lambda value: (abs(value - target), value))
        selected.append(point)
    return [0, *selected, duration_ms]


def build_dataset(source_root: Path, output_root: Path, *, force: bool = False) -> dict[str, Any]:
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    if not source_root.is_dir():
        raise ValueError(f"source directory does not exist: {source_root}")
    if output_root.exists():
        if not force:
            raise ValueError(f"output already exists: {output_root}")
        _assert_runtime_evaluation_target(output_root)
        shutil.rmtree(output_root)
    samples_root = output_root / "samples"
    samples_root.mkdir(parents=True)

    source_rows: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []
    sample_number = 0
    for audio_path in sorted(source_root.glob("*.mp3")):
        source_id = audio_path.stem
        if source_id not in _PARTS_BY_SOURCE:
            continue
        transcript_path = source_root / "转写文档" / f"{source_id}_转写稿_角色标注.md"
        if not transcript_path.is_file():
            raise ValueError(f"missing transcript for {source_id}")
        probe = _probe_audio(audio_path)
        segments, annotation_audit = parse_transcript(transcript_path, audio_duration_ms=probe["duration_ms"])
        boundaries = choose_boundaries(segments, probe["duration_ms"], _PARTS_BY_SOURCE[source_id])
        source_rows.append({
            "source_recording_id": source_id,
            "audio_name": audio_path.name,
            "audio_sha256": _sha256(audio_path),
            "audio_bytes": audio_path.stat().st_size,
            "audio_probe": probe,
            "transcript_name": transcript_path.name,
            "transcript_sha256": _sha256(transcript_path),
            "annotation": annotation_audit,
            "part_count": _PARTS_BY_SOURCE[source_id],
            "boundaries_ms": boundaries,
        })
        for part_index, (clip_start, clip_end) in enumerate(zip(boundaries, boundaries[1:]), 1):
            sample_number += 1
            sample_id = f"r2_{sample_number:03d}_{source_id}_p{part_index:02d}"
            selected = [
                row for row in segments
                if clip_start <= (row.start_ms + row.end_ms) // 2 < clip_end
            ]
            sample_rows.append(_write_sample(
                sample_id=sample_id,
                sample_root=samples_root / sample_id,
                source_id=source_id,
                source_audio=audio_path,
                clip_start=clip_start,
                clip_end=clip_end,
                segments=selected,
            ))

    if len(source_rows) != 12 or len(sample_rows) != 20:
        raise ValueError(f"expected 12 sources and 20 samples, got {len(source_rows)} and {len(sample_rows)}")
    scenario_counts: dict[str, int] = {}
    for row in sample_rows:
        for scenario in row["scenarios"]:
            scenario_counts[scenario] = scenario_counts.get(scenario, 0) + 1
    manifest_path = output_root / "manifest.jsonl"
    _atomic_text(manifest_path, "".join(json.dumps({
        "id": row["sample_id"],
        "source_recording_id": row["source_recording_id"],
        "annotation_grade": _ANNOTATION_GRADE,
        "audio_path": row["paths"]["audio"],
        "segments_path": row["paths"]["input"],
        "reference_path": row["paths"]["reference"],
        "scenarios": row["scenarios"],
    }, ensure_ascii=False, separators=(",", ":")) + "\n" for row in sample_rows))

    required = {"single_speaker": 3, "two_speaker": 3, "multi_speaker": 3, "overlap": 3, "noisy_or_far_field": 3}
    missing_scenarios = {name: count - scenario_counts.get(name, 0) for name, count in required.items() if scenario_counts.get(name, 0) < count}
    report = {
        "schema_version": 1,
        "dataset_id": "recording2_weak_v1",
        "annotation_grade": _ANNOTATION_GRADE,
        "formal_gate_eligible": False,
        "eligibility_reasons": ["only_12_independent_source_recordings", "speaker_reference_is_asr_weak_label"],
        "source_root_name": source_root.name,
        "independent_source_count": len(source_rows),
        "derived_sample_count": len(sample_rows),
        "total_source_duration_ms": sum(row["audio_probe"]["duration_ms"] for row in source_rows),
        "total_derived_duration_ms": sum(row["duration_ms"] for row in sample_rows),
        "total_input_segments": sum(row["segment_count"] for row in sample_rows),
        "scenario_counts": scenario_counts,
        "missing_scenarios": missing_scenarios,
        "valid": not missing_scenarios and all(row["valid"] for row in sample_rows) and all(
            row["annotation"]["declared_count_matches"]
            and row["annotation"]["usable_count_matches_declared"]
            and row["annotation"]["dropped_out_of_bounds_count"] == 0
            for row in source_rows
        ),
        "sources": source_rows,
        "samples": sample_rows,
    }
    _atomic_json(output_root / "validation-report.json", report)
    _atomic_json(output_root / "dataset-metadata.json", {
        key: report[key] for key in (
            "schema_version", "dataset_id", "annotation_grade", "formal_gate_eligible",
            "eligibility_reasons", "independent_source_count", "derived_sample_count",
            "total_source_duration_ms", "total_derived_duration_ms", "total_input_segments",
            "scenario_counts", "missing_scenarios", "valid",
        )
    })
    return report


def _write_sample(*, sample_id: str, sample_root: Path, source_id: str, source_audio: Path, clip_start: int, clip_end: int, segments: list[SourceSegment]) -> dict[str, Any]:
    sample_root.mkdir(parents=True)
    audio_path = sample_root / "audio.mp3"
    _render_clip(source_audio, audio_path, clip_start, clip_end)
    probe = _probe_audio(audio_path)
    relative_segments: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    for row in segments:
        start_ms = max(0, row.start_ms - clip_start)
        end_ms = min(probe["duration_ms"], row.end_ms - clip_start)
        if end_ms <= start_ms:
            continue
        segment_id = f"{sample_id}_seg_{row.source_index:05d}"
        relative_segments.append({
            "id": segment_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": row.text,
            "speaker": row.raw_speaker,
            "source_recording_id": source_id,
            "source_segment_index": row.source_index,
            "source_start_ms": row.start_ms,
            "source_end_ms": row.original_end_ms,
            "semantic_role": row.semantic_role,
            "reference_speaker": row.reference_speaker,
            "annotation_grade": _ANNOTATION_GRADE,
            "annotation_flags": list(row.flags),
        })
        references.append({
            "id": segment_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "speaker": row.reference_speaker,
            "semantic_role": row.semantic_role,
            "raw_speaker": row.raw_speaker,
            "annotation_grade": _ANNOTATION_GRADE,
            "annotation_flags": list(row.flags),
        })
    document = {
        "schema_version": "voice_analysis_input_v1",
        "metadata": {
            "dataset_id": "recording2_weak_v1",
            "sample_id": sample_id,
            "source_recording_id": source_id,
            "source_clip_start_ms": clip_start,
            "source_clip_end_ms": clip_end,
            "annotation_grade": _ANNOTATION_GRADE,
        },
        "segments": relative_segments,
    }
    SegmentDocument.model_validate(document)
    reference = {
        "schema_version": "voice_analysis_reference_v1",
        "annotation_grade": _ANNOTATION_GRADE,
        "provenance": {
            "source_recording_id": source_id,
            "source_clip_start_ms": clip_start,
            "source_clip_end_ms": clip_end,
            "speaker_label_source": "internal_asr_diarization_normalized_overlap_suffix",
            "human_identity_verified": False,
        },
        "segments": references,
    }
    _atomic_json(sample_root / "input.json", document)
    _atomic_json(sample_root / "reference.json", reference)
    speakers = sorted({row["speaker"] for row in references})
    roles = sorted({row["semantic_role"] for row in references})
    scenarios = _scenarios(references, speakers, roles)
    relative_root = Path("samples") / sample_id
    return {
        "sample_id": sample_id,
        "source_recording_id": source_id,
        "source_clip_start_ms": clip_start,
        "source_clip_end_ms": clip_end,
        "duration_ms": probe["duration_ms"],
        "segment_count": len(relative_segments),
        "speaker_count": len(speakers),
        "speakers": speakers,
        "semantic_roles": roles,
        "scenarios": scenarios,
        "zero_duration_repair_count": sum("zero_duration_expanded_to_1000ms" in row["annotation_flags"] for row in references),
        "overlap_suffix_count": sum("overlap_suffix_normalized" in row["annotation_flags"] for row in references),
        "ambiguous_role_count": sum("ambiguous_semantic_role" in row["annotation_flags"] for row in references),
        "audio_sha256": _sha256(audio_path),
        "audio_bytes": audio_path.stat().st_size,
        "audio_probe": probe,
        "paths": {
            "audio": (relative_root / "audio.mp3").as_posix(),
            "input": (relative_root / "input.json").as_posix(),
            "reference": (relative_root / "reference.json").as_posix(),
        },
        "valid": bool(relative_segments) and all(0 <= row["start_ms"] < row["end_ms"] <= probe["duration_ms"] for row in relative_segments),
    }


def _scenarios(references: list[dict[str, Any]], speakers: list[str], roles: list[str]) -> list[str]:
    scenarios: list[str] = []
    if len(speakers) == 1:
        scenarios.append("single_speaker")
    elif len(speakers) == 2:
        scenarios.append("two_speaker")
    elif len(speakers) >= 3:
        scenarios.append("multi_speaker")
    if any("overlap_suffix_normalized" in row["annotation_flags"] for row in references) or _has_overlap(references):
        scenarios.append("overlap")
    if any("现场" in role or "环境" in role for role in roles):
        scenarios.append("noisy_or_far_field")
    return scenarios


def _has_overlap(rows: list[dict[str, Any]]) -> bool:
    ordered = sorted(rows, key=lambda row: (row["start_ms"], row["end_ms"], row["speaker"]))
    latest_end = -1
    latest_speaker = ""
    for row in ordered:
        if row["start_ms"] < latest_end and row["speaker"] != latest_speaker:
            return True
        if row["end_ms"] > latest_end:
            latest_end = row["end_ms"]
            latest_speaker = row["speaker"]
    return False


def _render_clip(source: Path, destination: Path, start_ms: int, end_ms: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError("ffmpeg is required")
    proc = subprocess.run([
        ffmpeg, "-nostdin", "-v", "error", "-y",
        "-ss", f"{start_ms / 1000:.3f}", "-i", str(source),
        "-t", f"{(end_ms - start_ms) / 1000:.3f}",
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", "64k", str(destination),
    ], capture_output=True, check=False)
    if proc.returncode != 0 or not destination.is_file():
        raise ValueError(f"ffmpeg failed for {source.name}: {proc.stderr.decode(errors='replace')[:300]}")


def _probe_audio(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise ValueError("ffprobe is required")
    proc = subprocess.run([
        ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "format=duration,format_name:stream=codec_name,sample_rate,channels",
        "-of", "json", str(path),
    ], capture_output=True, check=False)
    if proc.returncode != 0:
        raise ValueError(f"ffprobe failed for {path.name}")
    payload = json.loads(proc.stdout)
    stream = payload["streams"][0]
    fmt = payload["format"]
    return {
        "duration_ms": int(round(float(fmt["duration"]) * 1000)),
        "format_name": str(fmt.get("format_name") or ""),
        "codec_name": str(stream.get("codec_name") or ""),
        "sample_rate": int(stream["sample_rate"]),
        "channels": int(stream["channels"]),
    }


def _assert_runtime_evaluation_target(path: Path) -> None:
    allowed = (_REPO_ROOT / "runtime" / "evaluation").resolve()
    if path.parent != allowed:
        raise ValueError("refusing to replace an output outside runtime/evaluation/<dataset>")


def _clock_ms(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_text(value, encoding="utf-8", newline="\n")
    partial.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=_DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = build_dataset(args.source, args.output, force=args.force)
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"recording2 dataset build failed: {exc}")
        return 1
    print(json.dumps({
        "output": str(args.output.resolve()),
        "valid": report["valid"],
        "independent_source_count": report["independent_source_count"],
        "derived_sample_count": report["derived_sample_count"],
        "total_derived_duration_ms": report["total_derived_duration_ms"],
        "scenario_counts": report["scenario_counts"],
        "missing_scenarios": report["missing_scenarios"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
