from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mediascribe.checkpoint import (
    OVERLAP_SECONDS,
    WINDOW_SECONDS,
    CheckpointBoundaryConflict,
    CheckpointCapability,
    CheckpointCursor,
    CheckpointSession,
    CheckpointWindow,
    capability_for,
    checkpoint_window_bounds,
    iter_checkpoint_windows,
    merge_resumed_segment,
)


def test_window_bounds_are_deterministic_and_clamp_tail():
    assert (WINDOW_SECONDS, OVERLAP_SECONDS) == (60, 2)
    assert checkpoint_window_bounds(119.0, 60, 2) == [
        (0, 0.0, 60.0),
        (1, 58.0, 118.0),
        (2, 116.0, 119.0),
    ]
    assert checkpoint_window_bounds(30.0, 60, 2) == [(0, 0.0, 30.0)]


def test_window_bounds_stop_when_clamped_window_reaches_duration():
    assert checkpoint_window_bounds(118.0, 60, 2) == [
        (0, 0.0, 60.0),
        (1, 58.0, 118.0),
    ]


def test_capabilities_cover_all_engines_without_accepting_unknown_names():
    assert capability_for("faster-whisper").mode == "segment"
    for engine in ("whisper", "whisperx", "whisper-cpp"):
        capability = capability_for(engine)
        assert capability is not None
        assert capability.mode == "window"
        assert capability.window_seconds == 60
        assert capability.overlap_seconds == 2
    assert capability_for("unknown") is None


def test_session_keeps_prefix_and_deduplicates_overlap():
    capability = CheckpointCapability("whisper", "window", 60, 2)
    cursor = CheckpointCursor(
        mode="window",
        resume_start=58.0,
        last_segment_end=60.0,
        previous_text="two",
        next_window_index=1,
    )
    committed: list[dict] = []
    session = CheckpointSession(
        capability=capability,
        prior_segments=[{"text": "one", "start": 0.0, "end": 30.0}],
        cursor=cursor,
        segment_cb=committed.append,
    )

    assert session.accept({"text": "covered", "start": 58.0, "end": 59.0}) is None
    accepted = session.accept({"text": "two next", "start": 59.0, "end": 62.0})
    assert accepted == {"text": "next", "start": 59.0, "end": 62.0}
    assert [segment["text"] for segment in session.segments] == ["one", "next"]
    assert committed == [accepted]


def test_session_rejects_non_deterministic_boundary_text():
    session = CheckpointSession(
        capability=CheckpointCapability("whisper", "window", 60, 2),
        prior_segments=[],
        cursor=CheckpointCursor("window", 9.0, 10.0, "two"),
        segment_cb=lambda segment: None,
    )
    with pytest.raises(CheckpointBoundaryConflict):
        session.accept({"text": "unrelated", "start": 9.0, "end": 12.0})


def test_session_rejects_overlap_stripping_without_previous_text():
    session = CheckpointSession(
        capability=CheckpointCapability("whisper", "window", 60, 2),
        prior_segments=[],
        cursor=CheckpointCursor("window", 9.0, 10.0, "，. "),
        segment_cb=lambda segment: None,
    )

    with pytest.raises(CheckpointBoundaryConflict):
        session.accept({"text": "unverified", "start": 9.0, "end": 12.0})

    assert session.segments == []


@pytest.mark.parametrize("previous_text", ["", " ，. "])
def test_merge_rejects_overlap_stripping_without_previous_text(previous_text: str):
    with pytest.raises(CheckpointBoundaryConflict):
        merge_resumed_segment(
            {"text": "unverified", "start": 9.0, "end": 12.0},
            cursor_end=10.0,
            previous_text=previous_text,
        )


def test_session_rejects_cursor_capability_mode_mismatch():
    with pytest.raises(ValueError, match="mode"):
        CheckpointSession(
            capability=CheckpointCapability("whisper", "window", 60, 2),
            prior_segments=[],
            cursor=CheckpointCursor("segment", 0.0, 10.0, "old"),
            segment_cb=None,
        )


@pytest.mark.parametrize(
    ("resume_start", "last_segment_end"),
    [(-1.0, 0.0), (float("nan"), 1.0), (0.0, float("inf"))],
)
def test_session_rejects_invalid_cursor_timestamps(resume_start: float, last_segment_end: float):
    with pytest.raises(ValueError, match="timestamp"):
        CheckpointSession(
            capability=CheckpointCapability("whisper", "window", 60, 2),
            prior_segments=[],
            cursor=CheckpointCursor("window", resume_start, last_segment_end, "old"),
            segment_cb=None,
        )


@pytest.mark.parametrize("next_window_index", [-1, 1.5])
def test_session_rejects_invalid_cursor_window_index(next_window_index: Any):
    with pytest.raises(ValueError, match="index"):
        CheckpointSession(
            capability=CheckpointCapability("whisper", "window", 60, 2),
            prior_segments=[],
            cursor=CheckpointCursor("window", 0.0, 0.0, "", next_window_index),
            segment_cb=None,
        )


@pytest.mark.parametrize(
    ("candidate", "cursor_end"),
    [
        ({"text": "covered", "start": -1.0, "end": 0.0}, 0.0),
        ({"text": "covered", "start": 0.0, "end": float("nan")}, 0.0),
        ({"text": "new", "start": 0.0, "end": float("inf")}, 0.0),
        ({"text": "new", "start": 2.0, "end": 1.0}, 0.0),
        ({"text": "covered", "start": 0.0, "end": 0.0}, -1.0),
        ({"text": "covered", "start": 0.0, "end": 0.0}, float("nan")),
        ({"text": "covered", "start": 0.0, "end": 0.0}, float("inf")),
    ],
)
def test_merge_validates_timestamps_before_deduplication(
    candidate: dict[str, Any], cursor_end: float
):
    with pytest.raises(ValueError, match="timestamp"):
        merge_resumed_segment(candidate, cursor_end=cursor_end, previous_text="")


def test_session_callback_failure_does_not_advance_local_cursor():
    def fail(_segment):
        raise RuntimeError("injected commit failure")

    session = CheckpointSession(
        capability=CheckpointCapability("whisper", "window", 60, 2),
        prior_segments=[],
        cursor=None,
        segment_cb=fail,
    )
    with pytest.raises(RuntimeError, match="injected commit failure"):
        session.accept({"text": "one", "start": 0.0, "end": 1.0})
    assert session.segments == []


def test_window_executor_starts_at_resume_index_and_marks_only_complete_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import mediascribe.checkpoint as checkpoint

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    windows = [
        checkpoint.CheckpointWindow(0, 0.0, 60.0, tmp_path / "0.wav"),
        checkpoint.CheckpointWindow(1, 58.0, 118.0, tmp_path / "1.wav"),
    ]

    def iter_windows(
        _path: Path,
        *,
        duration: float,
        window_seconds: int,
        overlap_seconds: int,
        start_index: int,
    ):
        assert (duration, window_seconds, overlap_seconds, start_index) == (118.0, 60, 2, 1)
        return iter(windows[start_index:])

    monkeypatch.setattr(checkpoint, "probe_duration_seconds", lambda path: 118.0)
    monkeypatch.setattr(checkpoint, "iter_checkpoint_windows", iter_windows)
    started: list[tuple[int, float, float]] = []
    committed: list[tuple[int, float, float, str | None]] = []

    def window_committed(
        index: int, start: float, end: float, detected_language: str | None
    ) -> None:
        committed.append((index, start, end, detected_language))

    session = CheckpointSession(
        capability=CheckpointCapability("whisper", "window", 60, 2),
        prior_segments=[{"text": "old", "start": 0.0, "end": 10.0}],
        cursor=CheckpointCursor("window", 0.0, 10.0, "old", next_window_index=1),
        segment_cb=lambda segment: None,
        window_started_cb=lambda *args: started.append(args),
        window_committed_cb=window_committed,
    )

    def transcribe_window(path: Path, duration: float) -> dict[str, Any]:
        assert path == tmp_path / "1.wav"
        assert duration == 60.0
        return {"language": "zh", "segments": [{"text": "new", "start": 1.0, "end": 3.0}]}

    result = checkpoint.run_windowed_transcription(
        audio,
        capability=session.capability,
        session=session,
        transcribe_window=transcribe_window,
    )

    assert [segment["text"] for segment in result["segments"]] == ["old", "new"]
    assert result["segments"][-1]["start"] == 59.0
    assert started == [(1, 58.0, 118.0)]
    assert committed == [(1, 58.0, 118.0, "zh")]


def _run_window_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows: list[CheckpointWindow],
    transcription: dict[str, Any],
    *,
    started: list[tuple[int, float, float]],
    committed: list[tuple[int, float, float, str | None]],
    segment_committed: list[dict[str, Any]],
    transcribed: list[tuple[Path, float]],
    cursor: CheckpointCursor | None = None,
) -> dict[str, Any]:
    import mediascribe.checkpoint as checkpoint

    audio = tmp_path / "audio.wav"
    monkeypatch.setattr(checkpoint, "probe_duration_seconds", lambda path: 119.0)
    monkeypatch.setattr(
        checkpoint,
        "iter_checkpoint_windows",
        lambda *args, **kwargs: iter(windows),
    )
    session = CheckpointSession(
        capability=CheckpointCapability("whisper", "window", 60, 2),
        prior_segments=[],
        cursor=cursor,
        segment_cb=segment_committed.append,
        window_started_cb=lambda *args: started.append(args),
        window_committed_cb=lambda *args: committed.append(args),
    )

    def transcribe_window(path: Path, duration: float) -> dict[str, Any]:
        transcribed.append((path, duration))
        return transcription

    return checkpoint.run_windowed_transcription(
        audio,
        capability=session.capability,
        session=session,
        transcribe_window=transcribe_window,
    )


@pytest.mark.parametrize(
    ("bounds", "expected_transcriptions"),
    [
        ([(1, 58.0, 118.0)], 0),
        ([(0, 0.0, 60.0), (0, 0.0, 60.0)], 1),
        ([(0, 0.0, 60.0), (2, 116.0, 119.0)], 1),
        ([(1, 0.0, 60.0)], 0),
        ([(0, 1.0, 60.0)], 0),
        ([(0, 0.0, 59.0)], 0),
    ],
)
def test_window_executor_rejects_invalid_window_stream_before_window_callbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bounds: list[tuple[int, float, float]],
    expected_transcriptions: int,
):
    windows = [
        CheckpointWindow(index, start, end, tmp_path / f"{position}.wav")
        for position, (index, start, end) in enumerate(bounds)
    ]
    started: list[tuple[int, float, float]] = []
    committed: list[tuple[int, float, float, str | None]] = []
    segment_committed: list[dict[str, Any]] = []
    transcribed: list[tuple[Path, float]] = []

    with pytest.raises(ValueError, match="window"):
        _run_window_executor(
            tmp_path,
            monkeypatch,
            windows,
            {"segments": [{"text": "new", "start": 1.0, "end": 3.0}]},
            started=started,
            committed=committed,
            segment_committed=segment_committed,
            transcribed=transcribed,
        )

    assert len(transcribed) == expected_transcriptions
    assert len(started) == expected_transcriptions
    assert len(committed) == expected_transcriptions
    assert len(segment_committed) == expected_transcriptions


def test_window_executor_forwards_unknown_detected_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    windows = [CheckpointWindow(0, 0.0, 60.0, tmp_path / "0.wav")]
    started: list[tuple[int, float, float]] = []
    committed: list[tuple[int, float, float, str | None]] = []
    segment_committed: list[dict[str, Any]] = []
    transcribed: list[tuple[Path, float]] = []

    result = _run_window_executor(
        tmp_path,
        monkeypatch,
        windows,
        {"segments": []},
        started=started,
        committed=committed,
        segment_committed=segment_committed,
        transcribed=transcribed,
    )

    assert result["language"] is None
    assert committed == [(0, 0.0, 60.0, None)]


def test_window_executor_preserves_cursor_language_when_engine_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    windows = [CheckpointWindow(1, 58.0, 118.0, tmp_path / "1.wav")]
    started: list[tuple[int, float, float]] = []
    committed: list[tuple[int, float, float, str | None]] = []
    segment_committed: list[dict[str, Any]] = []
    transcribed: list[tuple[Path, float]] = []
    cursor = CheckpointCursor("window", 58.0, 0.0, "", next_window_index=1, detected_language="zh")

    result = _run_window_executor(
        tmp_path,
        monkeypatch,
        windows,
        {"language": None, "segments": []},
        started=started,
        committed=committed,
        segment_committed=segment_committed,
        transcribed=transcribed,
        cursor=cursor,
    )

    assert result["language"] == "zh"
    assert committed == [(1, 58.0, 118.0, "zh")]


@pytest.mark.parametrize("location", ["segment", "word"])
@pytest.mark.parametrize(
    ("start", "end"),
    [
        (-0.01, 1.0),
        (float("nan"), 1.0),
        (0.0, float("inf")),
        (2.0, 1.0),
        (0.0, 60.051),
    ],
)
def test_window_executor_rejects_invalid_local_timestamps_before_offset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    location: str,
    start: float,
    end: float,
):
    windows = [CheckpointWindow(1, 58.0, 118.0, tmp_path / "1.wav")]
    segment = {"text": "new", "start": 1.0, "end": 3.0}
    if location == "segment":
        segment.update(start=start, end=end)
    else:
        segment["words"] = [{"word": "new", "start": start, "end": end}]
    started: list[tuple[int, float, float]] = []
    committed: list[tuple[int, float, float, str | None]] = []
    segment_committed: list[dict[str, Any]] = []
    transcribed: list[tuple[Path, float]] = []

    with pytest.raises(ValueError, match="timestamp"):
        _run_window_executor(
            tmp_path,
            monkeypatch,
            windows,
            {"segments": [segment]},
            started=started,
            committed=committed,
            segment_committed=segment_committed,
            transcribed=transcribed,
            cursor=CheckpointCursor("window", 58.0, 0.0, "", next_window_index=1),
        )

    assert segment_committed == []
    assert committed == []


def test_window_executor_validates_all_segments_before_committing_any(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    windows = [CheckpointWindow(0, 0.0, 60.0, tmp_path / "0.wav")]
    started: list[tuple[int, float, float]] = []
    committed: list[tuple[int, float, float, str | None]] = []
    segment_committed: list[dict[str, Any]] = []
    transcribed: list[tuple[Path, float]] = []

    with pytest.raises(ValueError, match="timestamp"):
        _run_window_executor(
            tmp_path,
            monkeypatch,
            windows,
            {
                "segments": [
                    {"text": "valid", "start": 0.0, "end": 1.0},
                    {"text": "invalid", "start": float("nan"), "end": 2.0},
                ]
            },
            started=started,
            committed=committed,
            segment_committed=segment_committed,
            transcribed=transcribed,
        )

    assert segment_committed == []
    assert committed == []


def test_window_executor_accepts_duration_tolerance_and_offsets_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    windows = [CheckpointWindow(1, 58.0, 118.0, tmp_path / "1.wav")]
    started: list[tuple[int, float, float]] = []
    committed: list[tuple[int, float, float, str | None]] = []
    segment_committed: list[dict[str, Any]] = []
    transcribed: list[tuple[Path, float]] = []

    result = _run_window_executor(
        tmp_path,
        monkeypatch,
        windows,
        {
            "segments": [
                {
                    "text": "tail",
                    "start": 1.0,
                    "end": 60.05,
                    "words": [{"word": "tail", "start": 1.0, "end": 60.05}],
                }
            ]
        },
        started=started,
        committed=committed,
        segment_committed=segment_committed,
        transcribed=transcribed,
        cursor=CheckpointCursor("window", 58.0, 0.0, "", next_window_index=1),
    )

    assert result["segments"] == [
        {
            "text": "tail",
            "start": 59.0,
            "end": 118.05,
            "words": [{"word": "tail", "start": 59.0, "end": 118.05}],
        }
    ]


def test_window_executor_cancellation_between_windows_is_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import threading

    import mediascribe.checkpoint as checkpoint
    from mediascribe.pipeline_stages import PipelineCancelled

    audio = tmp_path / "audio.wav"
    cancel_event = threading.Event()
    windows = [
        CheckpointWindow(0, 0.0, 60.0, tmp_path / "0.wav"),
        CheckpointWindow(1, 58.0, 118.0, tmp_path / "1.wav"),
    ]
    committed: list[dict] = []
    completed: list[int] = []
    session = CheckpointSession(
        capability=CheckpointCapability("whisper", "window", 60, 2),
        prior_segments=[],
        cursor=None,
        segment_cb=committed.append,
        window_committed_cb=lambda index, *_args: (completed.append(index), cancel_event.set()),
    )
    monkeypatch.setattr(checkpoint, "probe_duration_seconds", lambda _path: 118.0)
    monkeypatch.setattr(checkpoint, "iter_checkpoint_windows", lambda *_a, **_kw: iter(windows))

    with pytest.raises(PipelineCancelled):
        checkpoint.run_windowed_transcription(
            audio,
            capability=session.capability,
            session=session,
            transcribe_window=lambda *_a: {"segments": [{"text": "one", "start": 0.0, "end": 1.0}]},
            cancel_event=cancel_event,
        )

    assert completed == [0]
    assert len(committed) == 1


@pytest.mark.parametrize("start_index", [-1, 3, 4])
def test_iter_checkpoint_windows_rejects_invalid_start_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    start_index: int,
):
    import mediascribe.checkpoint as checkpoint

    ffmpeg_calls: list[list[str]] = []
    monkeypatch.setattr(checkpoint.shutil, "which", lambda executable: "ffmpeg")
    monkeypatch.setattr(
        checkpoint.subprocess,
        "run",
        lambda command, **kwargs: ffmpeg_calls.append(command),
    )

    with pytest.raises(ValueError, match="index"):
        list(
            iter_checkpoint_windows(
                tmp_path / "audio.wav",
                duration=119.0,
                window_seconds=60,
                overlap_seconds=2,
                start_index=start_index,
            )
        )

    assert ffmpeg_calls == []
