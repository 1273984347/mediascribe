"""Local-only first-use event lifecycle contracts (US-012)."""

from __future__ import annotations

import importlib
import json
import socket
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_event_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIASCRIBE_EVENT_DB", str(tmp_path / "events.sqlite3"))
    monkeypatch.delenv("MEDIASCRIBE_EVENT_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("MEDIASCRIBE_APP_DATA", raising=False)


def test_install_id_is_random_stable_and_not_workspace_scoped(tmp_path):
    events = importlib.import_module("mediascribe.events")
    first = tmp_path / "workspace-a" / "events.sqlite3"
    second = tmp_path / "workspace-b" / "events.sqlite3"
    store_a = events.EventStore(first)
    store_b = events.EventStore(second)
    assert store_a.install_id() == store_a.install_id()
    assert store_b.install_id() != store_a.install_id()
    assert len(store_a.install_id()) == 32
    assert all(char in "0123456789abcdef" for char in store_a.install_id())


def test_session_id_is_stable_for_one_process():
    events = importlib.reload(importlib.import_module("mediascribe.events"))
    first = events.session_id()
    second = events.session_id()
    assert first == second
    assert len(first) == 32


def test_default_app_data_path_is_not_under_workspace(monkeypatch, tmp_path):
    events = importlib.import_module("mediascribe.events")
    app_data = tmp_path / "AppData"
    monkeypatch.setenv("MEDIASCRIBE_APP_DATA", str(app_data))
    monkeypatch.delenv("MEDIASCRIBE_EVENT_DB", raising=False)
    path = events.event_db_path()
    assert path.parent == app_data / "MediaScribe"
    assert path.name == "events.sqlite3"
    assert tmp_path / "output" not in path.parents


def test_record_list_and_summary_use_whitelisted_fields(tmp_path):
    events = importlib.import_module("mediascribe.events")
    store = events.EventStore(tmp_path / "events.sqlite3")
    assert store.record(
        "artifact_opened", surface="web", properties={"action": "open", "task_id": "job123"}
    )
    rows = store.list_events()
    assert len(rows) == 1
    assert rows[0]["event"] == "artifact_opened"
    assert rows[0]["install_id"] == store.install_id()
    assert rows[0]["session_id"] == events.session_id()
    assert rows[0]["surface"] == "web"
    assert rows[0]["properties"] == {"action": "open", "task_id": "job123"}
    assert store.summary()["counts_by_event"] == {"artifact_opened": 1}


def test_sensitive_or_unknown_properties_are_rejected(tmp_path):
    events = importlib.import_module("mediascribe.events")
    store = events.EventStore(tmp_path / "events.sqlite3")
    forbidden = {
        "url": "https://private.example/path",
        "path": str(tmp_path / "clip.wav"),
        "text": "transcript body",
        "cookies": "secret=1",
        "api_key": "secret",
        "token": "secret",
    }
    for key, value in forbidden.items():
        with pytest.raises(events.EventPrivacyError):
            store.record("artifact_opened", properties={key: value})
    assert store.list_events() == []


def test_retention_default_is_90_days_and_purges_only_expired(tmp_path):
    events = importlib.import_module("mediascribe.events")
    store = events.EventStore(tmp_path / "events.sqlite3")
    now = 1_800_000_000
    old = now - 91 * 86400
    recent = now - 89 * 86400
    store.record("artifact_opened", properties={"action": "open"}, now=old)
    store.record("artifact_opened", properties={"action": "download"}, now=recent)
    assert store.retention_days == 90
    assert store.purge(now=now) == 1
    remaining = store.list_events()
    assert [row["properties"] for row in remaining] == [{"action": "download"}]


def test_retention_zero_disables_recording_and_clears_events(tmp_path, monkeypatch):
    events = importlib.import_module("mediascribe.events")
    path = tmp_path / "events.sqlite3"
    existing = events.EventStore(path)
    existing.record("artifact_opened", properties={"action": "open"})
    monkeypatch.setenv("MEDIASCRIBE_EVENT_RETENTION_DAYS", "0")
    disabled = events.EventStore(path)
    assert disabled.retention_days == 0
    assert disabled.list_events() == []
    assert disabled.record("artifact_opened", properties={"action": "open"}) is False


def test_invalid_retention_falls_back_to_default_with_warning(tmp_path, monkeypatch):
    events = importlib.import_module("mediascribe.events")
    monkeypatch.setenv("MEDIASCRIBE_EVENT_RETENTION_DAYS", "-1")
    store = events.EventStore(tmp_path / "events.sqlite3")
    assert store.retention_days == 90
    assert store.retention_warning == "EVENT_RETENTION_INVALID"


def test_clear_removes_events_but_preserves_install_identity_and_unrelated_files(tmp_path):
    events = importlib.import_module("mediascribe.events")
    path = tmp_path / "events.sqlite3"
    unrelated = tmp_path / "output" / "result.md"
    unrelated.parent.mkdir()
    unrelated.write_text("keep me", encoding="utf-8")
    store = events.EventStore(path)
    install = store.install_id()
    store.record("artifact_opened", properties={"action": "open"})
    store.clear()
    store.clear()
    assert store.list_events() == []
    assert store.install_id() == install
    assert unrelated.read_text(encoding="utf-8") == "keep me"


def test_export_contains_only_local_event_whitelist(tmp_path):
    events = importlib.import_module("mediascribe.events")
    store = events.EventStore(tmp_path / "events.sqlite3")
    store.record("artifact_opened", surface="web", properties={"action": "open"})
    output = tmp_path / "events.json"
    store.export(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == "mediascribe.local-events/v2"
    assert payload["retention_days"] == 90
    event = payload["events"][0]
    assert set(event) == {
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
    assert event["hardware_tier"] in {"reference_16", "standard_8", "constrained_4", "unknown"}
    assert str(tmp_path) not in output.read_text(encoding="utf-8")


def test_cli_events_list_export_clear(tmp_path, monkeypatch, capsys):
    events = importlib.import_module("mediascribe.events")
    from mediascribe.__main__ import main

    db = tmp_path / "cli-events.sqlite3"
    output = tmp_path / "events.json"
    monkeypatch.setenv("MEDIASCRIBE_EVENT_DB", str(db))
    store = events.EventStore(db)
    store.record("artifact_opened", surface="cli", properties={"action": "open"})

    assert main(["events", "list"]) == 0
    assert "artifact_opened" in capsys.readouterr().out

    assert main(["events", "export", "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["events"][0]["event"] == "artifact_opened"

    assert main(["events", "clear"]) == 0
    assert events.EventStore(db).list_events() == []


def test_event_lifecycle_does_not_open_network_sockets(tmp_path, monkeypatch):
    events = importlib.import_module("mediascribe.events")

    def fail(*_args, **_kwargs):
        raise AssertionError("event lifecycle attempted network access")

    monkeypatch.setattr(socket, "socket", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    store = events.EventStore(tmp_path / "events.sqlite3")
    store.record("artifact_opened", properties={"action": "open"})
    store.list_events()
    store.export(tmp_path / "events.json")
    store.purge()
    store.clear()
