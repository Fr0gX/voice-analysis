"""Audio loading helpers for the window refinement service."""
from __future__ import annotations

import audioop
import contextlib
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

try:
    import miniaudio  # type: ignore[import-untyped]
except Exception:  # noqa: BLE001
    miniaudio = None  # type: ignore[assignment]


@dataclass(frozen=True)
class PcmAudio:
    samples: bytes
    sample_rate: int
    duration_ms: int

    def slice_ms(self, start_ms: int, end_ms: int) -> "PcmAudio":
        bytes_per_ms = self.sample_rate * 2 // 1000
        start = max(0, start_ms * bytes_per_ms)
        end = min(len(self.samples), end_ms * bytes_per_ms)
        if end <= start:
            return PcmAudio(samples=b"", sample_rate=self.sample_rate, duration_ms=0)
        return PcmAudio(
            samples=self.samples[start:end],
            sample_rate=self.sample_rate,
            duration_ms=(end - start) // bytes_per_ms,
        )


def load_audio(path: str | Path, *, target_sample_rate: int = 16000) -> PcmAudio:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    if p.suffix.lower() == ".wav" or _is_riff_wav(p):
        return _load_wav(p, target_sample_rate=target_sample_rate)
    raw = _decode_compressed(p, target_sample_rate)
    duration_ms = (len(raw) // 2) * 1000 // target_sample_rate
    return PcmAudio(samples=raw, sample_rate=target_sample_rate, duration_ms=duration_ms)


def frame_iter(audio: PcmAudio, frame_ms: int = 30):
    import struct

    bytes_per_frame = audio.sample_rate * 2 * frame_ms // 1000
    pos = 0
    t = 0
    while pos + bytes_per_frame <= len(audio.samples):
        chunk = audio.samples[pos : pos + bytes_per_frame]
        samples = struct.unpack(f"<{len(chunk) // 2}h", chunk)
        yield t, t + frame_ms, samples
        pos += bytes_per_frame
        t += frame_ms


def _is_riff_wav(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            head = f.read(12)
    except OSError:
        return False
    return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE"


def _load_wav(path: Path, target_sample_rate: int) -> PcmAudio:
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
    except (wave.Error, EOFError):
        raw = _decode_compressed(path, target_sample_rate)
        duration_ms = (len(raw) // 2) * 1000 // target_sample_rate
        return PcmAudio(samples=raw, sample_rate=target_sample_rate, duration_ms=duration_ms)

    if sampwidth == 1:
        raw = audioop.lin2lin(raw, 1, 2)
        sampwidth = 2
    elif sampwidth == 4:
        raw = audioop.lin2lin(raw, 4, 2)
        sampwidth = 2
    if channels == 2:
        raw = audioop.tomono(raw, sampwidth, 0.5, 0.5)
    elif channels > 2:
        frame_bytes = channels * sampwidth
        raw = b"".join(raw[i : i + sampwidth] for i in range(0, len(raw), frame_bytes))
    if framerate != target_sample_rate:
        raw, _ = audioop.ratecv(raw, 2, 1, framerate, target_sample_rate, None)
        framerate = target_sample_rate
    duration_ms = (len(raw) // 2) * 1000 // framerate
    return PcmAudio(samples=raw, sample_rate=framerate, duration_ms=duration_ms)


def _decode_compressed(path: Path, target_sample_rate: int) -> bytes:
    errors: list[str] = []
    if miniaudio is not None:
        try:
            data = path.read_bytes()
            decoded = miniaudio.decode(  # type: ignore[union-attr]
                data,
                output_format=miniaudio.SampleFormat.SIGNED16,  # type: ignore[union-attr]
                nchannels=1,
                sample_rate=target_sample_rate,
                dither=miniaudio.DitherMode.NONE,  # type: ignore[union-attr]
            )
            return bytes(decoded.samples)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"miniaudio: {exc}")
    if shutil.which("ffmpeg") is None:
        errors.append("ffmpeg: not available")
        raise RuntimeError("audio decode failed (" + "; ".join(errors) + ")")
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel", "error",
            "-i", str(path),
            "-ac", "1",
            "-ar", str(target_sample_rate),
            "-f", "s16le",
            "-",
        ],
        capture_output=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")[:300]
        errors.append(f"ffmpeg: rc={proc.returncode} {stderr}")
        raise RuntimeError("audio decode failed (" + "; ".join(errors) + ")")
    return proc.stdout
