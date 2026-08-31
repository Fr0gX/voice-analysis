"""pyannote-backed ASR candidate speech-window segmentation."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .audio_io import PcmAudio, frame_iter, load_audio
from .config import ServiceConfig


@dataclass(frozen=True)
class SegmentationFrame:
    start_ms: int
    end_ms: int
    active_speaker_count: int
    dominant_speaker: str = ""
    confidence: float | None = None


class PyannoteWindowRefineBackend:
    backend_name = "pyannote_segmentation"

    def __init__(self, cfg: ServiceConfig) -> None:
        self.cfg = cfg
        self.loaded = False
        self.error: str | None = None
        self.device: str | None = None
        self._model: Any = None
        self._osd_pipeline: Any = None
        self.osd_error: str | None = None

    @property
    def available(self) -> bool:
        if self.loaded:
            return True
        try:
            self._load()
            self.loaded = True
            self.error = None
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            self.loaded = False
            return False

    def segment(
        self,
        *,
        audio_path: str,
        asr_candidate_windows: list[dict[str, Any]],
        profile: dict[str, Any],
        speech_db_threshold: float,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not self.available:
            raise RuntimeError(self.error or "pyannote model unavailable")
        audio = load_audio(audio_path, target_sample_rate=self.cfg.sample_rate)
        thresholds = _thresholds(profile)
        frames = self._timeline(audio)
        selected, split_events, split_event_counts = _split_candidates(
            audio,
            asr_candidate_windows,
            frames,
            thresholds=thresholds,
            backend_name=self.backend_name,
            speech_db_threshold=speech_db_threshold,
        )
        return selected, {
            "backend": self.backend_name,
            "selected_count": len(selected),
            "split_event_counts": split_event_counts,
            "split_events": split_events[:120],
            "rejected": {},
            "rejected_rows": [],
            "thresholds": thresholds,
            "frame_count": len(frames),
            "model_ready": True,
            "segmentation_model_dir": str(self.cfg.segmentation_model_dir),
            "osd_model_dir": str(self.cfg.osd_model_dir),
            "osd_enabled": bool(self.cfg.osd_enabled),
            "osd_ready": self._osd_pipeline is not None,
            "osd_error": self.osd_error,
            "overlap_source": "legacy_osd_pipeline" if self._osd_pipeline is not None else "segmentation_powerset",
            "stage": "speech_window_candidate_generation",
            "soft_quality_flags": ["tiny_interval", "low_speech_ratio", "unstable_loudness"],
        }

    def _load(self) -> None:
        try:
            import torch
            from pyannote.audio import Model
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"pyannote.audio unavailable: {exc}") from exc

        token = os.getenv(self.cfg.hf_token_env)
        source = _model_source(
            self.cfg.segmentation_model_dir,
            "pyannote/segmentation-3.0",
            allow_download=self.cfg.allow_model_download,
        )
        kwargs = {"use_auth_token": token} if self.cfg.allow_model_download and token else {}
        self._model = Model.from_pretrained(source, **kwargs)
        if hasattr(self._model, "eval"):
            self._model.eval()
        self.device = _select_device(self.cfg.device, torch)
        if self.device:
            self._model.to(torch.device(self.device))

        self.osd_error = None
        self._osd_pipeline = None
        if self.cfg.osd_enabled and self.cfg.osd_model_dir.exists():
            try:
                from pyannote.audio import Pipeline

                osd_source = _model_source(
                    self.cfg.osd_model_dir,
                    "pyannote/overlapped-speech-detection",
                    allow_download=self.cfg.allow_model_download,
                )
                self._osd_pipeline = Pipeline.from_pretrained(osd_source, **kwargs)
                if self.device and hasattr(self._osd_pipeline, "to"):
                    self._osd_pipeline.to(torch.device(self.device))
            except Exception as exc:  # noqa: BLE001
                self.osd_error = f"{type(exc).__name__}: {exc}"

    def _timeline(self, audio: PcmAudio) -> list[SegmentationFrame]:
        import torch

        waveform = _torch_waveform(audio, torch)
        with torch.no_grad():
            prediction = self._model(waveform)
        data, starts, ends = _prediction_to_arrays(prediction, audio.duration_ms)
        counts, dominant, confidence = _powerset_to_activity(data)
        frames = [
            SegmentationFrame(
                start_ms=int(starts[i]),
                end_ms=int(ends[i]),
                active_speaker_count=int(counts[i]),
                dominant_speaker=str(dominant[i] or ""),
                confidence=None if confidence[i] is None else float(confidence[i]),
            )
            for i in range(len(counts))
            if int(ends[i]) > int(starts[i])
        ]
        if self._osd_pipeline is not None:
            frames = self._apply_osd(audio, frames)
        return frames

    def _apply_osd(self, audio: PcmAudio, frames: list[SegmentationFrame]) -> list[SegmentationFrame]:
        if not frames:
            return frames
        import torch

        try:
            with torch.no_grad():
                annotation = self._osd_pipeline({
                    "waveform": _torch_waveform(audio, torch),
                    "sample_rate": audio.sample_rate,
                })
            intervals = [
                (int(round(float(segment.start) * 1000)), int(round(float(segment.end) * 1000)))
                for segment, _track, _label in annotation.itertracks(yield_label=True)
            ]
        except Exception:
            return frames
        if not intervals:
            return frames
        out: list[SegmentationFrame] = []
        for frame in frames:
            if any(_overlap_ms(frame.start_ms, frame.end_ms, s, e) > 0 for s, e in intervals):
                out.append(
                    SegmentationFrame(
                        start_ms=frame.start_ms,
                        end_ms=frame.end_ms,
                        active_speaker_count=max(2, frame.active_speaker_count),
                        dominant_speaker=frame.dominant_speaker,
                        confidence=frame.confidence,
                    )
                )
            else:
                out.append(frame)
        return out


def _model_source(local_dir: Path, hf_id: str, *, allow_download: bool) -> str:
    if local_dir.exists():
        return str(local_dir)
    if allow_download:
        return hf_id
    raise RuntimeError(f"local model directory not found: {local_dir}")


def _select_device(requested: str, torch) -> str:
    value = (requested or "auto").strip().lower()
    if value == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return value


def _thresholds(profile: dict[str, Any]) -> dict[str, Any]:
    clean = profile.get("clean_window") if isinstance(profile.get("clean_window"), dict) else {}
    candidate_cfg = (
        profile.get("speech_window_candidate")
        if isinstance(profile.get("speech_window_candidate"), dict)
        else {}
    )
    return {
        "edge_trim_ms": int(candidate_cfg.get("edge_trim_ms") or 200),
        "min_trimmed_ms": int(candidate_cfg.get("min_trimmed_ms") or 1200),
        "max_join_gap_ms": int(candidate_cfg.get("max_join_gap_ms") or clean.get("max_join_gap_ms") or 500),
        "min_speech_ratio": float(candidate_cfg.get("min_speech_ratio") or 0.75),
        "max_loudness_std_db": float(candidate_cfg.get("max_loudness_std_db") or 10.0),
    }


def _torch_waveform(audio: PcmAudio, torch):
    pcm = np.frombuffer(audio.samples, dtype="<i2").astype(np.float32) / 32768.0
    return torch.from_numpy(pcm).unsqueeze(0)


def _prediction_to_arrays(prediction: Any, duration_ms: int) -> tuple[np.ndarray, list[int], list[int]]:
    if hasattr(prediction, "data"):
        data = np.asarray(prediction.data)
        if data.ndim == 3:
            data = data.reshape((-1, data.shape[-1]))
        sw = getattr(prediction, "sliding_window", None)
        if sw is not None:
            try:
                starts: list[int] = []
                ends: list[int] = []
                for idx in range(len(data)):
                    segment = sw[idx]
                    starts.append(int(round(float(segment.start) * 1000)))
                    ends.append(int(round(float(segment.end) * 1000)))
                return data, starts, ends
            except Exception:
                pass
    else:
        data = np.asarray(prediction.detach().cpu().numpy() if hasattr(prediction, "detach") else prediction)
    if data.ndim == 3:
        data = data.reshape((-1, data.shape[-1]))
    if data.ndim == 1:
        data = data.reshape((-1, 1))
    count = int(data.shape[0])
    if count <= 0:
        return data, [], []
    step = max(1.0, float(duration_ms) / float(count))
    return (
        data,
        [int(round(i * step)) for i in range(count)],
        [int(round((i + 1) * step)) for i in range(count)],
    )


def _powerset_to_activity(data: np.ndarray) -> tuple[list[int], list[str], list[float | None]]:
    if data.ndim == 3:
        data = data.reshape((-1, data.shape[-1]))
    if data.ndim == 1:
        data = data.reshape((-1, 1))
    if data.shape[1] <= 1:
        values = data[:, 0]
        counts = [1 if float(v) >= 0.5 else 0 for v in values]
        return counts, ["speaker_0" if c else "" for c in counts], [float(v) for v in values]
    probs = _softmax(data)
    if data.shape[1] == 2:
        speech = probs[:, 1]
        counts = [1 if float(v) >= 0.5 else 0 for v in speech]
        return counts, ["speaker_0" if c else "" for c in counts], [float(v) for v in speech]
    if data.shape[1] == 7:
        class_counts = [0, 1, 1, 1, 2, 2, 2]
        class_speakers = ["", "speaker_0", "speaker_1", "speaker_2", "speaker_0", "speaker_0", "speaker_1"]
        winners = np.argmax(probs, axis=1)
        return (
            [class_counts[int(i)] for i in winners],
            [class_speakers[int(i)] for i in winners],
            [float(probs[row, int(col)]) for row, col in enumerate(winners)],
        )
    winners = np.argmax(probs, axis=1)
    counts = [0 if int(i) == 0 else 1 for i in winners]
    return (
        counts,
        ["" if c == 0 else f"class_{int(i)}" for c, i in zip(counts, winners)],
        [float(probs[row, int(col)]) for row, col in enumerate(winners)],
    )


def _softmax(data: np.ndarray) -> np.ndarray:
    values = np.asarray(data, dtype=np.float32)
    if np.all(values >= 0.0) and np.all(values <= 1.0):
        row_sum = values.sum(axis=1, keepdims=True)
        if np.all(row_sum > 0.0) and np.all(row_sum <= 1.2):
            return values
    shifted = values - values.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.maximum(exp.sum(axis=1, keepdims=True), 1e-8)


def _split_candidates(
    audio: PcmAudio,
    asr_candidates: list[dict[str, Any]],
    frames: list[SegmentationFrame],
    *,
    thresholds: dict[str, Any],
    backend_name: str,
    speech_db_threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    selected: list[dict[str, Any]] = []
    split_events: list[dict[str, Any]] = []
    split_event_counts: dict[str, int] = {}
    edge = int(thresholds["edge_trim_ms"])
    max_gap = int(thresholds["max_join_gap_ms"])
    for candidate in asr_candidates:
        cand_frames = [
            f for f in frames
            if _overlap_ms(f.start_ms, f.end_ms, int(candidate["start_ms"]), int(candidate["end_ms"])) > 0
        ]
        if not cand_frames:
            _record_split_event(split_event_counts, split_events, candidate, "no_segmentation_frames")
            continue
        blocked = _blocked_ranges(cand_frames, edge_trim_ms=edge, max_join_gap_ms=max_gap)
        for start, end in blocked:
            _record_split_event(
                split_event_counts,
                split_events,
                candidate,
                "blocked_interval",
                start_ms=start,
                end_ms=end,
            )
        for raw_start, raw_end in _single_speaker_groups(cand_frames, blocked, max_join_gap_ms=max_gap):
            window = _build_window(
                audio,
                candidate,
                cand_frames,
                raw_start,
                raw_end,
                backend_name=backend_name,
                thresholds=thresholds,
                speech_db_threshold=speech_db_threshold,
                window_id=f"speech_window_{len(selected):03d}",
            )
            selected.append(window)
    return selected, split_events, split_event_counts


def _blocked_ranges(frames: list[SegmentationFrame], *, edge_trim_ms: int, max_join_gap_ms: int) -> list[tuple[int, int]]:
    blocked: list[tuple[int, int]] = []
    for frame in frames:
        if frame.active_speaker_count >= 2:
            blocked.append((frame.start_ms - edge_trim_ms, frame.end_ms + edge_trim_ms))
    last_single: SegmentationFrame | None = None
    last_silence_gap = 0
    for frame in frames:
        if frame.active_speaker_count == 0:
            if last_single is not None:
                last_silence_gap += max(0, frame.end_ms - frame.start_ms)
            continue
        if frame.active_speaker_count >= 2:
            last_single = None
            last_silence_gap = 0
            continue
        if (
            last_single is not None
            and last_single.dominant_speaker
            and frame.dominant_speaker
            and frame.dominant_speaker != last_single.dominant_speaker
            and last_silence_gap <= max_join_gap_ms
        ):
            boundary = max(last_single.end_ms, frame.start_ms)
            blocked.append((boundary - edge_trim_ms, boundary + edge_trim_ms))
        last_single = frame
        last_silence_gap = 0
    return _merge_ranges(blocked)


def _single_speaker_groups(
    frames: list[SegmentationFrame],
    blocked: list[tuple[int, int]],
    *,
    max_join_gap_ms: int,
) -> list[tuple[int, int]]:
    groups: list[tuple[int, int]] = []
    current_start: int | None = None
    current_end: int | None = None
    last_single_end: int | None = None
    for frame in frames:
        if _is_blocked(frame.start_ms, frame.end_ms, blocked) or frame.active_speaker_count >= 2:
            if current_start is not None and current_end is not None:
                groups.append((current_start, current_end))
            current_start = None
            current_end = None
            last_single_end = None
            continue
        if frame.active_speaker_count == 0:
            if current_start is not None and last_single_end is not None:
                gap = max(0, frame.end_ms - last_single_end)
                if gap > max_join_gap_ms:
                    groups.append((current_start, last_single_end))
                    current_start = None
                    current_end = None
                    last_single_end = None
            continue
        if current_start is None or last_single_end is None:
            current_start = frame.start_ms
        elif frame.start_ms - last_single_end > max_join_gap_ms:
            groups.append((current_start, last_single_end))
            current_start = frame.start_ms
        current_end = frame.end_ms
        last_single_end = frame.end_ms
    if current_start is not None and current_end is not None:
        groups.append((current_start, current_end))
    return [(s, e) for s, e in groups if e > s]


def _build_window(
    audio: PcmAudio,
    candidate: dict[str, Any],
    frames: list[SegmentationFrame],
    raw_start: int,
    raw_end: int,
    *,
    backend_name: str,
    thresholds: dict[str, Any],
    speech_db_threshold: float,
    window_id: str,
) -> dict[str, Any]:
    start = max(int(candidate["start_ms"]), int(raw_start))
    end = min(int(candidate["end_ms"]), int(raw_end))
    stats = _window_audio_stats(audio, start, end, speech_db_threshold=speech_db_threshold)
    overlap_ms = _active_ms(frames, start, end, min_active=2)
    change_count = _change_point_count(frames, start, end)
    flags: list[str] = []
    if end - start < int(thresholds["min_trimmed_ms"]):
        flags.append("tiny_interval")
    if float(stats["speech_ratio"]) < float(thresholds["min_speech_ratio"]):
        flags.append("low_speech_ratio")
    if overlap_ms > 0:
        flags.append("residual_overlap")
    if change_count > 0:
        flags.append("residual_speaker_change")
    if float(stats["loudness_std_db"]) > float(thresholds["max_loudness_std_db"]):
        flags.append("unstable_loudness")
    return {
        "window_id": window_id,
        "source_candidate_id": str(candidate.get("candidate_id") or ""),
        "start_ms": start,
        "end_ms": end,
        "duration_ms": max(0, end - start),
        "raw_start_ms": int(raw_start),
        "raw_end_ms": int(raw_end),
        "source_segment_indices": list(candidate.get("source_segment_indices") or []),
        "backend": backend_name,
        "speech_ratio": round(float(stats["speech_ratio"]), 4),
        "overlap_ms": int(overlap_ms),
        "change_point_count": int(change_count),
        "boundary_left_silence_ms": _boundary_silence_ms(frames, start, left=True),
        "boundary_right_silence_ms": _boundary_silence_ms(frames, end, left=False),
        "loudness_db": stats["loudness_db"],
        "loudness_std_db": round(float(stats["loudness_std_db"]), 4),
        "flags": flags,
        "reject_reasons": [],
    }


def _window_audio_stats(audio: PcmAudio, start_ms: int, end_ms: int, *, speech_db_threshold: float) -> dict[str, Any]:
    clip = audio.slice_ms(max(0, int(start_ms)), max(int(start_ms), int(end_ms)))
    db_values: list[float] = []
    speech_values: list[float] = []
    for _s, _e, samples in frame_iter(clip, frame_ms=30):
        db = _rms_db(samples)
        if db <= -90.0:
            continue
        db_values.append(float(db))
        if db >= speech_db_threshold:
            speech_values.append(float(db))
    if not db_values:
        return {"speech_ratio": 0.0, "loudness_db": None, "loudness_std_db": 0.0}
    values = speech_values or db_values
    avg = sum(values) / len(values)
    var = sum((x - avg) * (x - avg) for x in values) / max(1, len(values))
    return {
        "speech_ratio": len(speech_values) / max(1, len(db_values)),
        "loudness_db": round(avg, 2),
        "loudness_std_db": math.sqrt(var),
    }


def _rms_db(samples) -> float:
    if not samples:
        return -120.0
    s = sum(x * x for x in samples) / len(samples)
    if s <= 0:
        return -120.0
    return 20.0 * math.log10(math.sqrt(s) / 32768.0)


def _active_ms(frames: list[SegmentationFrame], start: int, end: int, *, min_active: int) -> int:
    return sum(
        _overlap_ms(frame.start_ms, frame.end_ms, start, end)
        for frame in frames
        if frame.active_speaker_count >= min_active
    )


def _change_point_count(frames: list[SegmentationFrame], start: int, end: int) -> int:
    count = 0
    last = ""
    for frame in frames:
        if frame.end_ms <= start or frame.start_ms >= end or frame.active_speaker_count != 1:
            continue
        speaker = frame.dominant_speaker
        if last and speaker and speaker != last:
            count += 1
        if speaker:
            last = speaker
    return count


def _boundary_silence_ms(frames: list[SegmentationFrame], boundary_ms: int, *, left: bool) -> int:
    total = 0
    ordered = reversed(frames) if left else iter(frames)
    for frame in ordered:
        if left and frame.end_ms > boundary_ms:
            continue
        if not left and frame.start_ms < boundary_ms:
            continue
        if frame.active_speaker_count != 0:
            break
        total += max(0, frame.end_ms - frame.start_ms)
    return int(total)


def _record_split_event(
    split_event_counts: dict[str, int],
    split_events: list[dict[str, Any]],
    candidate: dict[str, Any],
    reason: str,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> None:
    split_event_counts[reason] = split_event_counts.get(reason, 0) + 1
    row = {
        "source_candidate_id": str(candidate.get("candidate_id") or ""),
        "source_segment_indices": list(candidate.get("source_segment_indices") or []),
        "reason": reason,
    }
    if start_ms is not None and end_ms is not None:
        row.update({"start_ms": int(start_ms), "end_ms": int(end_ms)})
    split_events.append(row)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for start, end in sorted((s, e) for s, e in ranges if e > s):
        if not out or start > out[-1][1]:
            out.append((start, end))
        else:
            out[-1] = (out[-1][0], max(out[-1][1], end))
    return out


def _is_blocked(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(_overlap_ms(start, end, s, e) > 0 for s, e in ranges)


def _overlap_ms(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(int(a_end), int(b_end)) - max(int(a_start), int(b_start)))
