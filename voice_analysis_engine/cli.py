"""Command-line entry point for M1 analysis and evaluation."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from .contracts import AnalysisRequest, SegmentDocument
from .engine import AnalysisEngine
from .errors import EngineError, input_error
from .evaluation import evaluate_manifest
from .exporters import export_failure


_REPO_ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-analysis")
    commands = parser.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze", help="analyze one recording")
    analyze.add_argument("--audio", type=Path, required=True)
    analyze.add_argument("--segments", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument("--config", type=Path)
    analyze.add_argument("--deadline-sec", type=float)
    analyze.add_argument("--quiet", action="store_true")
    evaluate = commands.add_parser("evaluate", help="run an M1 evaluation manifest")
    evaluate.add_argument("--manifest", type=Path, required=True)
    evaluate.add_argument("--report", type=Path, required=True)
    evaluate.add_argument("--config", type=Path, default=_REPO_ROOT / "config" / "evaluation.yaml")
    return parser


def main(argv: list[str] | None = None) -> int:
    _load_local_env()
    args = build_parser().parse_args(argv)
    if args.command == "analyze":
        return asyncio.run(_analyze(args))
    return asyncio.run(_evaluate(args))


async def _analyze(args) -> int:
    try:
        try:
            raw = json.loads(args.segments.read_text(encoding="utf-8"))
            document = SegmentDocument.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise input_error("SEGMENTS_INVALID", f"invalid segments document: {exc}") from exc
        deadline = None
        if args.deadline_sec is not None:
            if args.deadline_sec <= 0:
                raise input_error("DEADLINE_INVALID", "deadline-sec must be greater than zero")
            deadline = int(time.time() * 1000 + args.deadline_sec * 1000)
        result = await AnalysisEngine().analyze(
            AnalysisRequest(
                audio_path=args.audio,
                document=document,
                output_dir=args.output,
                config_overlay=args.config,
                deadline_epoch_ms=deadline,
            ),
            progress=None if args.quiet else _progress,
        )
        if not args.quiet:
            print(f"analysis {result.status}: {args.output.resolve() / 'result.json'}", file=sys.stderr)
        return 0 if result.status == "success" else 2
    except EngineError as exc:
        _write_failure(args.output, exc)
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001
        failure = EngineError("INTERNAL_ERROR", "unexpected internal error", "internal", 30, False)
        _write_failure(args.output, failure)
        print(f"INTERNAL_ERROR: {failure.message}", file=sys.stderr)
        return 30


async def _evaluate(args) -> int:
    try:
        report = await evaluate_manifest(args.manifest.resolve(), args.report.resolve(), args.config.resolve())
        print(f"evaluation {report['status']}: {args.report.resolve()}", file=sys.stderr)
        return 0 if report["status"] == "passed" else 2
    except EngineError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001
        print(f"INTERNAL_ERROR: {str(exc)[:300]}", file=sys.stderr)
        return 30


def _progress(event: dict[str, object]) -> None:
    print(f"{event['stage']}: {event['status']}", file=sys.stderr)


def _write_failure(output: Path, exc: EngineError) -> None:
    try:
        export_failure({
            "schema_version": "voice_analysis_failure_v1",
            "status": "failed",
            "error": {
                "code": exc.code,
                "stage": exc.stage,
                "message": exc.message,
                "retryable": exc.retryable,
            },
        }, output)
    except OSError:
        pass


def _load_local_env() -> None:
    path = _REPO_ROOT / ".env"
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")
