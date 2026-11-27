from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mediascribe.checkpoint import merge_resumed_segment
from mediascribe.web.job_recovery import (
    CheckpointBoundaryConflict,
    audio_sha256,
    build_resume_plan,
    decode_params_hash,
    validate_resume_decode,
)


def test_audio_sha256_is_content_based(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")
    assert audio_sha256(audio) == hashlib.sha256(b"abc").hexdigest()
    audio.write_bytes(b"abd")
    assert audio_sha256(audio) != hashlib.sha256(b"abc").hexdigest()


def test_decode_params_hash_changes_with_decode_inputs():
    base = {"engine": "faster-whisper", "model": "small", "device": "cpu"}
    first = decode_params_hash({**base, "language": "zh", "initial_prompt": "terms"})
    assert first == decode_params_hash({**base, "language": "zh", "initial_prompt": "terms"})
    assert first != decode_params_hash({**base, "language": "en", "initial_prompt": "terms"})
    assert first != decode_params_hash(
        {**base, "model": "base", "language": "zh", "initial_prompt": "terms"}
    )


def _snapshot(tmp_path: Path, audio: Path):
    return {
        "job_id": "j1",
        "attempt_id": "a1",
        "status": "interrupted",
        "finished": 1,
        "resumable": 1,
        "selection_json": '{"engine":"faster-whisper","model":"small"}',
        "audio_path": str(audio),
        "audio_sha256": audio_sha256(audio),
        "decode_params_hash": "params",
        "out_path": str(tmp_path / "out.md"),
        "last_segment_index": 1,
        "last_segment_end": 10.0,
        "committed_segments": 2,
    }


def test_build_resume_plan_uses_overlap_and_next_index(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    segments = [
        {"segment_index": 0, "start": 0.0, "end": 5.0, "text": "one"},
        {"segment_index": 1, "start": 5.0, "end": 10.0, "text": "two"},
    ]
    plan = build_resume_plan(snapshot, segments, attempt_id="a2")
    assert plan.attempt_id == "a2"
    assert plan.resume_start == 9.0
    assert plan.next_segment_index == 2
    assert plan.audio_path == audio
    assert plan.output_path == tmp_path / "out.md"


def test_resume_rejects_segment_identity_or_cursor_drift(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    segments = [
        {"segment_index": 0, "start": 0.0, "end": 5.0, "text": "one"},
        {"segment_index": 1, "start": 5.0, "end": 10.0, "text": "two"},
    ]
    segments[1]["decode_params_hash"] = "different"
    with pytest.raises(ValueError, match="CHECKPOINT_CONFLICT"):
        build_resume_plan(snapshot, segments, attempt_id="a2")

    segments[1]["decode_params_hash"] = snapshot["decode_params_hash"]
    snapshot["last_segment_end"] = 9.0
    with pytest.raises(ValueError, match="CHECKPOINT_CONFLICT"):
        build_resume_plan(snapshot, segments, attempt_id="a2")


def test_resume_rejects_audio_drift(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    audio.write_bytes(b"changed")
    with pytest.raises(ValueError, match="RESUME_INPUT_MISMATCH"):
        build_resume_plan(snapshot, [], attempt_id="a2")


def test_resume_decode_validation_uses_effective_payload(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    snapshot["decode_params_hash"] = decode_params_hash(
        {"initial_prompt": "terms", "compute_type": "int8"}
    )
    snapshot["committed_segments"] = 0
    snapshot["last_segment_index"] = -1
    snapshot["last_segment_end"] = 0.0
    plan = build_resume_plan(snapshot, [], attempt_id="a2")
    assert (
        validate_resume_decode(plan, {"initial_prompt": "terms", "compute_type": "int8"})
        == snapshot["decode_params_hash"]
    )
    with pytest.raises(ValueError, match="RESUME_INPUT_MISMATCH"):
        validate_resume_decode(plan, {"initial_prompt": "different", "compute_type": "int8"})


def test_resume_rejects_redacted_secret_requirement(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    snapshot["selection_json"] = (
        '{"engine":"faster-whisper","model":"small","resume_auth_required":true}'
    )
    with pytest.raises(ValueError, match="RESUME_AUTH_REQUIRED"):
        build_resume_plan(snapshot, [], attempt_id="a2")


def test_build_window_resume_plan_uses_active_or_next_window(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    snapshot.update(
        {
            "selection_json": '{"engine":"whisper","model":"small"}',
            "checkpoint_mode": "window",
            "checkpoint_window_seconds": 60,
            "checkpoint_overlap_seconds": 2,
            "checkpoint_cursor": 60.0,
            "checkpoint_window_index": 0,
            "active_checkpoint_window": None,
            "audio_duration": 119.0,
            "runtime_auth_required": 0,
            "committed_segments": 0,
            "last_segment_index": -1,
            "last_segment_end": 0.0,
        }
    )
    plan = build_resume_plan(snapshot, [], attempt_id="a2")
    assert plan.mode == "window"
    assert plan.next_window_index == 1
    assert plan.resume_start == 58.0
    assert plan.window_seconds == 60
    assert plan.overlap_seconds == 2

    snapshot["active_checkpoint_window"] = 1
    plan = build_resume_plan(snapshot, [], attempt_id="a3")
    assert plan.next_window_index == 1


def test_build_window_resume_plan_rejects_active_marker_after_final_window(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    snapshot.update(
        {
            "selection_json": '{"engine":"whisper","model":"small"}',
            "checkpoint_mode": "window",
            "checkpoint_window_seconds": 60,
            "checkpoint_overlap_seconds": 2,
            "checkpoint_cursor": 119.0,
            "checkpoint_window_index": 2,
            "active_checkpoint_window": 3,
            "audio_duration": 119.0,
            "committed_segments": 0,
            "last_segment_index": -1,
            "last_segment_end": 0.0,
        }
    )

    with pytest.raises(ValueError, match="CHECKPOINT_CONFLICT"):
        build_resume_plan(snapshot, [], attempt_id="a2")


def test_build_window_resume_plan_allows_finalization_after_final_window(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    snapshot.update(
        {
            "selection_json": '{"engine":"whisper","model":"small"}',
            "checkpoint_mode": "window",
            "checkpoint_window_seconds": 60,
            "checkpoint_overlap_seconds": 2,
            "checkpoint_cursor": 119.0,
            "checkpoint_window_index": 2,
            "active_checkpoint_window": None,
            "audio_duration": 119.0,
            "committed_segments": 0,
            "last_segment_index": -1,
            "last_segment_end": 0.0,
        }
    )

    plan = build_resume_plan(snapshot, [], attempt_id="a2")

    assert plan.next_window_index == 3
    assert plan.resume_start == 117.0


def test_build_window_resume_plan_rejects_cursor_drift(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    snapshot.update(
        {
            "selection_json": '{"engine":"whisper-cpp","model":"tiny"}',
            "checkpoint_mode": "window",
            "checkpoint_window_seconds": 60,
            "checkpoint_overlap_seconds": 2,
            "checkpoint_cursor": 61.0,
            "checkpoint_window_index": 0,
            "active_checkpoint_window": None,
            "audio_duration": 119.0,
        }
    )
    with pytest.raises(ValueError, match="CHECKPOINT_CONFLICT"):
        build_resume_plan(snapshot, [], attempt_id="a2")


def test_build_window_resume_plan_preserves_language_and_runtime_auth(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    snapshot.update(
        {
            "selection_json": '{"engine":"whisperx","model":"small"}',
            "checkpoint_mode": "window",
            "checkpoint_window_seconds": 60,
            "checkpoint_overlap_seconds": 2,
            "checkpoint_cursor": 0.0,
            "checkpoint_window_index": -1,
            "active_checkpoint_window": 0,
            "audio_duration": 119.0,
            "detected_language": "zh",
            "runtime_auth_required": 1,
            "committed_segments": 0,
            "last_segment_index": -1,
            "last_segment_end": 0.0,
        }
    )

    plan = build_resume_plan(snapshot, [], attempt_id="a2")

    assert plan.mode == "window"
    assert plan.next_window_index == 0
    assert plan.resume_start == 0.0
    assert plan.detected_language == "zh"
    assert plan.runtime_auth_required is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"checkpoint_window_seconds": 30},
        {"checkpoint_overlap_seconds": 1},
        {"audio_duration": 0.0},
        {"checkpoint_window_index": -2},
        {"checkpoint_window_index": 0, "checkpoint_cursor": 0.0},
        {"active_checkpoint_window": 0},
        {"active_checkpoint_window": 2},
        {"active_checkpoint_window": -1},
        {"checkpoint_window_index": 1},
    ],
)
def test_build_window_resume_plan_rejects_inconsistent_window_state(
    tmp_path: Path, overrides: dict[str, object]
):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    snapshot.update(
        {
            "selection_json": '{"engine":"whisper","model":"small"}',
            "checkpoint_mode": "window",
            "checkpoint_window_seconds": 60,
            "checkpoint_overlap_seconds": 2,
            "checkpoint_cursor": 60.0,
            "checkpoint_window_index": 0,
            "active_checkpoint_window": None,
            "audio_duration": 119.0,
            "committed_segments": 0,
            "last_segment_index": -1,
            "last_segment_end": 0.0,
        }
    )
    snapshot.update(overrides)

    with pytest.raises(ValueError, match="CHECKPOINT_CONFLICT"):
        build_resume_plan(snapshot, [], attempt_id="a2")


@pytest.mark.parametrize(
    ("engine", "mode"),
    [("unknown", "window"), ("whisper", "segment"), ("faster-whisper", "window")],
)
def test_build_resume_plan_rejects_unknown_capability_or_mode_mismatch(
    tmp_path: Path, engine: str, mode: str
):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    snapshot["selection_json"] = json.dumps({"engine": engine})
    snapshot["checkpoint_mode"] = mode

    with pytest.raises(ValueError, match="RESUME_UNSUPPORTED"):
        build_resume_plan(snapshot, [], attempt_id="a2")


def test_legacy_snapshot_without_mode_remains_segment_resume(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    snapshot.update({"committed_segments": 0, "last_segment_index": -1, "last_segment_end": 0.0})
    plan = build_resume_plan(snapshot, [], attempt_id="a2")
    assert plan.mode == "segment"
    assert plan.next_window_index is None


@pytest.mark.parametrize(
    "corruption",
    [
        {"start": -0.01},
        {"start": 8.0, "end": 7.0},
        {"start": 10.0, "end": 10.0},
        {"start": 0.0, "end": 10.06},
        {"words_json": "not-json"},
        {"words_json": '{"word":"bad"}'},
        {"words_json": '["bad"]'},
        {"words_json": '[{"start": -0.01, "end": 0.5}]'},
        {"words_json": '[{"start": 0.5, "end": 0.4}]'},
        {"words_json": '[{"start": 0.0, "end": 10.06}]'},
        {"words_json": '[{"start": NaN, "end": 0.5}]'},
    ],
)
def test_resume_revalidates_every_persisted_segment_and_word(
    tmp_path: Path, corruption: dict[str, object]
):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    snapshot["audio_duration"] = 10.0
    snapshot["last_segment_index"] = 0
    snapshot["last_segment_end"] = 5.0
    snapshot["committed_segments"] = 1
    segment = {
        "segment_index": 0,
        "start": 0.0,
        "end": 5.0,
        "text": "one",
        "decode_params_hash": "params",
        "text_sha256": hashlib.sha256(b"one").hexdigest(),
        "words_json": '[{"word":"one","start":0.0,"end":5.0}]',
    }
    segment.update(corruption)
    if "start" in corruption or "end" in corruption:
        snapshot["last_segment_end"] = float(segment["end"])

    with pytest.raises(ValueError, match="CHECKPOINT_CONFLICT"):
        build_resume_plan(snapshot, [segment], attempt_id="a2")


def test_resume_rejects_backward_final_segment_end(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    snapshot = _snapshot(tmp_path, audio)
    snapshot["audio_duration"] = 10.0
    snapshot["last_segment_end"] = 5.0
    segments = [
        {"segment_index": 0, "start": 0.0, "end": 8.0, "text": "one"},
        {"segment_index": 1, "start": 4.0, "end": 5.0, "text": "two"},
    ]
    with pytest.raises(ValueError, match="CHECKPOINT_CONFLICT"):
        build_resume_plan(snapshot, segments, attempt_id="a2")


def test_boundary_segments_are_discarded_or_deduplicated():
    assert (
        merge_resumed_segment(
            {"start": 8.0, "end": 9.5, "text": "covered"},
            cursor_end=10.0,
            previous_text="two",
        )
        is None
    )
    merged = merge_resumed_segment(
        {"start": 9.0, "end": 12.0, "text": "two next"},
        cursor_end=10.0,
        previous_text="two",
    )
    assert merged["text"] == "next"
    punctuated = merge_resumed_segment(
        {"start": 9.0, "end": 12.0, "text": "two， next"},
        cursor_end=10.0,
        previous_text="two",
    )
    assert punctuated["text"] == "next"
    with pytest.raises(CheckpointBoundaryConflict):
        merge_resumed_segment(
            {"start": 9.0, "end": 12.0, "text": "unrelated"},
            cursor_end=10.0,
            previous_text="two",
        )


def test_neutral_boundary_merge_is_the_single_fail_closed_authority():
    from mediascribe.web import job_recovery

    assert job_recovery.merge_resumed_segment is merge_resumed_segment
    with pytest.raises(ValueError, match="timestamp"):
        job_recovery.merge_resumed_segment(
            {"start": -1.0, "end": 12.0, "text": "two next"},
            cursor_end=10.0,
            previous_text="two",
        )
