"""US-013 wiring for the six core first-use events across supported surfaces."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


def _patch_recording_probes(monkeypatch) -> None:
    monkeypatch.setenv("MEDIASCRIBE_INTERNAL_TEST", "0")
    import mediascribe.events

    mediascribe.events.schema  # noqa: B018 - require the US-013 package layout
    monkeypatch.setattr(mediascribe.events.schema, "os_family", lambda: "linux", raising=False)
    monkeypatch.setattr(
        mediascribe.events.schema,
        "hardware_tier",
        lambda: "standard_8",
        raising=False,
    )
    monkeypatch.setattr(
        mediascribe.events.schema,
        "model_cache_state",
        lambda engine=None, model=None: "cold",
        raising=False,
    )


@pytest.fixture
def event_store(tmp_path, monkeypatch):
    _patch_recording_probes(monkeypatch)
    db = tmp_path / "events.sqlite3"
    monkeypatch.setenv("MEDIASCRIBE_EVENT_DB", str(db))
    import mediascribe.events

    return mediascribe.events.EventStore(db)


def _rows(store):
    return list(reversed(store.list_events()))


def _events_by_name(store, name):
    return [row for row in _rows(store) if row["event"] == name]


def test_doctor_records_each_explicit_readiness_run(event_store, tmp_path):
    from mediascribe.doctor import run_doctor

    first_exit = run_doctor(workspace=tmp_path / "doctor-workspace")
    second_exit = run_doctor(workspace=tmp_path / "doctor-workspace")
    rows = _events_by_name(event_store, "readiness_checked")
    assert [row["surface"] for row in rows] == ["cli", "cli"]
    assert [row["properties"]["exit_code"] for row in rows] == [first_exit, second_exit]
    assert all(
        row["properties"]["status"] in {"READY", "READY_WITH_WARNINGS", "BLOCKED"} for row in rows
    )


def test_web_health_records_readiness_once_per_process(event_store, tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app

    monkeypatch.setenv("MEDIASCRIBE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("MEDIASCRIBE_WIKI", "0")
    app = create_app(workspace=tmp_path)
    with TestClient(app):
        assert app.state.events_enabled is True
        app.state.event_recorder.record_readiness(
            status="READY",
            surface="web",
            dedupe=True,
        )
        app.state.event_recorder.record_readiness(
            status="READY",
            surface="web",
            dedupe=True,
        )
        app.state.event_recorder.record_readiness(
            status="READY",
            surface="web",
            dedupe=True,
        )
    assert len(_events_by_name(event_store, "readiness_checked")) == 1


def test_web_app_enables_events_only_when_explicitly_allowed(event_store, tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from mediascribe.web.app import create_app

    monkeypatch.setenv("MEDIASCRIBE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("MEDIASCRIBE_WIKI", "0")
    app = create_app(workspace=tmp_path)
    assert app.state.events_enabled is True
    assert app.state.event_recorder is not None


def test_web_job_terminal_state_records_valid_success(event_store, tmp_path):
    from mediascribe.events import EventRecorder
    from mediascribe.models import SourceRef, TranscriptResult
    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_runner import _store_job_result
    from mediascribe.web.job_store import JobStore

    markdown = tmp_path / "result.md"
    markdown.write_text("# Transcript\n\nvalid text\n", encoding="utf-8")
    result = TranscriptResult(
        source=SourceRef(raw_input="private/local/path/clip.wav", kind="audio", path=markdown),
        engine="faster-whisper",
        model="small",
        text="valid text",
        audio_path=tmp_path / "clip.wav",
        transcript_path=markdown,
        metadata={"engine": "faster-whisper", "model": "small", "title": "private title"},
        duration_seconds=12.5,
    )
    registry = ProgressRegistry()
    control = JobControl(JobStore(tmp_path / "jobs.sqlite3"), registry)
    job = control.submit("private/local/path/clip.wav", request={}, selection={}, out_path=markdown)
    control.start_attempt(job.job_id)
    _store_job_result(
        lambda: result,
        job.job_id,
        markdown,
        "private/local/path/clip.wav",
        {},
        threading.Lock(),
        registry,
        control,
        events=EventRecorder(store=event_store, surface="web"),
    )
    rows = _events_by_name(event_store, "transcription_finished")
    assert rows[0]["properties"]["result"] == "success"
    assert rows[0]["properties"]["artifact_exists"] is True
    assert rows[0]["properties"]["text_nonempty"] is True
    assert rows[0]["properties"]["metadata_consistent"] is True
    assert "private/local/path" not in json.dumps(_rows(event_store))
    assert "private title" not in json.dumps(_rows(event_store))


def test_web_job_no_speech_and_cancellation_use_distinct_terminal_events(event_store, tmp_path):
    from mediascribe.events import EventRecorder
    from mediascribe.pipeline_stages import NoSpeechError
    from mediascribe.progress import JobCancelled, ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_runner import _store_job_result
    from mediascribe.web.job_store import JobStore

    recorder = EventRecorder(store=event_store, surface="web")

    silent_registry = ProgressRegistry()
    silent_control = JobControl(JobStore(tmp_path / "jobs.sqlite3"), silent_registry)
    silent_job = silent_control.submit(
        "https://example.test/silent.mp4", request={}, selection={}, out_path=tmp_path / "silent.md"
    )
    silent_control.start_attempt(silent_job.job_id)
    _store_job_result(
        lambda: (_ for _ in ()).throw(NoSpeechError("silence")),
        silent_job.job_id,
        tmp_path / "silent.md",
        "https://example.test/silent.mp4",
        {},
        threading.Lock(),
        silent_registry,
        silent_control,
        events=recorder,
    )
    assert (
        _events_by_name(event_store, "transcription_finished")[-1]["properties"]["result"]
        == "no_speech"
    )

    cancelled_registry = ProgressRegistry()
    cancelled_control = JobControl(JobStore(tmp_path / "jobs.sqlite3"), cancelled_registry)
    cancelled_job = cancelled_control.submit(
        "https://example.test/clip.mp4",
        request={},
        selection={},
        out_path=tmp_path / "cancelled.md",
    )
    cancelled_control.start_attempt(cancelled_job.job_id)
    cancelled_control.cancel(cancelled_job.job_id)
    _store_job_result(
        lambda: (_ for _ in ()).throw(JobCancelled("cancelled")),
        cancelled_job.job_id,
        tmp_path / "cancelled.md",
        "https://example.test/clip.mp4",
        {},
        threading.Lock(),
        cancelled_registry,
        cancelled_control,
        events=recorder,
    )
    assert (
        _events_by_name(event_store, "transcription_finished")[-1]["properties"]["result"]
        == "cancelled"
    )


def test_web_submit_records_input_without_raw_url(event_store, tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from mediascribe.models import SourceRef, TranscriptResult
    from mediascribe.web.app import create_app

    class FakePipeline:
        def __init__(self, settings, **_kwargs):
            self.settings = settings
            self.transcriber = object()
            self._resource_notices = []

        def transcribe(self, source, **kwargs):
            output = Path(kwargs["output"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("# Transcript\n\nsubmitted\n", encoding="utf-8")
            return TranscriptResult(
                source=SourceRef(raw_input=source, kind="unknown", url=source),
                engine="faster-whisper",
                model="small",
                text="submitted",
                audio_path=tmp_path / "audio.wav",
                transcript_path=output,
                metadata={"engine": "faster-whisper", "model": "small"},
                duration_seconds=1.0,
            )

    monkeypatch.setenv("MEDIASCRIBE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("MEDIASCRIBE_WIKI", "0")
    monkeypatch.setattr("mediascribe.pipeline.Pipeline", FakePipeline)
    monkeypatch.setattr("mediascribe.pipeline.resolve_device", lambda _device: "cpu")
    app = create_app(workspace=tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            json={"urls": ["https://www.bilibili.com/video/BV1xx411c7mD"], "model": "small"},
        )
        assert response.status_code == 200
        job_id = response.json()["jobs"][0]["job_id"]
        for _ in range(100):
            if _events_by_name(event_store, "transcription_finished"):
                break
            time.sleep(0.02)
    rows = _events_by_name(event_store, "input_submitted")
    assert rows[0]["properties"]["input_kind"] == "platform_url"
    assert rows[0]["properties"]["duration_bucket"] == "unknown"
    assert "BV1xx411c7mD" not in json.dumps(_rows(event_store))
    assert _events_by_name(event_store, "transcription_finished")
    assert (
        _events_by_name(event_store, "transcription_finished")[-1]["properties"]["result"]
        == "success"
    )
    assert job_id


def test_web_retry_records_only_the_clickable_recovery_action(event_store, tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from mediascribe.model_weights import ModelDownloadError
    from mediascribe.models import SourceRef, TranscriptResult
    from mediascribe.web.app import create_app

    calls = []

    class FakePipeline:
        def __init__(self, settings, **_kwargs):
            self.settings = settings
            self.transcriber = object()
            self._resource_notices = []

        def transcribe(self, source, **kwargs):
            calls.append(kwargs["output"])
            output = Path(kwargs["output"])
            if len(calls) == 1:
                raise ModelDownloadError("private token /secret/path")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("# Transcript\n\nrecovered\n", encoding="utf-8")
            return TranscriptResult(
                source=SourceRef(raw_input=source, kind="unknown", url=source),
                engine="whisper",
                model="small",
                text="recovered",
                audio_path=tmp_path / "audio.wav",
                transcript_path=output,
                metadata={"engine": "whisper", "model": "small"},
                duration_seconds=1.0,
            )

    monkeypatch.setenv("MEDIASCRIBE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("MEDIASCRIBE_WIKI", "0")
    monkeypatch.setattr("mediascribe.pipeline.Pipeline", FakePipeline)
    monkeypatch.setattr("mediascribe.pipeline.resolve_device", lambda _device: "cpu")
    app = create_app(workspace=tmp_path)
    with TestClient(app) as client:
        submitted = client.post(
            "/api/jobs",
            json={"urls": ["https://www.bilibili.com/video/BV1xx411c7mD"], "engine": "whisper"},
        )
        assert submitted.status_code == 200
        job_id = submitted.json()["jobs"][0]["job_id"]
        for _ in range(100):
            result = client.get(f"/api/jobs/{job_id}/result").json()
            if result["finished"]:
                break
            time.sleep(0.02)
        assert result["error_code"] == "MODEL_DOWNLOAD_FAILED"
        assert client.post(f"/api/jobs/{job_id}/retry").status_code == 200
        for _ in range(100):
            recovered = client.get(f"/api/jobs/{job_id}/result").json()
            if recovered.get("retry_job_id"):
                retry_id = recovered["retry_job_id"]
                break
            time.sleep(0.02)
        else:
            pytest.fail("retry job did not start")
        for _ in range(100):
            terminal = client.get(f"/api/jobs/{retry_id}/result").json()
            if terminal["finished"]:
                break
            time.sleep(0.02)
    rows = _events_by_name(event_store, "recovery_action_selected")
    assert len(rows) == 1
    assert rows[0]["surface"] == "web"
    assert rows[0]["properties"] == {
        "action": "retry",
        "error_code": "MODEL_DOWNLOAD_FAILED",
        "task_id": job_id,
        "retry_task_id": retry_id,
    }
    assert "/secret/path" not in json.dumps(_rows(event_store))


def test_mcp_transcription_records_shared_install_and_terminal_event(event_store, tmp_path):
    from mediascribe.mcp_server.protocol import _handle_request
    from mediascribe.mcp_server.tools import TOOL_HANDLERS

    def fake_handler(args):
        return {"transcript": str(tmp_path / "private/path/result.md"), "engine": args["engine"]}

    with mock.patch.dict(TOOL_HANDLERS, {"transcribe_video": fake_handler}):
        response = _handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "transcribe_video",
                    "arguments": {
                        "url": "https://www.douyin.com/video/private-id",
                        "engine": "whisper",
                    },
                },
            }
        )
    assert response["result"]["isError"] is False
    inputs = _events_by_name(event_store, "input_submitted")
    terminals = _events_by_name(event_store, "transcription_finished")
    assert [row["surface"] for row in inputs] == ["mcp"]
    assert terminals[-1]["surface"] == "mcp"
    assert terminals[-1]["properties"]["result"] == "success"
    assert inputs[0]["install_id"] == terminals[-1]["install_id"]
    assert "private-id" not in json.dumps(_rows(event_store))
    assert str(tmp_path) not in json.dumps(_rows(event_store))


def test_mcp_no_speech_maps_to_distinct_finished_event(event_store, tmp_path):
    from mediascribe.mcp_server.protocol import _handle_request
    from mediascribe.mcp_server.tools import TOOL_HANDLERS
    from mediascribe.pipeline_stages import NoSpeechError

    def fail(_args):
        raise NoSpeechError("silent")

    with mock.patch.dict(TOOL_HANDLERS, {"transcribe_video": fail}):
        response = _handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "transcribe_video",
                    "arguments": {"url": "https://example.test/silent.mp4"},
                },
            }
        )
    assert response["result"]["isError"] is True
    rows = _events_by_name(event_store, "transcription_finished")
    assert rows[-1]["properties"] == {
        "result": "no_speech",
        "error_code": "NO_SPEECH",
        "stage": "transcribe",
    }


def test_mcp_ok_false_is_structured_error_and_failed_event(event_store, tmp_path):
    from mediascribe.mcp_server.protocol import _handle_request
    from mediascribe.mcp_server.tools import TOOL_HANDLERS

    def fake_handler(_args):
        return {
            "ok": False,
            "error": "private diagnostic /secret/path",
            "error_code": "INPUT_INVALID",
        }

    with mock.patch.dict(TOOL_HANDLERS, {"transcribe_wechat_mp": fake_handler}):
        response = _handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "transcribe_wechat_mp",
                    "arguments": {"url": "https://mp.weixin.qq.com/s/private"},
                },
            }
        )
    result = response["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "INPUT_INVALID"
    assert result["structuredContent"]["recovery"]["scope"] == "input"
    assert "/secret/path" not in json.dumps(result)
    terminal = _events_by_name(event_store, "transcription_finished")[-1]
    assert terminal["properties"]["result"] == "failed"
    assert terminal["properties"]["error_code"] == "INPUT_INVALID"


def test_batch_result_records_finished_event_without_content_id(event_store, tmp_path):
    from mediascribe.batch_cli import _ResultRecorder
    from mediascribe.events import EventRecorder

    tracker = mock.Mock()
    cache = mock.Mock()
    recorder = _ResultRecorder(
        tracker=tracker,
        cache=cache,
        log=mock.Mock(),
        t=lambda key, **kwargs: key,
        events=EventRecorder(store=event_store, surface="batch"),
    )
    recorder.record(
        {
            "video_id": "private-video-id",
            "url": "https://example.test/private-video",
            "status": "success",
            "transcript": str(tmp_path / "private" / "result.md"),
        },
        12.0,
        "worker-1",
    )
    rows = _events_by_name(event_store, "transcription_finished")
    assert rows[0]["surface"] == "batch"
    assert rows[0]["properties"]["result"] == "success"
    assert "private-video-id" not in json.dumps(_rows(event_store))
    assert str(tmp_path) not in json.dumps(_rows(event_store))


def test_cli_transcribe_records_input_and_success_but_no_unobservable_open(
    event_store, tmp_path, monkeypatch, capsys
):
    from mediascribe.__main__ import main
    from mediascribe.models import SourceRef, TranscriptResult

    output = tmp_path / "result.md"
    result = TranscriptResult(
        source=SourceRef(raw_input="https://www.bilibili.com/video/BV1xx411c7mD", kind="bilibili"),
        engine="faster-whisper",
        model="small",
        text="cli result",
        audio_path=tmp_path / "audio.wav",
        transcript_path=output,
        metadata={"engine": "faster-whisper", "model": "small"},
        duration_seconds=2.0,
    )

    class FakePipeline:
        def __init__(self, settings):
            self.settings = settings
            self._resource_notices = []

        def transcribe(self, _source, **kwargs):
            output.write_text("# Transcript\n\ncli result\n", encoding="utf-8")
            return result

    monkeypatch.setattr("mediascribe.config.find_spec", lambda _name: object())
    monkeypatch.setattr("mediascribe.__main__.Pipeline", FakePipeline)
    monkeypatch.setattr("mediascribe.__main__.preflight_source", lambda *_args: result.source)
    monkeypatch.setattr("mediascribe.pipeline.resolve_device", lambda _device: "cpu")
    assert main(["transcribe", "https://www.bilibili.com/video/BV1xx411c7mD"]) == 0
    capsys.readouterr()
    assert _events_by_name(event_store, "input_submitted")
    terminal = _events_by_name(event_store, "transcription_finished")[-1]
    assert terminal["surface"] == "cli"
    assert terminal["properties"]["result"] == "success"
    assert not _events_by_name(event_store, "artifact_opened")
    assert "BV1xx411c7mD" not in json.dumps(_rows(event_store))


def test_cli_no_speech_records_four_value_terminal_result(
    event_store, tmp_path, monkeypatch, capsys
):
    from mediascribe.__main__ import main
    from mediascribe.pipeline_stages import NoSpeechError

    class FakePipeline:
        def __init__(self, settings):
            self.settings = settings
            self._resource_notices = []

        def transcribe(self, _source, **_kwargs):
            raise NoSpeechError("silent")

    monkeypatch.setattr("mediascribe.config.find_spec", lambda _name: object())
    monkeypatch.setattr("mediascribe.__main__.Pipeline", FakePipeline)
    monkeypatch.setattr("mediascribe.__main__.preflight_source", lambda *_args: None)
    monkeypatch.setattr("mediascribe.pipeline.resolve_device", lambda _device: "cpu")
    with pytest.raises(SystemExit) as exit_info:
        main(["transcribe", str(tmp_path / "silent.wav")])
    assert exit_info.value.code == 1
    capsys.readouterr()
    row = _events_by_name(event_store, "transcription_finished")[-1]
    assert row["properties"]["result"] == "no_speech"
    assert row["properties"]["error_code"] == "NO_SPEECH"


def test_first_use_events_are_written_to_the_shared_user_install(
    tmp_path, monkeypatch, event_store
):
    from mediascribe.__main__ import main
    from mediascribe.models import SourceRef, TranscriptResult

    output = tmp_path / "result.md"
    result = TranscriptResult(
        source=SourceRef(raw_input="https://example.test/video.mp4", kind="video"),
        engine="faster-whisper",
        model="small",
        text="shared identity",
        audio_path=tmp_path / "audio.wav",
        transcript_path=output,
        metadata={"engine": "faster-whisper", "model": "small"},
        duration_seconds=1.0,
    )

    class FakePipeline:
        def __init__(self, settings):
            self.settings = settings
            self._resource_notices = []

        def transcribe(self, _source, **kwargs):
            output.write_text("# Transcript\n\nshared identity\n", encoding="utf-8")
            return result

    monkeypatch.setattr("mediascribe.config.find_spec", lambda _name: object())
    monkeypatch.setattr("mediascribe.__main__.Pipeline", FakePipeline)
    monkeypatch.setattr("mediascribe.__main__.preflight_source", lambda *_args: result.source)
    monkeypatch.setattr("mediascribe.pipeline.resolve_device", lambda _device: "cpu")
    assert main(["transcribe", "https://example.test/video.mp4"]) == 0
    first = _events_by_name(event_store, "first_run_started")
    finish = _events_by_name(event_store, "transcription_finished")
    assert first
    assert finish[0]["install_id"] == first[0]["install_id"]
