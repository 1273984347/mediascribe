from __future__ import annotations

from copy import deepcopy
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
from mediascribe.transcribers import whisperx as wx
from mediascribe.transcribers.whisperx import WhisperXTranscriber
from mediascribe.zh_normalize import SIMPLIFIED_ZH_PROMPT


@pytest.fixture(autouse=True)
def _clear_whisperx_cache():
    wx.clear_model_cache()
    yield
    wx.clear_model_cache()


def _record_window_commit(
    committed: list[tuple[int, float, float, str | None]],
):
    def commit(index: int, start: float, end: float, detected_language: str | None) -> None:
        committed.append((index, start, end, detected_language))

    return commit


def test_whisperx_checkpoint_commits_global_words_and_reruns_diarization(tmp_path: Path):
    fake = mock.MagicMock()
    model = mock.Mock()
    model.transcribe.return_value = {
        "language": "zh",
        "segments": [{"text": "hello", "start": 1.0, "end": 3.0}],
    }
    fake.load_model.return_value = model
    fake.load_audio.return_value = "window-audio"
    fake.load_align_model.return_value = ("align", {"language": "zh"})
    fake.align.return_value = {
        "language": "zh",
        "segments": [
            {
                "text": "hello",
                "start": 1.0,
                "end": 3.0,
                "words": [{"word": "hello", "start": 1.0, "end": 3.0, "score": 0.9}],
            }
        ],
    }
    diarize = mock.Mock(return_value="diarize-result")
    fake.DiarizationPipeline.return_value = diarize

    def assign_speakers(_diarize_result, result):
        result["segments"][-1]["speaker"] = "SPEAKER_00"
        result["segments"][-1]["words"][0]["speaker"] = "SPEAKER_00"
        return result

    fake.assign_word_speakers.side_effect = assign_speakers
    transcriber = WhisperXTranscriber(
        model="small",
        device="cpu",
        hf_token="runtime-secret",
        diarization=True,
        allow_model_download=True,
    )
    prior = {
        "text": "before",
        "start": 0.0,
        "end": 10.0,
        "words": [{"word": "before", "start": 0.0, "end": 9.0, "score": 0.8}],
    }
    committed: list[dict] = []
    windows: list[tuple[int, float, float, str | None]] = []
    session = CheckpointSession(
        capability=CheckpointCapability("whisperx", "window", 60, 2, "whisperx-diarization", True),
        prior_segments=[prior],
        cursor=CheckpointCursor(
            "window",
            58.0,
            10.0,
            "before",
            next_window_index=1,
            detected_language="zh",
        ),
        segment_cb=lambda segment: committed.append(deepcopy(segment)),
        window_committed_cb=_record_window_commit(windows),
    )
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"wav")
    window = tmp_path / "window.wav"
    window.write_bytes(b"wav")

    with (
        mock.patch.dict("sys.modules", {"whisperx": fake}),
        mock.patch("mediascribe.checkpoint.probe_duration_seconds", return_value=118.0),
        mock.patch(
            "mediascribe.checkpoint.iter_checkpoint_windows",
            return_value=iter([CheckpointWindow(1, 58.0, 118.0, window)]),
        ),
    ):
        result = transcriber.transcribe(
            audio_path,
            prompt="domain terms",
            checkpoint_session=session,
        )

    assert fake.load_model.call_args.kwargs["asr_options"] == {"initial_prompt": "domain terms"}
    model.transcribe.assert_called_once_with("window-audio", batch_size=16, language="zh")
    fake.align.assert_called_once_with(
        model.transcribe.return_value["segments"],
        "align",
        {"language": "zh"},
        "window-audio",
        "cpu",
    )
    assert [segment["text"] for segment in result["segments"]] == ["before", "hello"]
    assert result["segments"][-1]["start"] == 59.0
    assert result["segments"][-1]["end"] == 61.0
    assert result["segments"][-1]["words"][0]["start"] == 59.0
    assert result["segments"][-1]["words"][0]["end"] == 61.0
    assert committed == [
        {
            "text": "hello",
            "start": 59.0,
            "end": 61.0,
            "words": [{"word": "hello", "start": 59.0, "end": 61.0, "score": 0.9}],
        }
    ]
    assert committed[0]["words"][0]["word"] == "hello"
    assert windows == [(1, 58.0, 118.0, "zh")]
    assert fake.assign_word_speakers.call_args.args[1]["word_segments"] == [
        prior["words"][0],
        result["segments"][-1]["words"][0],
    ]
    diarize.assert_called_once_with(str(audio_path))
    fake.assign_word_speakers.assert_called_once()
    assert fake.assign_word_speakers.call_args.args[0] == "diarize-result"
    assert len(fake.assign_word_speakers.call_args.args[1]["segments"]) == 2
    assert result["speaker_diarization"] is True
    assert "runtime-secret" not in repr(session.segments)


def test_whisperx_first_aligned_window_preserves_asr_language(tmp_path: Path):
    fake = mock.MagicMock()
    model = mock.Mock()
    model.transcribe.return_value = {
        "language": "zh",
        "segments": [{"text": "hello", "start": 1.0, "end": 3.0}],
    }
    fake.load_model.return_value = model
    fake.load_audio.return_value = "window-audio"
    fake.load_align_model.return_value = ("align", {"language": "zh"})
    # WhisperX 3.8.6 align() returns neither text nor language.
    fake.align.return_value = {
        "segments": [
            {
                "text": "hello",
                "start": 1.0,
                "end": 3.0,
                "words": [{"word": "hello", "start": 1.0, "end": 3.0, "score": 0.9}],
            }
        ],
        "word_segments": [{"word": "hello", "start": 1.0, "end": 3.0, "score": 0.9}],
    }
    transcriber = WhisperXTranscriber(model="small", device="cpu", allow_model_download=True)
    windows: list[tuple[int, float, float, str | None]] = []
    session = CheckpointSession(
        capability=CheckpointCapability("whisperx", "window", 60, 2),
        prior_segments=[],
        cursor=None,
        segment_cb=None,
        window_committed_cb=_record_window_commit(windows),
    )
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"wav")
    window = tmp_path / "window.wav"
    window.write_bytes(b"wav")

    with (
        mock.patch.dict("sys.modules", {"whisperx": fake}),
        mock.patch("mediascribe.checkpoint.probe_duration_seconds", return_value=60.0),
        mock.patch(
            "mediascribe.checkpoint.iter_checkpoint_windows",
            return_value=iter([CheckpointWindow(0, 0.0, 60.0, window)]),
        ),
    ):
        result = transcriber.transcribe(audio_path, language="zh", checkpoint_session=session)

    assert windows == [(0, 0.0, 60.0, "zh")]
    assert result["language"] == "zh"
    assert result["segments"] == fake.align.return_value["segments"]


def test_whisperx_whole_file_loads_prompt_into_model_options(tmp_path: Path):
    fake = mock.MagicMock()
    model = mock.Mock()
    model.transcribe.return_value = {"language": "en", "segments": []}
    fake.load_model.return_value = model
    fake.load_audio.return_value = "audio"
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"wav")
    transcriber = WhisperXTranscriber(model="small", device="cpu", allow_model_download=True)

    with mock.patch.dict("sys.modules", {"whisperx": fake}):
        result = transcriber.transcribe(audio_path, language="en", prompt="domain terms")

    assert fake.load_model.call_args.kwargs["asr_options"] == {"initial_prompt": "domain terms"}
    model.transcribe.assert_called_once_with("audio", batch_size=16)
    fake.align.assert_not_called()
    assert result == {
        "text": "",
        "segments": [],
        "language": "en",
        "model": "small",
        "speaker_diarization": False,
    }


def test_whisperx_prepare_forwards_initial_prompt_to_model_loader():
    fake = mock.MagicMock()
    model = mock.Mock()
    fake.load_model.return_value = model
    transcriber = WhisperXTranscriber(model="small", device="cpu", allow_model_download=True)

    with mock.patch.dict("sys.modules", {"whisperx": fake}):
        prepared = transcriber.prepare(language="zh", initial_prompt="domain terms")

    assert prepared is model
    assert fake.load_model.call_args.kwargs["asr_options"] == {"initial_prompt": "domain terms"}


def test_whisperx_effective_decode_params_describe_runtime_and_loader_defaults():
    transcriber = WhisperXTranscriber(
        model="small",
        device="cpu",
        hf_token="runtime-secret",
        diarization=True,
        allow_model_download=True,
    )

    with mock.patch.object(metadata, "version", return_value="3.8.6") as version:
        payload = transcriber.effective_decode_params(language="zh", initial_prompt="domain terms")

    version.assert_called_once_with("whisperx")
    assert payload == {
        "engine": "whisperx",
        "package_version": "3.8.6",
        "model": "small",
        "device": "cpu",
        "compute_type": "int8",
        "language": "zh",
        "initial_prompt": "domain terms",
        "batch_size": 16,
        "vad_method": "pyannote",
        "vad_options": {
            "chunk_size": 30,
            "vad_onset": 0.500,
            "vad_offset": 0.363,
        },
        "diarization": True,
        "window_seconds": 60,
        "overlap_seconds": 2,
    }
    assert "runtime-secret" not in repr(payload)


@pytest.mark.parametrize(
    ("requested_device", "cuda_available", "expected_device", "expected_compute_type"),
    [
        (None, False, "cpu", "int8"),
        (None, True, "cuda", "float16"),
        ("cuda", False, "cuda", "float16"),
    ],
)
def test_whisperx_effective_decode_params_resolve_device_and_compute_type(
    requested_device: str | None,
    cuda_available: bool,
    expected_device: str,
    expected_compute_type: str,
):
    with mock.patch.object(wx.torch.cuda, "is_available", return_value=cuda_available):
        transcriber = WhisperXTranscriber(model="small", device=requested_device)

    with mock.patch.object(metadata, "version", return_value="3.8.6"):
        payload = transcriber.effective_decode_params(language="en", initial_prompt=None)

    assert payload["device"] == expected_device
    assert payload["compute_type"] == expected_compute_type
    assert payload["diarization"] is False


@pytest.mark.parametrize(
    "capability",
    [None, CheckpointCapability("whisperx", "segment")],
)
def test_whisperx_effective_decode_params_require_window_capability(capability):
    transcriber = WhisperXTranscriber(model="small", device="cpu")

    with (
        mock.patch.object(metadata, "version", return_value="3.8.6"),
        mock.patch.object(transcriber, "checkpoint_capability", return_value=capability),
        pytest.raises(ValueError, match="whisperx requires a window checkpoint capability"),
    ):
        transcriber.effective_decode_params(language=None, initial_prompt=None)


def test_whisperx_effective_decode_params_fall_back_only_when_metadata_is_missing():
    transcriber = WhisperXTranscriber(model="small", device="cpu")

    with mock.patch.object(
        metadata, "version", side_effect=metadata.PackageNotFoundError("whisperx")
    ):
        payload = transcriber.effective_decode_params(language="zh", initial_prompt=None)

    assert payload["package_version"] == "unknown"
    assert payload["initial_prompt"] == SIMPLIFIED_ZH_PROMPT

    with mock.patch.object(metadata, "version", side_effect=RuntimeError("metadata failure")):
        with pytest.raises(RuntimeError, match="metadata failure"):
            transcriber.effective_decode_params(language=None, initial_prompt=None)


def test_whisperx_silent_window_commits_without_loading_align_model(tmp_path: Path):
    fake = mock.MagicMock()
    model = mock.Mock()
    model.transcribe.return_value = {"language": "zh", "segments": []}
    fake.load_model.return_value = model
    fake.load_audio.return_value = "window-audio"
    transcriber = WhisperXTranscriber(model="small", device="cpu", allow_model_download=True)
    windows: list[tuple[int, float, float, str | None]] = []
    session = CheckpointSession(
        capability=CheckpointCapability("whisperx", "window", 60, 2),
        prior_segments=[],
        cursor=None,
        segment_cb=None,
        window_committed_cb=_record_window_commit(windows),
    )
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"wav")
    window = tmp_path / "window.wav"
    window.write_bytes(b"wav")

    with (
        mock.patch.dict("sys.modules", {"whisperx": fake}),
        mock.patch("mediascribe.checkpoint.probe_duration_seconds", return_value=60.0),
        mock.patch(
            "mediascribe.checkpoint.iter_checkpoint_windows",
            return_value=iter([CheckpointWindow(0, 0.0, 60.0, window)]),
        ),
    ):
        result = transcriber.transcribe(audio_path, language="zh", checkpoint_session=session)

    assert result["segments"] == []
    assert result["language"] == "zh"
    assert windows == [(0, 0.0, 60.0, "zh")]
    fake.load_align_model.assert_not_called()
    fake.align.assert_not_called()
