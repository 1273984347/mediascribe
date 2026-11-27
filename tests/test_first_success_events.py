"""CLI first-success events must be driven by real artifact validation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from mediascribe.events import EventStore
from mediascribe.model_weights import ModelNotReadyError, WeightStatus
from mediascribe.models import SourceRef, TranscriptResult


@pytest.fixture
def event_store(tmp_path, monkeypatch):
    db = tmp_path / "events.sqlite3"
    monkeypatch.setenv("MEDIASCRIBE_EVENT_DB", str(db))
    monkeypatch.setenv("MEDIASCRIBE_INTERNAL_TEST", "0")
    return EventStore(db)


def _result(output: Path, source: str = "https://example.test/audio.wav"):
    return TranscriptResult(
        source=SourceRef(raw_input=source, kind="audio", path=output),
        engine="faster-whisper",
        model="small",
        text="人工智能 软件开发 技术债",
        audio_path=output,
        transcript_path=output,
        metadata={"engine": "faster-whisper", "model": "small"},
        duration_seconds=1.0,
    )


def test_cli_header_only_artifact_is_failure_not_success(
    event_store, tmp_path, monkeypatch, capsys
):
    from mediascribe.__main__ import main

    output = tmp_path / "result.md"

    class FakePipeline:
        def __init__(self, settings):
            self.settings = settings
            self._resource_notices = []

        def transcribe(self, _source, **_kwargs):
            output.write_text("# Transcript\n\n## 转录内容\n", encoding="utf-8")
            return _result(output)

    monkeypatch.setattr("mediascribe.config.find_spec", lambda _name: object())
    monkeypatch.setattr("mediascribe.__main__.Pipeline", FakePipeline)
    monkeypatch.setattr("mediascribe.__main__.preflight_source", lambda *_args: None)
    monkeypatch.setattr("mediascribe.pipeline.resolve_device", lambda _device: "cpu")

    with pytest.raises(SystemExit) as exc_info:
        main(["transcribe", str(tmp_path / "audio.wav"), "--output", str(output)])
    assert exc_info.value.code == 1
    capsys.readouterr()
    terminals = [
        row for row in event_store.list_events() if row["event"] == "transcription_finished"
    ]
    assert [row["properties"]["result"] for row in terminals] == ["failed"]
    assert terminals[0]["properties"]["error_code"] == "RESULT_INVALID"


def test_cli_model_not_ready_records_failed_terminal(event_store, tmp_path, monkeypatch, capsys):
    from mediascribe.__main__ import main

    missing = WeightStatus(
        engine="faster-whisper",
        model="small",
        ok=False,
        location="missing-cache",
        hint="provision",
    )
    monkeypatch.setattr("mediascribe.config.find_spec", lambda _name: object())
    monkeypatch.setattr(
        "mediascribe.__main__.Pipeline", mock.Mock(side_effect=ModelNotReadyError(missing))
    )
    monkeypatch.setattr("mediascribe.__main__.preflight_source", lambda *_args: None)

    assert main(["transcribe", str(tmp_path / "audio.wav")]) == 1
    capsys.readouterr()
    rows = event_store.list_events()
    assert any(row["event"] == "input_submitted" for row in rows)
    terminal = [row for row in rows if row["event"] == "transcription_finished"][-1]
    assert terminal["properties"]["result"] == "failed"
    assert terminal["properties"]["error_code"] == "MODEL_NOT_READY"


def test_cli_success_event_contains_actual_artifact_flags(
    event_store, tmp_path, monkeypatch, capsys
):
    from mediascribe.__main__ import main

    output = tmp_path / "result.md"

    class FakePipeline:
        def __init__(self, settings):
            self.settings = settings
            self._resource_notices = []

        def transcribe(self, _source, **_kwargs):
            output.write_text(
                "# Transcript\n\n## 转录内容\n\n人工智能 软件开发 技术债\n", encoding="utf-8"
            )
            return _result(output)

    monkeypatch.setattr("mediascribe.config.find_spec", lambda _name: object())
    monkeypatch.setattr("mediascribe.__main__.Pipeline", FakePipeline)
    monkeypatch.setattr("mediascribe.__main__.preflight_source", lambda *_args: None)
    monkeypatch.setattr("mediascribe.pipeline.resolve_device", lambda _device: "cpu")

    assert main(["transcribe", str(tmp_path / "audio.wav"), "--output", str(output)]) == 0
    capsys.readouterr()
    terminal = [
        row for row in event_store.list_events() if row["event"] == "transcription_finished"
    ][-1]
    assert terminal["properties"]["result"] == "success"
    assert terminal["properties"]["artifact_exists"] is True
    assert terminal["properties"]["text_nonempty"] is True
    assert terminal["properties"]["metadata_consistent"] is True


def test_generic_result_validator_rejects_header_only_markdown(tmp_path):
    from mediascribe.pipeline_stages import ResultValidationError, validate_transcript_result

    result = _result(tmp_path / "result.md")
    with pytest.raises(ResultValidationError):
        validate_transcript_result(result, "# Transcript\n\n## 转录内容\n")
