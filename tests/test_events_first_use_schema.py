"""US-013 core first-use event and local metrics contracts."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

CORE_FIELDS = {
    "event_id",
    "event",
    "ts",
    "install_id",
    "session_id",
    "surface",
    "version",
    "os_family",
    "hardware_tier",
    "is_first_run",
    "model_cache_state",
    "is_internal_test",
    "properties",
}


@pytest.fixture
def events(tmp_path, monkeypatch):
    module = pytest.importorskip("mediascribe.events")
    monkeypatch.setenv("MEDIASCRIBE_EVENT_DB", str(tmp_path / "events.sqlite3"))
    monkeypatch.setenv("MEDIASCRIBE_INTERNAL_TEST", "0")
    monkeypatch.delenv("MEDIASCRIBE_APP_DATA", raising=False)
    monkeypatch.setattr(module.schema, "os_family", lambda: "linux", raising=False)
    monkeypatch.setattr(module.schema, "hardware_tier", lambda: "standard_8", raising=False)
    monkeypatch.setattr(
        module.schema,
        "model_cache_state",
        lambda engine=None, model=None: "cold",
        raising=False,
    )
    return module


def _event_row(
    event,
    *,
    install="install-a",
    surface="cli",
    ts=1_000.0,
    internal=False,
    properties=None,
    **columns,
):
    row = {
        "event": event,
        "ts": ts,
        "install_id": install,
        "surface": surface,
        "os_family": "linux",
        "hardware_tier": "standard_8",
        "is_first_run": event == "first_run_started",
        "model_cache_state": "cold",
        "is_internal_test": internal,
        "properties": properties or {},
    }
    row.update(columns)
    return row


def test_events_namespace_is_a_package_with_us012_compatibility(events, tmp_path):
    assert Path(events.__file__).parent.name == "events"
    store = events.EventStore(tmp_path / "events.sqlite3")
    assert store.record("artifact_opened", surface="web", properties={"action": "open"})
    assert events.EXPORT_SCHEMA == "mediascribe.local-events/v2"


def test_v2_export_promotes_common_fields(events, tmp_path):
    store = events.EventStore(tmp_path / "events.sqlite3")
    store.record("artifact_opened", surface="web", properties={"action": "copy"})
    rows = store.list_events()
    assert set(rows[0]) == CORE_FIELDS
    assert rows[0]["os_family"] == "linux"
    assert rows[0]["hardware_tier"] == "standard_8"
    assert rows[0]["model_cache_state"] == "cold"
    assert rows[0]["is_first_run"] is False
    assert rows[0]["is_internal_test"] is False

    output = tmp_path / "events.json"
    store.export(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == "mediascribe.local-events/v2"
    assert set(payload["events"][0]) == CORE_FIELDS


def test_v2_migrates_v1_database_without_losing_existing_events(tmp_path, monkeypatch):
    import sqlite3
    import time

    path = tmp_path / "events.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE event_identity(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE local_events(
            event_id TEXT PRIMARY KEY,
            event TEXT NOT NULL,
            ts REAL NOT NULL,
            install_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            surface TEXT NOT NULL,
            version TEXT NOT NULL,
            properties TEXT NOT NULL
        );
        INSERT INTO event_identity VALUES('install_id', 'old-install');
        """
    )
    conn.execute(
        "INSERT INTO local_events VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        ("old", "artifact_opened", time.time(), "old-install", "old-session", "web", "1", "{}"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("MEDIASCRIBE_INTERNAL_TEST", "0")
    events = pytest.importorskip("mediascribe.events")
    store = events.EventStore(path)
    assert [row["event"] for row in store.list_events()] == ["artifact_opened"]
    assert store.install_id() == "old-install"
    assert store.list_events()[0]["hardware_tier"] == "unknown"


def test_first_run_starts_once_per_install_across_surfaces(events, tmp_path):
    store = events.EventStore(tmp_path / "events.sqlite3")
    cli = events.EventRecorder(store=store, surface="cli")
    web = events.EventRecorder(store=store, surface="web")
    assert cli.record_first_run() is True
    assert web.record_first_run() is False
    assert cli.record_artifact(action="open") is True
    rows = store.list_events()
    first_runs = [row for row in rows if row["event"] == "first_run_started"]
    assert len(first_runs) == 1
    assert first_runs[0]["is_first_run"] is True


def test_clear_events_keeps_first_run_identity(events, tmp_path):
    store = events.EventStore(tmp_path / "events.sqlite3")
    recorder = events.EventRecorder(store=store, surface="cli")
    assert recorder.record_first_run() is True
    store.clear()
    assert recorder.record_first_run() is False
    assert store.list_events() == []


def test_readiness_is_deduped_per_store_and_surface_but_explicit_runs_are_forced(events, tmp_path):
    store = events.EventStore(tmp_path / "events.sqlite3")
    web = events.EventRecorder(store=store, surface="web")
    doctor = events.EventRecorder(store=store, surface="cli")
    assert web.record_readiness(status="READY") is True
    assert web.record_readiness(status="READY") is False
    assert (
        doctor.record_readiness(
            status="READY_WITH_WARNINGS",
            exit_code=0,
            warnings=["MODEL_NOT_READY"],
            engine="faster-whisper",
            cache_state="cold",
        )
        is True
    )
    assert (
        doctor.record_readiness(
            status="READY_WITH_WARNINGS",
            exit_code=0,
            warnings=["MODEL_NOT_READY"],
            engine="faster-whisper",
            cache_state="cold",
            force=True,
        )
        is True
    )
    rows = [row for row in store.list_events() if row["event"] == "readiness_checked"]
    assert [row["surface"] for row in rows] == ["cli", "cli", "web"]
    assert rows[0]["properties"] == {
        "status": "READY_WITH_WARNINGS",
        "exit_code": 0,
        "blocker_codes": [],
        "warning_codes": ["MODEL_NOT_READY"],
        "engine": "faster-whisper",
        "cache_state": "cold",
    }


def test_local_duration_uses_probe_but_remote_urls_are_never_probed(events, tmp_path, monkeypatch):
    probe_calls = []

    def fake_probe(path):
        probe_calls.append(Path(path))
        return 120.0

    monkeypatch.setattr(events.schema, "probe_duration_seconds", fake_probe, raising=False)
    store = events.EventStore(tmp_path / "events.sqlite3")
    recorder = events.EventRecorder(store=store, surface="cli")
    local = tmp_path / "clip.wav"
    local.write_bytes(b"fake")
    assert recorder.record_input(
        input_value=str(local),
        selection_mode="auto",
        engine="faster-whisper",
        model="small",
    )
    assert recorder.record_input(
        input_value="https://www.bilibili.com/video/BV1xx411c7mD",
        selection_mode="explicit",
        engine="whisper",
        model="medium",
        device="cuda",
    )
    rows = list(reversed([row for row in store.list_events() if row["event"] == "input_submitted"]))
    assert [row["properties"]["input_kind"] for row in rows] == ["local_audio", "platform_url"]
    assert [row["properties"]["duration_bucket"] for row in rows] == ["1-3m", "unknown"]
    assert [row["properties"]["network_required"] for row in rows] == [False, True]
    assert probe_calls == [local]
    assert "clip.wav" not in json.dumps(rows, ensure_ascii=False)
    assert "BV1xx411c7mD" not in json.dumps(rows, ensure_ascii=False)


def test_transcription_finished_result_is_a_four_value_enum(events, tmp_path):
    store = events.EventStore(tmp_path / "events.sqlite3")
    recorder = events.EventRecorder(store=store, surface="web")
    assert recorder.record_finished(result="success", task_id="job-1") is True
    assert recorder.record_finished(result="no_speech", error_code="NO_SPEECH") is True
    assert recorder.record_finished(result="cancelled", task_id="job-2") is True
    assert recorder.record_finished(result="failed", error_code="DOWNLOAD_FAILED") is True
    with pytest.raises(ValueError):
        recorder.record_finished(result="partial")
    with pytest.raises(ValueError):
        recorder.record_finished(result="skipped")
    rows = list(
        reversed([row for row in store.list_events() if row["event"] == "transcription_finished"])
    )
    assert [row["properties"]["result"] for row in rows] == [
        "success",
        "no_speech",
        "cancelled",
        "failed",
    ]
    assert rows[0]["properties"]["artifact_exists"] is True
    assert rows[0]["properties"]["text_nonempty"] is True
    assert rows[0]["properties"]["metadata_consistent"] is True


def test_artifact_and_recovery_events_record_only_observable_user_actions(events, tmp_path):
    store = events.EventStore(tmp_path / "events.sqlite3")
    recorder = events.EventRecorder(store=store, surface="web")
    assert recorder.record_artifact(action="open", task_id="job-1") is True
    assert recorder.record_artifact(action="copy", task_id="job-1") is True
    assert recorder.record_artifact(action="download", task_id="job-1") is True
    with pytest.raises(ValueError):
        recorder.record_artifact(action="external_editor")
    assert recorder.record_recovery(action="retry", task_id="job-1") is True
    with pytest.raises(ValueError):
        recorder.record_recovery(action="install_engine")
    rows = list(reversed(store.list_events()))
    assert [row["properties"]["action"] for row in rows if row["event"] == "artifact_opened"] == [
        "open",
        "copy",
        "download",
    ]
    assert [
        row["properties"]["action"] for row in rows if row["event"] == "recovery_action_selected"
    ] == ["retry"]


def test_internal_test_records_do_not_enter_local_metrics(events):
    rows = [
        _event_row("first_run_started", install="real", ts=10.0),
        _event_row(
            "input_submitted",
            install="real",
            ts=20.0,
            properties={"input_kind": "platform_url"},
        ),
        _event_row(
            "transcription_finished",
            install="real",
            ts=30.0,
            properties={
                "result": "success",
                "artifact_exists": True,
                "text_nonempty": True,
                "metadata_consistent": True,
            },
        ),
        _event_row(
            "artifact_opened",
            install="real",
            ts=40.0,
            properties={"action": "open"},
        ),
        _event_row("first_run_started", install="ci", ts=11.0, internal=True),
        _event_row(
            "transcription_finished",
            install="ci",
            ts=21.0,
            internal=True,
            properties={"result": "success"},
        ),
    ]
    metrics = events.metrics.calculate_metrics(rows)
    assert metrics["totals"]["new_installations"] == 1
    assert metrics["totals"]["successful_installs"] == 1
    assert [row["install_id"] for row in metrics["installs"]] == ["real"]


def test_metrics_calculates_local_funnel_by_install_and_surface(events):
    rows = [
        _event_row("first_run_started", surface="cli", ts=1.0),
        _event_row("input_submitted", surface="cli", ts=2.0),
        _event_row(
            "transcription_finished",
            surface="cli",
            ts=3.0,
            properties={
                "result": "failed",
                "error_code": "MODEL_DOWNLOAD_FAILED",
            },
        ),
        _event_row(
            "recovery_action_selected",
            surface="cli",
            ts=4.0,
            properties={"action": "retry", "error_code": "MODEL_DOWNLOAD_FAILED"},
        ),
        _event_row(
            "transcription_finished",
            surface="cli",
            ts=5.0,
            properties={
                "result": "success",
                "artifact_exists": True,
                "text_nonempty": True,
                "metadata_consistent": True,
            },
        ),
        _event_row("artifact_opened", surface="cli", ts=6.0, properties={"action": "copy"}),
        _event_row("first_run_started", install="install-b", surface="web", ts=1.5),
    ]
    metrics = events.metrics.calculate_metrics(rows)
    assert metrics["schema"] == "mediascribe.local-event-metrics/v1"
    assert metrics["funnel"] == [
        "first_run_started",
        "input_submitted",
        "transcription_finished_success",
        "artifact_opened",
        "recovery_action_selected",
    ]
    assert metrics["totals"] == {
        "new_installations": 2,
        "submitted_installs": 1,
        "successful_installs": 1,
        "opened_installs": 1,
        "recovery_selected_installs": 1,
        "recovered_installs": 1,
        "failed_installs": 1,
        "cancelled_installs": 0,
        "no_speech_installs": 0,
    }
    assert metrics["rates"] == {
        "submission_rate": 0.5,
        "success_rate": 0.5,
        "open_rate": 1.0,
        "recovery_success_rate": 1.0,
    }
    assert metrics["by_surface"]["cli"]["successful_installs"] == 1
    assert metrics["by_surface"]["web"]["new_installations"] == 1
    assert metrics["by_surface"]["web"]["successful_installs"] == 0


def test_metrics_cancelled_and_no_speech_do_not_enter_success_denominator(events):
    rows = [
        _event_row("first_run_started", install="cancel", ts=1.0),
        _event_row(
            "transcription_finished",
            install="cancel",
            ts=2.0,
            properties={"result": "cancelled"},
        ),
        _event_row("first_run_started", install="quiet", ts=1.0),
        _event_row(
            "transcription_finished",
            install="quiet",
            ts=2.0,
            properties={"result": "no_speech"},
        ),
    ]
    metrics = events.metrics.calculate_metrics(rows)
    assert metrics["totals"]["new_installations"] == 2
    assert metrics["totals"]["successful_installs"] == 0
    assert metrics["totals"]["cancelled_installs"] == 1
    assert metrics["totals"]["no_speech_installs"] == 1


def test_events_metrics_cli_outputs_local_json(events, tmp_path, monkeypatch, capsys):
    from mediascribe.__main__ import main

    db = tmp_path / "metrics.sqlite3"
    monkeypatch.setenv("MEDIASCRIBE_EVENT_DB", str(db))
    store = events.EventStore(db)
    recorder = events.EventRecorder(store=store, surface="cli")
    recorder.record_first_run()
    recorder.record_input(input_value="https://www.bilibili.com/video/BV1xx411c7mD")
    recorder.record_finished(
        result="success",
        artifact_exists=True,
        text_nonempty=True,
        metadata_consistent=True,
    )
    assert main(["events", "metrics"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "mediascribe.local-event-metrics/v1"
    assert payload["totals"]["successful_installs"] == 1


def test_event_lifecycle_probes_do_not_use_network(events, monkeypatch, tmp_path):
    import socket

    def fail(*_args, **_kwargs):
        raise AssertionError("US-013 event lifecycle attempted network access")

    monkeypatch.setattr(socket, "socket", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    store = events.EventStore(tmp_path / "events.sqlite3")
    recorder = events.EventRecorder(store=store, surface="web")
    assert recorder.record_first_run() is True
    assert recorder.record_readiness(status="READY") is True
    assert (
        recorder.record_input(
            input_value="https://example.com/media.mp4",
            selection_mode="auto",
            engine="faster-whisper",
        )
        is True
    )
    assert recorder.record_finished(result="success") is True
    assert recorder.record_artifact(action="download") is True
    assert recorder.record_recovery(action="retry") is True
    events.metrics.calculate_metrics(store.list_events())


def test_recorder_is_skipped_for_internal_test_runs(events, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIASCRIBE_INTERNAL_TEST", "1")
    store = events.EventStore(tmp_path / "events.sqlite3")
    recorder = events.EventRecorder(store=store, surface="cli")
    assert recorder.record_first_run() is False
    assert recorder.record_finished(result="success") is False
    assert store.list_events() == []


def test_recorder_never_creates_event_store_when_retention_is_disabled(events, monkeypatch):
    monkeypatch.setenv("MEDIASCRIBE_EVENT_RETENTION_DAYS", "0")
    calls = []

    class BrokenStore:
        def __getattr__(self, _name):
            calls.append(_name)
            raise AssertionError("retention-disabled recording must not touch the store")

    recorder = events.EventRecorder(store=BrokenStore(), surface="cli")
    assert recorder.record_first_run() is False
    assert recorder.record_finished(result="success") is False
    assert calls == []
