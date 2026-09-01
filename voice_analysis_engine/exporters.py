"""Atomic exports derived exclusively from the authoritative result JSON."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import AnalysisConfig


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_text(value, encoding="utf-8", newline="\n")
    os.replace(partial, path)


def export_result(payload: dict[str, Any], output_dir: Path, cfg: AnalysisConfig) -> dict[str, str]:
    output = output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "result.json"
    _atomic_text(result_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    authority = json.loads(result_path.read_text(encoding="utf-8"))
    options = cfg.section("exports")
    written = {"json": str(result_path)}
    if options.get("txt", True):
        path = output / "transcript.txt"
        _atomic_text(path, _txt(authority, options))
        written["txt"] = str(path)
    if options.get("srt", True):
        path = output / "transcript.srt"
        _atomic_text(path, _srt(authority, options))
        written["srt"] = str(path)
    if options.get("vtt", True):
        path = output / "transcript.vtt"
        _atomic_text(path, _vtt(authority, options))
        written["vtt"] = str(path)
    return written


def export_failure(payload: dict[str, Any], output_dir: Path) -> Path:
    path = output_dir.resolve() / "failure.json"
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def _prefix(segment: dict[str, Any], options: dict[str, Any]) -> str:
    assignment = segment["assignment"]
    parts = [f"[{assignment['label']}]" ]
    if options.get("include_confidence"):
        parts.append(f"[confidence={float(assignment.get('confidence') or 0.0):.4f}]")
    if options.get("include_reason"):
        parts.append(f"[reason={assignment.get('reason') or ''}]")
    if options.get("include_risk"):
        parts.append(f"[risk={assignment.get('risk', {}).get('level') or ''}]")
    return " ".join(parts)


def _txt(payload: dict[str, Any], options: dict[str, Any]) -> str:
    rows = []
    for segment in payload["segments"]:
        normalized = segment["normalized"]
        rows.append(
            f"[{_clock(normalized['start_ms'])} - {_clock(normalized['end_ms'])}] "
            f"{_prefix(segment, options)} {normalized['text']}"
        )
    return "\n".join(rows) + ("\n" if rows else "")


def _srt(payload: dict[str, Any], options: dict[str, Any]) -> str:
    rows: list[str] = []
    for index, segment in enumerate(payload["segments"], 1):
        normalized = segment["normalized"]
        rows.extend([
            str(index),
            f"{_subtitle_time(normalized['start_ms'], ',')} --> {_subtitle_time(normalized['end_ms'], ',')}",
            f"{_prefix(segment, options)} {normalized['text']}",
            "",
        ])
    return "\n".join(rows)


def _vtt(payload: dict[str, Any], options: dict[str, Any]) -> str:
    rows = ["WEBVTT", ""]
    for segment in payload["segments"]:
        normalized = segment["normalized"]
        rows.extend([
            f"{_subtitle_time(normalized['start_ms'], '.')} --> {_subtitle_time(normalized['end_ms'], '.')}",
            f"{_prefix(segment, options)} {normalized['text']}",
            "",
        ])
    return "\n".join(rows)


def _clock(milliseconds: int) -> str:
    hours, remainder = divmod(int(milliseconds), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _subtitle_time(milliseconds: int, separator: str) -> str:
    return _clock(milliseconds).replace(".", separator)
