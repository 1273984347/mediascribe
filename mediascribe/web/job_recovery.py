"""Fingerprints and recovery planning for durable Web jobs."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mediascribe.checkpoint import CheckpointBoundaryConflict as CheckpointBoundaryConflict
from mediascribe.checkpoint import (
    CheckpointCapability,
    capability_for,
    checkpoint_window_bounds,
    validate_segment_timeline,
    validate_word_timelines,
)
from mediascribe.checkpoint import merge_resumed_segment as merge_resumed_segment


def audio_sha256(path: Path) -> str:
    """Return the SHA-256 fingerprint of an audio file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decode_params_hash(params: dict[str, Any]) -> str:
    """Hash effective decode parameters, excluding resume authorization metadata."""
    payload = {key: value for key, value in params.items() if key != "resume_auth_required"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResumePlan:
    job_id: str
    attempt_id: str
    audio_path: Path
    output_path: Path
    resume_start: float
    next_segment_index: int
    decode_params_hash: str
    mode: str = "segment"
    window_seconds: int | None = None
    overlap_seconds: int | None = None
    next_window_index: int | None = None
    detected_language: str | None = None
    runtime_auth_required: bool = False


def _window_resume_state(
    snapshot: dict[str, Any], capability: CheckpointCapability
) -> tuple[int, int, int, float]:
    """Validate the completed and active window markers against deterministic bounds."""
    window_seconds = capability.window_seconds
    overlap_seconds = capability.overlap_seconds
    stored_window_seconds = snapshot.get("checkpoint_window_seconds")
    stored_overlap_seconds = snapshot.get("checkpoint_overlap_seconds")
    if (
        window_seconds is None
        or overlap_seconds is None
        or isinstance(stored_window_seconds, bool)
        or not isinstance(stored_window_seconds, int)
        or stored_window_seconds != window_seconds
        or isinstance(stored_overlap_seconds, bool)
        or not isinstance(stored_overlap_seconds, int)
        or stored_overlap_seconds != overlap_seconds
    ):
        raise ValueError("CHECKPOINT_CONFLICT")
    duration_value = snapshot.get("audio_duration")
    if duration_value is None:
        raise ValueError("CHECKPOINT_CONFLICT")
    try:
        duration = float(duration_value)
        bounds = checkpoint_window_bounds(duration, window_seconds, overlap_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("CHECKPOINT_CONFLICT") from exc

    window_index = snapshot.get("checkpoint_window_index")
    if isinstance(window_index, bool) or not isinstance(window_index, int):
        raise ValueError("CHECKPOINT_CONFLICT")
    if window_index == -1:
        expected_cursor = 0.0
    elif 0 <= window_index < len(bounds):
        expected_cursor = bounds[window_index][2]
    else:
        raise ValueError("CHECKPOINT_CONFLICT")
    cursor_value = snapshot.get("checkpoint_cursor")
    if cursor_value is None:
        raise ValueError("CHECKPOINT_CONFLICT")
    try:
        cursor = float(cursor_value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("CHECKPOINT_CONFLICT") from exc
    if cursor != expected_cursor:
        raise ValueError("CHECKPOINT_CONFLICT")

    active_window = snapshot.get("active_checkpoint_window")
    if active_window is not None:
        if (
            isinstance(active_window, bool)
            or not isinstance(active_window, int)
            or not 0 <= active_window < len(bounds)
            or active_window != window_index + 1
        ):
            raise ValueError("CHECKPOINT_CONFLICT")
        next_window_index = active_window
    else:
        next_window_index = window_index + 1
    resume_start = max(0.0, cursor - overlap_seconds)
    return window_seconds, overlap_seconds, next_window_index, resume_start


def build_resume_plan(
    snapshot: dict[str, Any],
    segments: list[dict[str, Any]],
    *,
    attempt_id: str,
) -> ResumePlan:
    """Validate durable checkpoint identity and compute the overlap resume window."""
    selection = json.loads(snapshot.get("selection_json") or "{}")
    capability = capability_for(str(selection.get("engine") or ""))
    mode = snapshot.get("checkpoint_mode") or (
        "segment" if capability and capability.mode == "segment" else None
    )
    if capability is None or mode != capability.mode:
        raise ValueError("RESUME_UNSUPPORTED")
    if snapshot.get("status") != "interrupted" or not snapshot.get("resumable"):
        raise ValueError("RESUME_UNSUPPORTED")
    if selection.get("resume_auth_required"):
        raise ValueError("RESUME_AUTH_REQUIRED")
    window_seconds: int | None = None
    overlap_seconds: int | None = None
    next_window_index: int | None = None
    resume_start = 0.0
    if mode == "window":
        window_seconds, overlap_seconds, next_window_index, resume_start = _window_resume_state(
            snapshot, capability
        )
    audio = Path(snapshot["audio_path"])
    output = Path(snapshot["out_path"])
    if not output.parent.is_dir():
        raise ValueError("RESUME_INPUT_MISMATCH")
    if not audio.is_file() or audio_sha256(audio) != snapshot.get("audio_sha256"):
        raise ValueError("RESUME_INPUT_MISMATCH")
    expected = list(range(int(snapshot["committed_segments"])))
    actual = [int(segment["segment_index"]) for segment in segments]
    if actual != expected:
        raise ValueError("CHECKPOINT_CONFLICT")
    if int(snapshot["last_segment_index"]) != len(segments) - 1:
        raise ValueError("CHECKPOINT_CONFLICT")
    maximum: float | None = None
    if snapshot.get("audio_duration") is not None:
        try:
            maximum = float(snapshot["audio_duration"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("CHECKPOINT_CONFLICT") from exc
        if not math.isfinite(maximum) or maximum <= 0:
            raise ValueError("CHECKPOINT_CONFLICT")
    previous_end = 0.0
    for segment in segments:
        if (
            segment.get("decode_params_hash", snapshot["decode_params_hash"])
            != snapshot["decode_params_hash"]
        ):
            raise ValueError("CHECKPOINT_CONFLICT")
        try:
            start, end = validate_segment_timeline(
                segment.get("start"),
                segment.get("end"),
                maximum=maximum,
                cursor_end=previous_end,
            )
            words = segment.get("words_json")
            if words is not None:
                if isinstance(words, str):
                    words = json.loads(words)
                validate_word_timelines(words, segment_start=start, segment_end=end)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("CHECKPOINT_CONFLICT") from exc
        previous_end = end
        if "text_sha256" in segment:
            normalized = " ".join(str(segment["text"]).split())
            if hashlib.sha256(normalized.encode("utf-8")).hexdigest() != segment["text_sha256"]:
                raise ValueError("CHECKPOINT_CONFLICT")
    cursor = float(snapshot["last_segment_end"])
    if segments:
        if abs(cursor - float(segments[-1]["end"])) > 0.05:
            raise ValueError("CHECKPOINT_CONFLICT")
    elif cursor != 0.0:
        raise ValueError("CHECKPOINT_CONFLICT")
    if mode == "segment":
        resume_start = max(0.0, cursor - 1.0)
    return ResumePlan(
        job_id=snapshot["job_id"],
        attempt_id=attempt_id,
        audio_path=audio,
        output_path=output,
        resume_start=resume_start,
        next_segment_index=int(snapshot["last_segment_index"]) + 1,
        decode_params_hash=str(snapshot["decode_params_hash"]),
        mode=mode,
        window_seconds=window_seconds,
        overlap_seconds=overlap_seconds,
        next_window_index=next_window_index,
        detected_language=snapshot.get("detected_language"),
        runtime_auth_required=bool(snapshot.get("runtime_auth_required")),
    )


def validate_resume_decode(plan: ResumePlan, payload: dict[str, Any]) -> str:
    """Reject a resumed attempt whose effective decode inputs changed."""
    actual = decode_params_hash(payload)
    if actual != plan.decode_params_hash:
        raise ValueError("RESUME_INPUT_MISMATCH")
    return actual
