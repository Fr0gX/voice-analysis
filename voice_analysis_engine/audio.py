"""Bounded audio probing, one-time normalization and random PCM access."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .config import AnalysisConfig
from .errors import deadline_error, input_error


_ALLOWED_CODECS = {
    "aac", "flac", "mp3", "vorbis", "opus", "amr_nb", "amr_wb",
}
_ALLOWED_CONTAINERS = {
    "wav", "wave", "mp3", "flac", "ogg", "webm", "matroska", "aac", "amr",
    "mov", "mp4", "m4a", "3gp", "3g2", "mj2",
}


@dataclass(frozen=True)
class AudioProbe:
    source_name: str
    source_bytes: int
    source_sha256: str
    format_name: str
    codec_name: str
    duration_ms: int
    sample_rate: int | None
    channels: int | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_bytes": self.source_bytes,
            "source_sha256": self.source_sha256,
            "detected_format": self.format_name,
            "detected_codec": self.codec_name,
            "duration_ms": self.duration_ms,
            "source_sample_rate": self.sample_rate,
            "source_channels": self.channels,
            "normalized_sample_rate": 16000,
            "normalized_channels": 1,
            "normalized_sample_format": "s16le",
        }


def _remaining_timeout(deadline_epoch_ms: int | None, stage: str) -> float | None:
    if deadline_epoch_ms is None:
        return None
    remaining = (deadline_epoch_ms - int(time.time() * 1000)) / 1000.0
    if remaining <= 0:
        raise deadline_error(stage)
    return remaining


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def probe_audio(path: Path, cfg: AnalysisConfig, deadline_epoch_ms: int | None) -> AudioProbe:
    source = path.resolve()
    if not source.is_file():
        raise input_error("AUDIO_NOT_FOUND", "audio file does not exist")
    size = source.stat().st_size
    limits = cfg.section("input")
    if size <= 0:
        raise input_error("AUDIO_EMPTY", "audio file must not be empty")
    if size > int(limits["maximum_file_bytes"]):
        raise input_error("AUDIO_SIZE_LIMIT", "audio file exceeds configured size limit")
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise input_error("FFPROBE_UNAVAILABLE", "ffprobe is required for content-based audio probing")
    try:
        proc = subprocess.run(
            [
                ffprobe, "-v", "error", "-select_streams", "a:0",
                "-show_entries", "format=format_name,duration:stream=codec_name,sample_rate,channels,duration",
                "-of", "json", str(source),
            ],
            capture_output=True,
            check=False,
            timeout=_remaining_timeout(deadline_epoch_ms, "audio_probe"),
        )
    except subprocess.TimeoutExpired as exc:
        raise deadline_error("audio_probe") from exc
    if proc.returncode != 0:
        raise input_error("AUDIO_PROBE_FAILED", "audio content cannot be probed")
    try:
        payload = json.loads(proc.stdout.decode("utf-8"))
        stream = (payload.get("streams") or [])[0]
        fmt = payload.get("format") or {}
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise input_error("AUDIO_PROBE_FAILED", "audio stream metadata is invalid") from exc
    codec = str(stream.get("codec_name") or "").lower()
    format_name = str(fmt.get("format_name") or "").lower()
    format_tokens = set(format_name.split(","))
    supported = codec.startswith("pcm_") or codec in _ALLOWED_CODECS
    supported = supported and bool(format_tokens & _ALLOWED_CONTAINERS)
    if not supported:
        raise input_error(
            "AUDIO_UNSUPPORTED",
            f"unsupported audio content: format={format_name or 'unknown'} codec={codec or 'unknown'}",
        )
    duration_raw = stream.get("duration") or fmt.get("duration")
    try:
        duration_ms = int(round(float(duration_raw) * 1000))
    except (TypeError, ValueError, OverflowError) as exc:
        raise input_error("AUDIO_DURATION_UNKNOWN", "audio duration cannot be determined before decode") from exc
    if not int(limits["minimum_duration_ms"]) <= duration_ms <= int(limits["maximum_duration_ms"]):
        raise input_error("AUDIO_DURATION_LIMIT", "audio duration is outside configured limits")
    return AudioProbe(
        source_name=source.name,
        source_bytes=size,
        source_sha256=_sha256_file(source),
        format_name=format_name,
        codec_name=codec,
        duration_ms=duration_ms,
        sample_rate=int(stream["sample_rate"]) if stream.get("sample_rate") else None,
        channels=int(stream["channels"]) if stream.get("channels") else None,
    )


def normalize_audio(
    source: Path,
    destination: Path,
    cfg: AnalysisConfig,
    deadline_epoch_ms: int | None,
) -> int:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise input_error("FFMPEG_UNAVAILABLE", "ffmpeg is required for audio normalization", "audio_normalize")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    try:
        proc = subprocess.run(
            [
                ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(source.resolve()),
                "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(cfg.section("input")["target_sample_rate"]),
                "-c:a", "pcm_s16le", "-f", "wav", str(partial),
            ],
            capture_output=True,
            check=False,
            timeout=_remaining_timeout(deadline_epoch_ms, "audio_normalize"),
        )
    except subprocess.TimeoutExpired as exc:
        raise deadline_error("audio_normalize") from exc
    if proc.returncode != 0 or not partial.is_file():
        partial.unlink(missing_ok=True)
        raise input_error("AUDIO_DECODE_FAILED", "audio normalization failed", "audio_normalize")
    os.replace(partial, destination)
    with wave.open(str(destination), "rb") as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getframerate() != 16000:
            raise input_error("AUDIO_NORMALIZE_INVALID", "normalized audio format is invalid", "audio_normalize")
        duration_ms = reader.getnframes() * 1000 // reader.getframerate()
    limits = cfg.section("input")
    if not int(limits["minimum_duration_ms"]) <= duration_ms <= int(limits["maximum_duration_ms"]):
        raise input_error("AUDIO_DURATION_LIMIT", "decoded audio duration is outside configured limits")
    return duration_ms


class PcmReader:
    """Open a normalized WAV on demand so the full recording never becomes resident."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        with wave.open(str(self.path), "rb") as reader:
            self.sample_rate = reader.getframerate()
            self.channels = reader.getnchannels()
            self.sample_width = reader.getsampwidth()
            self.sample_count = reader.getnframes()
        if (self.sample_rate, self.channels, self.sample_width) != (16000, 1, 2):
            raise ValueError("PcmReader requires 16 kHz mono int16 WAV")
        self.duration_ms = self.sample_count * 1000 // self.sample_rate

    def read_ms(self, start_ms: int, end_ms: int, *, maximum_ms: int | None = None) -> bytes:
        start_ms = max(0, int(start_ms))
        end_ms = min(self.duration_ms, max(start_ms, int(end_ms)))
        if maximum_ms is not None:
            end_ms = min(end_ms, start_ms + int(maximum_ms))
        start_frame = start_ms * self.sample_rate // 1000
        end_frame = end_ms * self.sample_rate // 1000
        with wave.open(str(self.path), "rb") as reader:
            reader.setpos(start_frame)
            return reader.readframes(max(0, end_frame - start_frame))

    def iter_ms(self, start_ms: int, end_ms: int, *, chunk_ms: int = 30000) -> Iterator[bytes]:
        pos = max(0, int(start_ms))
        limit = min(self.duration_ms, max(pos, int(end_ms)))
        while pos < limit:
            nxt = min(limit, pos + chunk_ms)
            yield self.read_ms(pos, nxt)
            pos = nxt

    def quality_metrics(self, start_ms: int, end_ms: int, *, speech_db_threshold: float) -> dict[str, Any]:
        db_values: list[float] = []
        speech_values: list[float] = []
        leftover = b""
        frame_bytes = self.sample_rate * 2 * 30 // 1000
        for chunk in self.iter_ms(start_ms, end_ms):
            data = leftover + chunk
            pos = 0
            while pos + frame_bytes <= len(data):
                frame = data[pos : pos + frame_bytes]
                samples = struct.unpack(f"<{len(frame) // 2}h", frame)
                power = sum(value * value for value in samples) / max(1, len(samples))
                db = -120.0 if power <= 0 else 20.0 * math.log10(math.sqrt(power) / 32768.0)
                if db > -90.0:
                    db_values.append(db)
                    if db >= speech_db_threshold:
                        speech_values.append(db)
                pos += frame_bytes
            leftover = data[pos:]
        if not db_values:
            return {"speech_ratio": 0.0, "loudness_db": None, "loudness_std_db": 0.0}
        values = speech_values or db_values
        avg = sum(values) / len(values)
        variance = sum((value - avg) ** 2 for value in values) / max(1, len(values))
        return {
            "speech_ratio": len(speech_values) / max(1, len(db_values)),
            "loudness_db": round(avg, 2),
            "loudness_std_db": math.sqrt(variance),
        }
