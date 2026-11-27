from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from .audio_utils import probe_duration_seconds

WINDOW_SECONDS = 60
OVERLAP_SECONDS = 2


@dataclass(frozen=True)
class CheckpointCapability:
    engine: str
    mode: Literal["segment", "window"]
    window_seconds: int | None = None
    overlap_seconds: int | None = None
    finalizer: Literal["none", "whisperx-diarization"] = "none"
    requires_runtime_auth: bool = False


@dataclass(frozen=True)
class CheckpointCursor:
    mode: Literal["segment", "window"]
    resume_start: float
    last_segment_end: float
    previous_text: str
    next_window_index: int | None = None
    detected_language: str | None = None


@dataclass(frozen=True)
class CheckpointWindow:
    index: int
    start: float
    end: float
    path: Path


class CheckpointBoundaryConflict(RuntimeError):
    """Raised when overlap text cannot prove a deterministic boundary."""


def capability_for(engine: str) -> CheckpointCapability | None:
    if engine == "faster-whisper":
        return CheckpointCapability(engine, "segment")
    if engine == "whisper":
        return CheckpointCapability(engine, "window", WINDOW_SECONDS, OVERLAP_SECONDS)
    if engine == "whisperx":
        return CheckpointCapability(
            engine,
            "window",
            WINDOW_SECONDS,
            OVERLAP_SECONDS,
            finalizer="whisperx-diarization",
            requires_runtime_auth=False,
        )
    if engine == "whisper-cpp":
        return CheckpointCapability(engine, "window", WINDOW_SECONDS, OVERLAP_SECONDS)
    return None


def checkpoint_window_bounds(
    duration: float, window_seconds: int, overlap_seconds: int
) -> list[tuple[int, float, float]]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("audio duration must be finite and positive")
    if window_seconds <= 0 or not 0 <= overlap_seconds < window_seconds:
        raise ValueError("invalid checkpoint window parameters")
    stride = window_seconds - overlap_seconds
    bounds: list[tuple[int, float, float]] = []
    index = 0
    start = 0.0
    while start < duration:
        end = min(start + window_seconds, duration)
        bounds.append((index, start, end))
        if end >= duration:
            break
        index += 1
        start = index * stride
    return bounds


def iter_checkpoint_windows(
    audio_path: Path,
    *,
    duration: float,
    window_seconds: int,
    overlap_seconds: int,
    start_index: int = 0,
) -> Iterator[CheckpointWindow]:
    bounds = checkpoint_window_bounds(duration, window_seconds, overlap_seconds)
    if (
        isinstance(start_index, bool)
        or not isinstance(start_index, int)
        or not 0 <= start_index < len(bounds)
    ):
        raise ValueError("checkpoint window index is outside the audio timeline")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required for checkpoint windows")
    with tempfile.TemporaryDirectory(prefix="mediascribe-checkpoint-") as directory:
        root = Path(directory)
        for index, start, end in bounds[start_index:]:
            path = root / f"window_{index:06d}.wav"
            command = [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(audio_path),
                "-t",
                f"{end - start:.3f}",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "wav",
                str(path),
            ]
            subprocess.run(command, check=True, timeout=300, capture_output=True, text=True)
            yield CheckpointWindow(index, start, end, path)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).strip("，。！？、,.!? ")


def _normalized_with_positions(text: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        for normalized_char in _normalize_text(char):
            normalized.append(normalized_char)
            positions.append(index)
    return "".join(normalized), positions


def _strip_previous_overlap(text: str, previous_text: str) -> str:
    previous = _normalize_text(previous_text)
    if not previous:
        raise CheckpointBoundaryConflict("cannot strip overlap without committed boundary text")
    normalized, positions = _normalized_with_positions(text)
    if normalized.startswith(previous):
        cut = positions[len(previous)] if len(previous) < len(positions) else len(text)
        return text[cut:].lstrip("，。！？、,.!? \t\r\n")
    if normalized.endswith(previous):
        cut = positions[len(positions) - len(previous)]
        return text[:cut].rstrip("，。！？、,.!? \t\r\n")
    raise CheckpointBoundaryConflict("resumed boundary text does not overlap committed text")


def _finite_nonnegative_timestamp(value: Any) -> float:
    try:
        timestamp = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid checkpoint timestamp") from exc
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("invalid checkpoint timestamp")
    return timestamp


def _validate_timestamps(
    start: Any, end: Any, *, maximum: float | None = None
) -> tuple[float, float]:
    start_value = _finite_nonnegative_timestamp(start)
    end_value = _finite_nonnegative_timestamp(end)
    if end_value < start_value:
        raise ValueError("invalid checkpoint timestamp order")
    if maximum is not None and end_value > maximum + 0.05:
        raise ValueError("checkpoint timestamp exceeds window duration")
    return start_value, end_value


def validate_segment_timeline(
    start: Any,
    end: Any,
    *,
    maximum: float | None = None,
    cursor_end: float | None = None,
) -> tuple[float, float]:
    """Validate a durable segment against audio and cursor boundaries."""
    start_value, end_value = _validate_timestamps(start, end, maximum=maximum)
    if cursor_end is not None:
        previous_end = _finite_nonnegative_timestamp(cursor_end)
        if end_value <= previous_end + 0.05:
            raise ValueError("segment end must advance beyond the durable cursor")
    return start_value, end_value


def validate_word_timelines(
    words: Any, *, segment_start: float, segment_end: float
) -> list[dict[str, Any]]:
    """Validate and normalize persisted word timelines inside their segment."""
    if not isinstance(words, list):
        raise ValueError("words must be a list of objects")
    validated: list[dict[str, Any]] = []
    for raw_word in words:
        if not isinstance(raw_word, dict):
            raise ValueError("words must be a list of objects")
        word = dict(raw_word)
        try:
            word_start, word_end = _validate_timestamps(word.get("start"), word.get("end"))
        except ValueError as exc:
            raise ValueError("invalid word timestamp") from exc
        if word_start < segment_start - 0.05 or word_end > segment_end + 0.05:
            raise ValueError("word timestamp falls outside its segment timeline")
        word["start"] = word_start
        word["end"] = word_end
        validated.append(word)
    return validated


def _validated_local_segments(
    result: dict[str, Any], window_duration: float
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for raw_segment in result.get("segments", []):
        segment = dict(raw_segment)
        start, end = validate_segment_timeline(
            segment.get("start", 0.0), segment.get("end", 0.0), maximum=window_duration
        )
        segment["start"] = start
        segment["end"] = end
        if "words" in segment:
            segment["words"] = validate_word_timelines(
                segment["words"], segment_start=start, segment_end=end
            )
        segments.append(segment)
    return segments


def merge_resumed_segment(
    candidate: dict[str, Any], *, cursor_end: float, previous_text: str
) -> dict[str, Any] | None:
    cursor_end = _finite_nonnegative_timestamp(cursor_end)
    start, end = _validate_timestamps(candidate["start"], candidate["end"])
    if end <= cursor_end + 0.05:
        return None
    text = str(candidate["text"]).strip()
    if start < cursor_end:
        text = _strip_previous_overlap(text, previous_text)
    merged = dict(candidate)
    merged["text"] = text
    return merged if text else None


class CheckpointSession:
    def __init__(
        self,
        *,
        capability: CheckpointCapability,
        prior_segments: list[dict[str, Any]],
        cursor: CheckpointCursor | None,
        segment_cb: Callable[[dict[str, Any]], None] | None,
        window_started_cb: Callable[[int, float, float], None] | None = None,
        window_committed_cb: Callable[[int, float, float, str | None], None] | None = None,
    ) -> None:
        if cursor is not None:
            if cursor.mode != capability.mode:
                raise ValueError("checkpoint cursor mode does not match capability")
            _finite_nonnegative_timestamp(cursor.resume_start)
            _finite_nonnegative_timestamp(cursor.last_segment_end)
            if cursor.next_window_index is not None and (
                isinstance(cursor.next_window_index, bool)
                or not isinstance(cursor.next_window_index, int)
                or cursor.next_window_index < 0
            ):
                raise ValueError("checkpoint cursor window index must be nonnegative")
        self.capability = capability
        self.cursor = cursor
        self._segments = [dict(segment) for segment in prior_segments]
        self._segment_cb = segment_cb
        self._window_started_cb = window_started_cb
        self._window_committed_cb = window_committed_cb
        self._last_end = float(cursor.last_segment_end) if cursor else 0.0
        self._previous_text = cursor.previous_text if cursor else ""

    @property
    def segments(self) -> list[dict[str, Any]]:
        return [dict(segment) for segment in self._segments]

    def accept(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        text = " ".join(str(candidate["text"]).split())
        if not text:
            raise ValueError("invalid checkpoint segment")
        merged = merge_resumed_segment(
            {**candidate, "text": text},
            cursor_end=self._last_end,
            previous_text=self._previous_text,
        )
        if merged is None:
            return None
        if self._segment_cb is not None:
            self._segment_cb(merged)
        self._segments.append(merged)
        self._last_end = float(merged["end"])
        self._previous_text = str(merged["text"])
        return merged

    def start_window(self, index: int, start: float, end: float) -> None:
        if self._window_started_cb is not None:
            self._window_started_cb(index, start, end)

    def commit_window(
        self, index: int, start: float, end: float, detected_language: str | None = None
    ) -> None:
        if self._window_committed_cb is not None:
            self._window_committed_cb(index, start, end, detected_language)


def run_windowed_transcription(
    audio_path: Path,
    *,
    capability: CheckpointCapability,
    session: CheckpointSession,
    transcribe_window: Callable[[Path, float], dict[str, Any]],
    cancel_event: Any = None,
) -> dict[str, Any]:
    if (
        capability.mode != "window"
        or capability.window_seconds is None
        or capability.overlap_seconds is None
    ):
        raise ValueError("window executor requires a window capability")
    duration = probe_duration_seconds(audio_path)
    if duration is None:
        raise RuntimeError("cannot determine audio duration for checkpointing")
    start_index = session.cursor.next_window_index if session.cursor else 0
    if start_index is None:
        raise ValueError("window resume cursor is missing next_window_index")
    bounds = checkpoint_window_bounds(
        duration, capability.window_seconds, capability.overlap_seconds
    )
    if not 0 <= start_index <= len(bounds):
        raise ValueError("checkpoint window index is outside the audio timeline")
    detected_language = session.cursor.detected_language if session.cursor else None
    windows = (
        iter_checkpoint_windows(
            audio_path,
            duration=duration,
            window_seconds=capability.window_seconds,
            overlap_seconds=capability.overlap_seconds,
            start_index=start_index,
        )
        if start_index < len(bounds)
        else iter(())
    )
    for position, window in enumerate(windows):
        expected_index = start_index + position
        if (
            isinstance(window.index, bool)
            or not isinstance(window.index, int)
            or expected_index >= len(bounds)
            or (window.index, window.start, window.end) != bounds[expected_index]
        ):
            raise ValueError("checkpoint window does not match deterministic bounds")
        if cancel_event is not None and cancel_event.is_set():
            from .pipeline_stages import PipelineCancelled

            raise PipelineCancelled("checkpoint transcription cancelled")
        session.start_window(window.index, window.start, window.end)
        result = transcribe_window(window.path, window.end - window.start)
        detected_language = detected_language or result.get("language")
        local_segments = _validated_local_segments(result, window.end - window.start)
        for segment in local_segments:
            segment["start"] += window.start
            segment["end"] += window.start
            if "words" in segment:
                segment["words"] = [
                    {
                        **word,
                        "start": word["start"] + window.start,
                        "end": word["end"] + window.start,
                    }
                    for word in segment["words"]
                ]
            session.accept(segment)
        session.commit_window(window.index, window.start, window.end, detected_language)
    text = " ".join(str(segment.get("text") or "").strip() for segment in session.segments).strip()
    return {"text": text, "segments": session.segments, "language": detected_language}
