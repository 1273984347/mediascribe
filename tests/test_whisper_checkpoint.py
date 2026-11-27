from __future__ import annotations

from importlib import metadata
from pathlib import Path
from unittest import mock

import pytest

from mediascribe.checkpoint import (
    CheckpointCapability,
    CheckpointCursor,
    CheckpointSession,
    CheckpointWindow,
)
from mediascribe.transcribers.whisper import WhisperTranscriber
from mediascribe.zh_normalize import SIMPLIFIED_ZH_PROMPT


def _record_window_commit(
    committed: list[tuple[int, float, float, str | None]],
):
    def commit(index: int, start: float, end: float, detected_language: str | None) -> None:
        committed.append((index, start, end, detected_language))

    return commit


def test_whisper_window_adapter_commits_only_complete_window_results(tmp_path: Path):
    transcriber = WhisperTranscriber(model="small", device="cpu")
    model = mock.Mock()
    model.transcribe.return_value = {
        "text": " hello ",
        "language": "zh",
        "segments": [{"text": "hello", "start": 1.0, "end": 3.0}],
    }
    committed: list[dict] = []
    windows_committed: list[tuple[int, float, float, str | None]] = []
    session = CheckpointSession(
        capability=CheckpointCapability("whisper", "window", 60, 2),
        prior_segments=[],
        cursor=None,
        segment_cb=committed.append,
        window_committed_cb=_record_window_commit(windows_committed),
    )
    window = tmp_path / "window.wav"
    window.write_bytes(b"wav")

    with (
        mock.patch.object(transcriber, "_ensure_model", return_value=model),
        mock.patch("mediascribe.checkpoint.probe_duration_seconds", return_value=60.0),
        mock.patch(
            "mediascribe.checkpoint.iter_checkpoint_windows",
            return_value=iter([CheckpointWindow(0, 0.0, 60.0, window)]),
        ),
    ):
        result = transcriber.transcribe(
            tmp_path / "audio.wav",
            language="zh",
            checkpoint_session=session,
        )

    assert result["segments"] == [{"text": "hello", "start": 1.0, "end": 3.0}]
    assert committed == result["segments"]
    assert windows_committed == [(0, 0.0, 60.0, "zh")]
    model.transcribe.assert_called_once()
    assert model.transcribe.call_args.args == (str(window),)
    assert model.transcribe.call_args.kwargs["language"] == "zh"
    assert "clip_timestamps" not in model.transcribe.call_args.kwargs


def test_whisper_window_adapter_does_not_commit_incomplete_window_results(tmp_path: Path):
    transcriber = WhisperTranscriber(model="small", device="cpu")
    model = mock.Mock()
    model.transcribe.return_value = {
        "text": "hello",
        "language": "zh",
        "segments": [{"text": "hello", "start": 1.0, "end": 61.0}],
    }
    committed: list[dict] = []
    windows_committed: list[tuple[int, float, float, str | None]] = []
    session = CheckpointSession(
        capability=CheckpointCapability("whisper", "window", 60, 2),
        prior_segments=[],
        cursor=None,
        segment_cb=committed.append,
        window_committed_cb=_record_window_commit(windows_committed),
    )
    window = tmp_path / "window.wav"
    window.write_bytes(b"wav")

    with (
        mock.patch.object(transcriber, "_ensure_model", return_value=model),
        mock.patch("mediascribe.checkpoint.probe_duration_seconds", return_value=60.0),
        mock.patch(
            "mediascribe.checkpoint.iter_checkpoint_windows",
            return_value=iter([CheckpointWindow(0, 0.0, 60.0, window)]),
        ),
        pytest.raises(ValueError, match="exceeds window duration"),
    ):
        transcriber.transcribe(tmp_path / "audio.wav", checkpoint_session=session)

    assert committed == []
    assert windows_committed == []


def test_whisper_window_adapter_rejects_non_window_session(tmp_path: Path):
    transcriber = WhisperTranscriber(model="small", device="cpu")
    model = mock.Mock()
    session = CheckpointSession(
        capability=CheckpointCapability("whisper", "segment"),
        prior_segments=[],
        cursor=None,
        segment_cb=None,
    )

    with (
        mock.patch.object(transcriber, "_ensure_model", return_value=model),
        pytest.raises(ValueError, match="requires a window checkpoint session"),
    ):
        transcriber.transcribe(tmp_path / "audio.wav", checkpoint_session=session)

    model.transcribe.assert_not_called()


def test_whisper_window_adapter_reuses_resumed_detected_language(tmp_path: Path):
    transcriber = WhisperTranscriber(model="small", device="cpu")
    model = mock.Mock()
    model.transcribe.side_effect = [
        {
            "text": "next",
            "language": "zh",
            "segments": [{"text": "next", "start": 0.0, "end": 1.0}],
        },
        {
            "text": "last",
            "language": "zh",
            "segments": [{"text": "last", "start": 1.0, "end": 2.0}],
        },
    ]
    windows_committed: list[tuple[int, float, float, str | None]] = []
    session = CheckpointSession(
        capability=CheckpointCapability("whisper", "window", 60, 2),
        prior_segments=[{"text": "before", "start": 55.0, "end": 58.0}],
        cursor=CheckpointCursor(
            "window",
            58.0,
            58.0,
            "before",
            next_window_index=1,
            detected_language="zh",
        ),
        segment_cb=None,
        window_committed_cb=_record_window_commit(windows_committed),
    )
    windows = [
        CheckpointWindow(1, 58.0, 118.0, tmp_path / "window-1.wav"),
        CheckpointWindow(2, 116.0, 174.0, tmp_path / "window-2.wav"),
    ]
    for window in windows:
        window.path.write_bytes(b"wav")

    with (
        mock.patch.object(transcriber, "_ensure_model", return_value=model),
        mock.patch("mediascribe.checkpoint.probe_duration_seconds", return_value=174.0),
        mock.patch("mediascribe.checkpoint.iter_checkpoint_windows", return_value=iter(windows)),
    ):
        result = transcriber.transcribe(tmp_path / "audio.wav", checkpoint_session=session)

    assert [segment["text"] for segment in result["segments"]] == ["before", "next", "last"]
    assert [call.kwargs["language"] for call in model.transcribe.call_args_list] == ["zh", "zh"]
    assert windows_committed == [(1, 58.0, 118.0, "zh"), (2, 116.0, 174.0, "zh")]


def test_whisper_without_session_preserves_whole_file_transcription(tmp_path: Path):
    transcriber = WhisperTranscriber(model="small", device="cpu")
    model = mock.Mock()
    model.transcribe.return_value = {
        "text": " hello ",
        "language": "zh",
        "segments": [{"text": "hello", "start": 1.0, "end": 3.0}],
    }
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"wav")

    with (
        mock.patch.object(transcriber, "_ensure_model", return_value=model),
        mock.patch(
            "mediascribe.transcribers.whisper.run_windowed_transcription",
            side_effect=AssertionError("whole-file transcription must not use windows"),
        ),
    ):
        result = transcriber.transcribe(audio, language="zh")

    assert result == {
        "text": "hello",
        "segments": [{"text": "hello", "start": 1.0, "end": 3.0}],
        "language": "zh",
        "model": "small",
    }
    model.transcribe.assert_called_once_with(
        str(audio),
        verbose=False,
        fp16=False,
        language="zh",
        initial_prompt=SIMPLIFIED_ZH_PROMPT,
    )


def test_whisper_effective_decode_params_resolve_auto_device_without_loading_model():
    transcriber = WhisperTranscriber(model="small", device=None)

    with (
        mock.patch.object(transcriber, "_ensure_model", side_effect=AssertionError("model loaded")),
        mock.patch("torch.cuda.is_available", return_value=True),
    ):
        payload = transcriber.effective_decode_params(language=None, initial_prompt=None)

    assert payload["device"] == "cuda"


def test_whisper_effective_decode_params_describe_runtime_and_defaults():
    transcriber = WhisperTranscriber(model="small", device="cpu")

    with mock.patch.object(metadata, "version", return_value="20250625") as version:
        payload = transcriber.effective_decode_params(
            language="zh", initial_prompt=SIMPLIFIED_ZH_PROMPT
        )

    version.assert_called_once_with("openai-whisper")
    assert payload == {
        "engine": "whisper",
        "package_version": "20250625",
        "model": "small",
        "device": "cpu",
        "language": "zh",
        "initial_prompt": SIMPLIFIED_ZH_PROMPT,
        "temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        "condition_on_previous_text": True,
        "window_seconds": 60,
        "overlap_seconds": 2,
    }


def test_whisper_effective_decode_params_fall_back_only_when_metadata_is_missing():
    transcriber = WhisperTranscriber(model="small", device="cpu")

    with mock.patch.object(
        metadata, "version", side_effect=metadata.PackageNotFoundError("openai-whisper")
    ):
        payload = transcriber.effective_decode_params(language=None, initial_prompt=None)

    assert payload["package_version"] == "unknown"
    assert payload["language"] is None
    assert payload["initial_prompt"] is None

    with mock.patch.object(metadata, "version", side_effect=RuntimeError("metadata failure")):
        with pytest.raises(RuntimeError, match="metadata failure"):
            transcriber.effective_decode_params(language=None, initial_prompt=None)
