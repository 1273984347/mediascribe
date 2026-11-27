from __future__ import annotations

import time
from pathlib import Path

import pytest

from mediascribe.web.job_events import CheckpointConflict, JobEvent
from mediascribe.web.job_store import JobStore


def _event(payload: dict | None = None) -> JobEvent:
    return JobEvent(
        event_id="evt-1",
        job_id="j1",
        attempt_id="a1",
        seq=1,
        type="segment_committed",
        payload=payload or {"segment_index": 0, "text": "hello"},
        created_at=time.time(),
    )


def test_commit_event_writes_event_snapshot_and_segment_atomically(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.upsert("j1", "https://example.test/a", out_path="out/a.md")
    event = _event()
    snapshot = {"status": "running", "committed_segments": 1, "last_segment_index": 0}
    segment = {
        "job_id": "j1",
        "segment_index": 0,
        "attempt_id": "a1",
        "start": 0.0,
        "end": 1.0,
        "text": "hello",
        "text_sha256": "not-used-by-this-test",
        "engine": "faster-whisper",
        "model": "small",
        "decode_params_hash": "params",
    }

    assert store.commit_event(event, snapshot=snapshot, segment=segment) is True
    assert store.commit_event(event, snapshot=snapshot, segment=segment) is False
    assert store.get_snapshot("j1")["committed_segments"] == 1
    assert store.get_segments("j1")[0]["text"] == "hello"


def test_same_event_id_with_different_payload_is_a_conflict(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.upsert("j1", "u")
    assert store.commit_event(_event({"segment_index": 0, "text": "hello"})) is True
    with pytest.raises(CheckpointConflict):
        store.commit_event(_event({"segment_index": 0, "text": "different"}))


def test_existing_database_is_migrated_without_losing_rows(tmp_path: Path):
    import sqlite3

    db = tmp_path / "jobs.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, url TEXT NOT NULL, "
        "stage TEXT, finished INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0, "
        "error TEXT, out_path TEXT, engine TEXT, title TEXT, created_at REAL NOT NULL, "
        "updated_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO jobs (job_id, url, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("old", "u", 1.0, 1.0),
    )
    conn.commit()
    conn.close()

    store = JobStore(db)
    row = store.get_snapshot("old")
    assert row["url"] == "u"
    assert row["status"] == "queued"
    assert row["resumable"] == 0
    assert row["progress_known"] == 0
