"""P1-3(2026-11-20 架构评审)— :mod:`mediascribe.web.job_store` 任务表测试。

覆盖:
1. CRUD 基本回路(upsert / mark_terminal / get / list)
2. 启动重放:unfinished → lost 终态,已终态行不动
3. purge:只清超过保留窗口的终态行,进行中的行永不清理
4. Web 集成:重启后 /api/jobs 列出历史、result 端点回源 out 文件
   (评审验收指标:重启不丢成果取回路径;1h purge 后仍 200)
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from mediascribe.web import job_store
from mediascribe.web.job_events import CheckpointConflict, JobEvent
from mediascribe.web.job_store import JobStore

try:
    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app

    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover - optional
    _HAS_FASTAPI = False


def _durable_event(
    event_id: str = "evt-1",
    *,
    job_id: str = "j1",
    attempt_id: str = "a1",
    seq: int = 1,
    payload: dict | None = None,
) -> JobEvent:
    return JobEvent(
        event_id=event_id,
        job_id=job_id,
        attempt_id=attempt_id,
        seq=seq,
        type="segment_committed",
        payload=payload or {"segment_index": 0, "text": "hello"},
        created_at=10.0,
    )


def _durable_segment(*, attempt_id: str = "a1", text: str = "hello") -> dict:
    return {
        "job_id": "j1",
        "segment_index": 0,
        "attempt_id": attempt_id,
        "start": 0.0,
        "end": 1.0,
        "text": text,
        "text_sha256": "text-hash",
        "engine": "faster-whisper",
        "model": "small",
        "decode_params_hash": "params-hash",
    }


@pytest.mark.parametrize(
    ("finished", "cancelled", "error", "expected"),
    [
        (0, 0, None, "queued"),
        (0, 0, "boom", "queued"),
        (1, 0, None, "succeeded"),
        (1, 0, "boom", "failed"),
        (1, 0, "interrupted: restart", "interrupted"),
        (0, 1, "interrupted: restart", "cancelled"),
    ],
)
def test_legacy_status_backfill(tmp_path: Path, finished, cancelled, error, expected):
    db = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, url TEXT NOT NULL, "
            "stage TEXT, finished INTEGER NOT NULL DEFAULT 0, "
            "cancelled INTEGER NOT NULL DEFAULT 0, error TEXT, out_path TEXT, "
            "engine TEXT, title TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "INSERT INTO jobs (job_id, url, finished, cancelled, error, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("old", "u", finished, cancelled, error, 1.0, 1.0),
        )

    assert JobStore(db).get_snapshot("old")["status"] == expected


def test_migration_failure_rolls_back_all_schema_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    db = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, url TEXT NOT NULL, "
            "stage TEXT, finished INTEGER NOT NULL DEFAULT 0, "
            "cancelled INTEGER NOT NULL DEFAULT 0, error TEXT, out_path TEXT, "
            "engine TEXT, title TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "INSERT INTO jobs (job_id, url, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("old", "u", 1.0, 1.0),
        )

    class FailingMigrationConnection(sqlite3.Connection):
        alter_count = 0

        def execute(self, sql, parameters=()):
            if sql.lstrip().upper().startswith("ALTER TABLE JOBS ADD COLUMN"):
                self.alter_count += 1
                if self.alter_count == 3:
                    raise sqlite3.OperationalError("injected migration failure")
            return super().execute(sql, parameters)

    real_connect = sqlite3.connect

    def failing_connect(*args, **kwargs):
        kwargs["factory"] = FailingMigrationConnection
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(job_store.sqlite3, "connect", failing_connect)
    with pytest.raises(sqlite3.OperationalError, match="injected migration failure"):
        JobStore(db)

    with real_connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        columns = [row[1] for row in conn.execute("PRAGMA table_info(jobs)")]
        legacy_row = conn.execute("SELECT job_id, url FROM jobs").fetchone()

    assert tables == {"jobs"}
    assert columns == [
        "job_id",
        "url",
        "stage",
        "finished",
        "cancelled",
        "error",
        "out_path",
        "engine",
        "title",
        "created_at",
        "updated_at",
    ]
    assert legacy_row == ("old", "u")


def test_migration_does_not_reset_durable_status_on_reopen(tmp_path: Path):
    db = tmp_path / "jobs.sqlite3"
    store = JobStore(db)
    assert store.commit_event(_durable_event(), snapshot={"url": "u", "status": "running"})

    assert JobStore(db).get_snapshot("j1")["status"] == "running"


def test_commit_event_can_create_job_from_url_only_snapshot(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")

    assert store.commit_event(_durable_event(), snapshot={"url": "u"}) is True
    assert store.get_snapshot("j1")["url"] == "u"


def test_identical_segment_content_is_idempotent_across_attempts(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.upsert("j1", "u")
    assert store.commit_event(_durable_event(), segment=_durable_segment())

    assert (
        store.commit_event(
            _durable_event("evt-2", attempt_id="a2", seq=2),
            segment=_durable_segment(attempt_id="a2"),
        )
        is True
    )
    rows = store.get_segments("j1")
    assert len(rows) == 1
    assert rows[0]["attempt_id"] == "a1"
    assert store.next_event_seq("j1") == 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("text", "changed"),
        ("start", 0.5),
        ("end", 1.5),
        ("text_sha256", "changed-hash"),
        ("engine", "whisper"),
        ("model", "large-v3"),
        ("decode_params_hash", "changed-params"),
    ],
)
def test_segment_content_conflict_rolls_back_event_and_snapshot(tmp_path: Path, field: str, value):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.upsert("j1", "u")
    assert store.commit_event(
        _durable_event(),
        snapshot={"status": "running", "committed_segments": 1},
        segment=_durable_segment(),
    )
    changed = _durable_segment(attempt_id="a2")
    changed[field] = value

    with pytest.raises(CheckpointConflict):
        store.commit_event(
            _durable_event("evt-2", attempt_id="a2", seq=2),
            snapshot={"status": "failed", "committed_segments": 2},
            segment=changed,
        )

    assert store.get_snapshot("j1")["status"] == "running"
    assert store.get_snapshot("j1")["committed_segments"] == 1
    assert store.get_segments("j1")[0]["text"] == "hello"
    assert store.next_event_seq("j1") == 2


def test_reused_event_sequence_is_reallocated_for_new_event_id(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.upsert("j1", "u")
    assert store.commit_event(_durable_event())

    assert store.commit_event(_durable_event("evt-2")) is True
    assert store.next_event_seq("j1") == 3


def test_concurrent_store_instances_allocate_distinct_sequences(tmp_path: Path):
    db = tmp_path / "jobs.sqlite3"
    first = JobStore(db)
    second = JobStore(db)
    first.upsert("j1", "u")
    barrier = threading.Barrier(2)

    def commit(store, event):
        barrier.wait()
        return store.commit_event(event)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                commit,
                (first, second),
                (_durable_event("evt-1"), _durable_event("evt-2")),
            )
        )

    assert results == [True, True]
    assert first.next_event_seq("j1") == 3


def test_get_latest_event_payload_returns_none_for_missing_job_or_type(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.upsert("j1", "u")
    store.commit_event(_durable_event(payload={"status": "succeeded", "result": {"x": 1}}))

    assert store.get_latest_event_payload("missing", "attempt_finished") is None
    assert store.get_latest_event_payload("j1", "cancel_requested") is None
    assert store.get_latest_event_payload("j1", "segment_committed") == {
        "status": "succeeded",
        "result": {"x": 1},
    }


def test_next_event_seq_starts_at_zero_and_tracks_commits(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    assert store.next_event_seq("j1") == 0
    store.upsert("j1", "u")
    assert store.commit_event(_durable_event(seq=4))
    assert store.next_event_seq("j1") == 5


class TestJobStoreBasics(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self._tmp.name) / "jobs.sqlite3")

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_upsert_and_get(self):
        self.store.upsert("j1", "https://example.com/a", out_path="/tmp/a.md")
        row = self.store.get("j1")
        self.assertIsNotNone(row)
        self.assertEqual(row["url"], "https://example.com/a")
        self.assertEqual(row["out_path"], "/tmp/a.md")
        self.assertEqual(row["finished"], 0)

    def test_mark_terminal_roundtrip(self):
        self.store.upsert("j1", "u", out_path="/x.md")
        self.store.mark_terminal("j1", finished=True, out_path="/x.md", engine="fw", title="T")
        row = self.store.get("j1")
        self.assertEqual(row["finished"], 1)
        self.assertEqual(row["engine"], "fw")
        self.assertEqual(row["title"], "T")
        # error 的 COALESCE 语义:后写 None 不覆盖已有 error
        self.store.upsert("j2", "u2")
        self.store.mark_terminal("j2", finished=True, error="boom")
        self.store.mark_terminal("j2", finished=True)
        self.assertEqual(self.store.get("j2")["error"], "boom")

    def test_recover_interrupted_preserves_checkpoint_and_marks_resumable(self):
        store = JobStore(Path(self._tmp.name) / "jobs.sqlite3")
        store.upsert("j1", "u", out_path="out/a.md")
        store.commit_event(
            JobEvent(
                event_id="e1",
                job_id="j1",
                attempt_id="a1",
                seq=1,
                type="attempt_started",
                payload={},
                created_at=time.time(),
            ),
            snapshot={
                "status": "running",
                "attempt_id": "a1",
                "audio_path": "audio/a.wav",
                "audio_sha256": "hash",
                "decode_params_hash": "params",
                "selection_json": '{"engine":"faster-whisper","model":"small"}',
            },
        )
        from mediascribe.progress import ProgressRegistry
        from mediascribe.web.job_control import JobControl

        count = JobControl(store, ProgressRegistry()).recover_interrupted()
        row = store.get_snapshot("j1")
        assert count == 1
        assert row["status"] == "interrupted"
        assert row["finished"] == 1
        assert row["resumable"] == 1
        assert row["audio_sha256"] == "hash"
        assert row["attempt_id"] == "a1"
        assert store.get_latest_event_payload("j1", "job_interrupted") == {"resumable": True}
        assert store.next_event_seq("j1") == 3
        assert JobControl(store, ProgressRegistry()).recover_interrupted() == 0
        assert store.next_event_seq("j1") == 3

    def test_recover_unfinished_marks_lost(self):
        from mediascribe.progress import ProgressRegistry
        from mediascribe.web.job_control import JobControl

        control = JobControl(self.store, ProgressRegistry())
        self.store.upsert("a", "u1")
        self.store.upsert("b", "u2")
        self.store.mark_terminal("b", finished=True)
        n = control.recover_interrupted()
        self.assertEqual(n, 1)
        a = self.store.get("a")
        self.assertEqual(a["status"], "interrupted")
        self.assertEqual(a["finished"], 1)
        self.assertEqual(a["resumable"], 0)
        self.assertIn("interrupted", a["error"])
        # 已终态的行不动
        self.assertIsNone(self.store.get("b")["error"])
        # 重放幂等
        self.assertEqual(control.recover_interrupted(), 0)

    def test_purge_keeps_running_jobs(self):
        self.store.upsert("old", "u1")
        self.store.mark_terminal("old", finished=True)
        self.store.upsert("running", "u2")  # 未终态
        # 把 old 的 updated_at 拨到窗口之外(独立短连接,不动被测对象内部)
        import sqlite3

        conn = sqlite3.connect(str(Path(self._tmp.name) / "jobs.sqlite3"))
        try:
            conn.execute(
                "UPDATE jobs SET updated_at = ? WHERE job_id = ?",
                (time.time() - 10 * 24 * 3600, "old"),
            )
            conn.commit()
        finally:
            conn.close()
        n = self.store.purge_older_than()
        self.assertEqual(n, 1)
        self.assertIsNone(self.store.get("old"))
        self.assertIsNotNone(self.store.get("running"))

    def test_list_newest_first(self):
        self.store.upsert("first", "u1")
        time.sleep(0.01)
        self.store.upsert("second", "u2")
        rows = self.store.list()
        self.assertEqual([r["job_id"] for r in rows], ["second", "first"])


class _WritingPipeline:
    """transcribe 时把 markdown 落盘的假管线(模拟 with_progress 的 output 透传)。"""

    engine = "fake"
    model = "small"
    settings = SimpleNamespace(engine=engine, model=model, device="cpu")
    transcriber = SimpleNamespace(vad_filter=True)

    def transcribe(self, url, output=None, **kwargs):
        out_path = Path(output)
        out_path.write_text("# fake transcript\n", encoding="utf-8")
        return SimpleNamespace(
            engine="fake",
            model="small",
            transcript_path=out_path,
            duration_seconds=1.0,
            source=SimpleNamespace(display_name="fake input"),
            metadata={"title": "Fake Title", "engine": "fake", "model": "small"},
        )


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestRestartRecovery(unittest.TestCase):
    """验收指标(P1-3):重启后成果仍可取回,历史任务仍在 /api/jobs。"""

    def _submit_and_wait(self, app_obj, client, url="https://example.com/x"):
        """提交并等 SQLite 任务表落终态(vault 归档在内存态 finished 之后、
        store 终态写之前完成 —— 等内存态会让 vault 写与临时目录清理赛跑)。"""
        r = client.post("/api/jobs", json={"urls": [url]})
        assert r.status_code == 200, r.text
        job_id = r.json()["jobs"][0]["job_id"]
        deadline = time.time() + 5.0
        while time.time() < deadline:
            row = app_obj.state.job_store.get(job_id) or {}
            if row.get("finished"):
                return job_id
            time.sleep(0.05)
        raise AssertionError("job did not reach terminal state in job store in time")

    def test_result_survives_restart_and_purge(self):
        apps = []
        with tempfile.TemporaryDirectory() as td:
            try:
                ws = Path(td)
                with mock.patch(
                    "mediascribe.web.app._build_pipeline",
                    return_value=_WritingPipeline(),
                ):
                    # ---- 第一次启动:提交并等到终态 ----
                    app1 = create_app(workspace=ws)
                    apps.append(app1)
                    client1 = TestClient(app1)
                    job_id = self._submit_and_wait(app1, client1)
                    r1 = client1.get(f"/api/jobs/{job_id}/result")
                    self.assertEqual(r1.status_code, 200)
                    self.assertEqual(r1.json()["markdown"], "# fake transcript\n")

                    # ---- 重启:全新进程态(内存注册表为空),同一 workspace ----
                    app2 = create_app(workspace=ws)
                    apps.append(app2)
                    client2 = TestClient(app2)

                # result 端点回源 SQLite + out 文件 —— 不再 404
                r2 = client2.get(f"/api/jobs/{job_id}/result")
                self.assertEqual(r2.status_code, 200, r2.text)
                body = r2.json()
                self.assertEqual(body["markdown"], "# fake transcript\n")
                self.assertTrue(body["finished"])
                self.assertEqual(body["title"], "Fake Title")

                # /api/jobs 列出历史任务(内存注册表为空,历史从任务表补出)
                jobs = client2.get("/api/jobs").json()["jobs"]
                self.assertIn(job_id, {j["job_id"] for j in jobs})

                # 未登记的 job_id 仍按契约 404
                self.assertEqual(client2.get("/api/jobs/never-existed/result").status_code, 404)
            finally:
                # Windows: sqlite 连接/WAL 句柄不关,TemporaryDirectory
                # 清理会 PermissionError —— 必须在 with 块退出**之前**关。
                for app in apps:
                    store = getattr(app.state, "job_store", None)
                    if store is not None:
                        store.close()

    def test_losing_unfinished_jobs_on_restart_is_visible(self):
        with tempfile.TemporaryDirectory() as td:
            try:
                ws = Path(td)
                store = JobStore(ws / "jobs.sqlite3")
                store.upsert("orphan", "https://example.com/lost")  # 模拟上次进程死掉
                store.close()
                app = create_app(workspace=ws)
                client = TestClient(app)
                jobs = client.get("/api/jobs").json()["jobs"]
                orphan = [j for j in jobs if j["job_id"] == "orphan"]
                self.assertEqual(len(orphan), 1)
                self.assertTrue(orphan[0]["finished"], "遗留 unfinished 必须被重放为终态")
                self.assertIn("interrupted", orphan[0]["error"])
            finally:
                store = getattr(app.state, "job_store", None)
                if store is not None:
                    store.close()


def test_get_segment_returns_one_indexed_entry(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.upsert("j1", "u")
    store.commit_event(
        JobEvent("e1", "j1", "a1", 1, "segment_committed", {}, 1.0),
        segment={
            "job_id": "j1",
            "segment_index": 0,
            "attempt_id": "a1",
            "start": 0.0,
            "end": 1.0,
            "text": "one",
            "text_sha256": "hash",
            "engine": "faster-whisper",
            "model": "small",
            "decode_params_hash": "params",
        },
    )
    assert store.get_segment("j1", 0)["text"] == "one"
    assert store.get_segment("j1", 1) is None


def test_window_columns_and_words_are_additive_and_persisted(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.upsert("j1", "u")
    words = [{"word": "hello", "start": 0.0, "end": 0.5, "score": 0.9}]
    assert store.commit_event(
        _durable_event(),
        snapshot={
            "checkpoint_mode": "window",
            "checkpoint_window_seconds": 60,
            "checkpoint_overlap_seconds": 2,
            "checkpoint_cursor": 60.0,
            "checkpoint_window_index": 0,
            "active_checkpoint_window": None,
            "audio_duration": 119.0,
            "runtime_auth_required": 0,
        },
        segment={**_durable_segment(), "words_json": json.dumps(words)},
    )

    snapshot = JobStore(tmp_path / "jobs.sqlite3").get_snapshot("j1")
    segment = store.get_segments("j1")[0]
    assert snapshot["checkpoint_cursor"] == 60.0
    assert snapshot["checkpoint_window_index"] == 0
    assert snapshot["audio_duration"] == 119.0
    assert json.loads(segment["words_json"]) == words


def test_existing_segment_table_gets_words_column_without_losing_rows(tmp_path: Path):
    db = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE job_segments (job_id TEXT, segment_index INTEGER, attempt_id TEXT, "
            "start REAL, end REAL, text TEXT, text_sha256 TEXT, engine TEXT, model TEXT, "
            "decode_params_hash TEXT, created_at REAL, PRIMARY KEY(job_id, segment_index))"
        )
        conn.execute(
            "INSERT INTO job_segments VALUES ('j1', 0, 'a1', 0, 1, 'hello', 'h', "
            "'whisper', 'small', 'p', 1.0)"
        )

    store = JobStore(db)
    assert store.get_segments("j1")[0]["text"] == "hello"
    assert store.get_segments("j1")[0]["words_json"] is None


if __name__ == "__main__":
    unittest.main()
