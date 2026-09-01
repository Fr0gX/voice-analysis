from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import tracemalloc
import wave

import pytest

from voice_analysis_engine.audio import PcmReader, normalize_audio, probe_audio
from voice_analysis_engine.config import load_analysis_config


def _wav(path, duration_ms=1000):
    sample_rate = 16000
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"".join(
            int(4000 * math.sin(2 * math.pi * 220 * index / sample_rate)).to_bytes(2, "little", signed=True)
            for index in range(sample_rate * duration_ms // 1000)
        ))


@pytest.mark.parametrize(
    ("suffix", "arguments"),
    [
        ("mp3", ["-c:a", "libmp3lame"]),
        ("flac", ["-c:a", "flac"]),
        ("m4a", ["-c:a", "aac"]),
        ("aac", ["-c:a", "aac", "-f", "adts"]),
        ("ogg", ["-c:a", "libvorbis"]),
        ("opus", ["-c:a", "libopus", "-f", "ogg"]),
        ("webm", ["-c:a", "libopus", "-f", "webm"]),
        ("3gp", ["-c:a", "aac", "-f", "3gp"]),
    ],
)
def test_mainstream_compressed_formats_are_content_probed_and_normalized(tmp_path, suffix, arguments):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg unavailable")
    source = tmp_path / "source.wav"
    encoded = tmp_path / f"encoded.{suffix}"
    normalized = tmp_path / f"normalized-{suffix}.wav"
    _wav(source)
    proc = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(source), *arguments, str(encoded)],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"local FFmpeg lacks {suffix} encoder")
    config = load_analysis_config()
    probe = probe_audio(encoded, config, None)
    duration = normalize_audio(encoded, normalized, config, None)
    reader = PcmReader(normalized)
    assert probe.duration_ms >= 900
    assert duration >= 900
    assert 0 < len(reader.read_ms(100, 200)) <= 3200


def test_minimum_duration_boundary(tmp_path):
    source = tmp_path / "minimum.wav"
    normalized = tmp_path / "normalized.wav"
    _wav(source, duration_ms=400)
    config = load_analysis_config()
    assert probe_audio(source, config, None).duration_ms == 400
    assert normalize_audio(source, normalized, config, None) == 400


def test_four_hour_sparse_pcm_is_random_read_without_full_residency(tmp_path):
    sample_rate = 16000
    duration_seconds = 4 * 60 * 60
    data_size = sample_rate * duration_seconds * 2
    path = tmp_path / "four-hours.wav"
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )
    path.write_bytes(header)
    if os.name == "nt":
        subprocess.run(["fsutil", "sparse", "setflag", str(path)], capture_output=True, check=False)
    with path.open("r+b") as stream:
        stream.seek(44 + data_size - 1)
        stream.write(b"\0")
    reader = PcmReader(path)
    tracemalloc.start()
    chunk = reader.read_ms(reader.duration_ms - 1000, reader.duration_ms)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert reader.duration_ms == 14_400_000
    assert len(chunk) == 32000
    assert peak < 2 * 1024 * 1024
