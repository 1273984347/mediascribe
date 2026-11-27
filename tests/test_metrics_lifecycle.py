"""Metrics lifecycle regressions for cleared events and cross-surface installs."""

from __future__ import annotations

import pytest

from mediascribe.events import EventRecorder, EventStore
from mediascribe.metrics import calculate_metrics


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIASCRIBE_INTERNAL_TEST", "0")
    return EventStore(tmp_path / "events.sqlite3")


def test_metrics_include_new_events_after_user_clears_history(store):
    recorder = EventRecorder(store=store, surface="cli")
    recorder.record_first_run()
    store.clear()
    recorder.record_finished(result="success")

    metrics = calculate_metrics(store.list_events(limit=1000))
    assert metrics["totals"]["new_installations"] == 0
    assert metrics["totals"]["successful_installs"] == 1
    assert metrics["rates"]["success_rate"] is None
    assert [row["install_id"] for row in metrics["installs"]] == [store.install_id()]


def test_metrics_surface_facet_uses_installs_seen_in_that_surface(store):
    EventRecorder(store=store, surface="cli").record_first_run()
    EventRecorder(store=store, surface="web").record_finished(result="success", surface="web")

    metrics = calculate_metrics(store.list_events(limit=1000))
    web = metrics["by_surface"]["web"]
    assert web["new_installations"] == 0
    assert web["successful_installs"] == 1
    assert metrics["totals"]["successful_installs"] == 1
