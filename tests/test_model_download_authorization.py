"""Explicit model-download authorization for verification and WhisperX assets."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from mediascribe.model_weights import ModelNotReadyError, WeightStatus
from mediascribe.transcribers import whisperx as wx


def test_verify_blocks_missing_model_before_constructing_transcriber(tmp_path):
    from mediascribe import verify as verify_module

    transcript = tmp_path / "transcript.md"
    transcript.write_text("# Transcript\n\nbody\n", encoding="utf-8")
    settings = SimpleNamespace(workspace_root=tmp_path, allow_model_download=False)
    missing = WeightStatus(
        engine="faster-whisper",
        model="large-v3",
        ok=False,
        location="missing-cache",
        hint="provision or authorize",
    )
    with (
        mock.patch.object(
            verify_module,
            "locate_artifacts",
            return_value=SimpleNamespace(source_url=None, metadata={}),
        ),
        mock.patch.object(
            verify_module, "ensure_audio", return_value=(tmp_path / "audio.wav", True)
        ),
        mock.patch(
            "mediascribe.model_weights.ensure_model_ready", side_effect=ModelNotReadyError(missing)
        ),
        mock.patch(
            "mediascribe.transcribers.faster_whisper.FasterWhisperTranscriber"
        ) as transcriber,
    ):
        with pytest.raises(ModelNotReadyError):
            verify_module.run_verify(transcript, settings, model="large-v3", device="cpu")
    transcriber.assert_not_called()


def test_verify_cli_defaults_download_off_and_supports_explicit_opt_in(tmp_path, monkeypatch):
    from mediascribe.__main__ import main

    settings = SimpleNamespace(workspace_root=tmp_path, allow_model_download=False)
    monkeypatch.setattr("mediascribe.config.Settings", lambda: settings)
    calls = []
    monkeypatch.setattr(
        "mediascribe.verify.run_verify",
        lambda transcript, current_settings, **kwargs: calls.append(current_settings),
    )

    assert main(["verify", str(tmp_path / "transcript.md")]) == 0
    assert calls[-1].allow_model_download is False

    assert main(["verify", str(tmp_path / "transcript.md"), "--allow-model-download"]) == 0
    assert calls[-1].allow_model_download is True


def test_whisperx_align_model_requires_authorization_when_not_cached():
    wx.clear_model_cache()
    fake_whisperx = mock.MagicMock()
    with pytest.raises(ModelNotReadyError):
        wx._get_cached_align_model(fake_whisperx, "zh", "cpu")
    fake_whisperx.load_align_model.assert_not_called()


def test_whisperx_align_model_loads_when_download_is_authorized():
    wx.clear_model_cache()
    fake_whisperx = mock.MagicMock()
    fake_whisperx.load_align_model.return_value = ("ALIGN", {})
    assert wx._get_cached_align_model(fake_whisperx, "zh", "cpu", allow_model_download=True) == (
        "ALIGN",
        {},
    )
    fake_whisperx.load_align_model.assert_called_once()


def test_whisperx_diarization_requires_authorization_when_not_cached():
    wx.clear_model_cache()
    fake_whisperx = mock.MagicMock()
    with pytest.raises(ModelNotReadyError):
        wx._get_cached_diarize_pipeline(fake_whisperx, "token", "cpu")
    fake_whisperx.DiarizationPipeline.assert_not_called()


def test_pipeline_passes_model_download_authorization_to_whisperx():
    from mediascribe.config import Settings
    from mediascribe.pipeline import Pipeline

    settings = Settings(engine="whisperx", model="small", device="cpu", allow_model_download=True)
    with (
        mock.patch("mediascribe.pipeline.ensure_model_ready", return_value=mock.MagicMock(ok=True)),
        mock.patch("mediascribe.pipeline.WhisperXTranscriber") as transcriber,
    ):
        Pipeline(settings)
    assert transcriber.call_args.kwargs["allow_model_download"] is True
