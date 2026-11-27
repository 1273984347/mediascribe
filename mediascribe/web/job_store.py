"""SQLite 任务表 — Web 任务生命周期的持久层(P1-3,2026-11-20 架构评审)。

此前任务态 100% 进程内存(``app.state.jobs`` / ``app.state.job_results``):
重启即全部蒸发(进行中任务不可观测、已完成成果的取回路径丢失),
``/api/health`` 携带的 1h purge 还会让 ``/api/jobs/{id}/result`` 在一小时后
开始 404 —— markdown 明明已落 ``<workspace>/out/*.md``,却没有任何索引指路。

本模块补一张 SQLite 任务表,只做**最小持久化**:

* 提交时 upsert;终态(成功/失败/取消)写回 out_path / engine / title / error;
* 启动时把上一次进程遗留的 unfinished 任务标记为 interrupted(重启可观测,
  不再悬挂);
* ``/api/jobs`` 合并内存注册表(live)+ 本表(history);
* ``/api/jobs/{id}/result`` 在内存未命中时回源本表 + out_path 文件,
  重启与 1h purge 后仍可取回;
* purge 保留 7 天(SQLite 便宜,历史比内存态长得多)。

并发与句柄模型
--------------
**每次操作独立短连接**(开、用完即关),不在对象上持有连接:

* 测试会为同一个临时 workspace 反复 ``create_app``,常驻连接在 Windows 上
  会让 ``TemporaryDirectory`` 清理撞 ``WinError 32``(句柄泄漏);
  短连接从根上消灭该问题(本仓库 CI Windows 踩坑清单的既有教训)。
* 任务库极小(每任务几行、每批几十次写),连接开销微秒级,可忽略。
* 写串行由 ``threading.Lock`` 保证(提交在请求线程、终态在 worker 线程、
  purge 在健康探针线程);WAL 模式在 ``__init__`` 设置一次(库级持久属性),
  读不阻塞写。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from mediascribe.web.job_events import CheckpointConflict, JobEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    stage       TEXT,
    finished    INTEGER NOT NULL DEFAULT 0,
    cancelled   INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    out_path    TEXT,
    engine      TEXT,
    title       TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
)
"""

_EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_events (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(job_id, seq)
)
"""

_SEGMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_segments (
    job_id TEXT NOT NULL,
    segment_index INTEGER NOT NULL,
    attempt_id TEXT NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    engine TEXT NOT NULL,
    model TEXT NOT NULL,
    decode_params_hash TEXT NOT NULL,
    words_json TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY(job_id, segment_index)
)
"""

_SNAPSHOT_COLUMNS = {
    "status": "TEXT NOT NULL DEFAULT 'queued'",
    "attempt_id": "TEXT",
    "retry_of": "TEXT",
    "stage_index": "INTEGER NOT NULL DEFAULT 0",
    "request_json": "TEXT",
    "selection_json": "TEXT",
    "audio_path": "TEXT",
    "audio_sha256": "TEXT",
    "decode_params_hash": "TEXT",
    "stage_current": "INTEGER NOT NULL DEFAULT 0",
    "stage_total": "INTEGER NOT NULL DEFAULT 0",
    "progress_known": "INTEGER NOT NULL DEFAULT 0",
    "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
    "last_segment_index": "INTEGER NOT NULL DEFAULT -1",
    "last_segment_end": "REAL NOT NULL DEFAULT 0",
    "committed_segments": "INTEGER NOT NULL DEFAULT 0",
    "resumable": "INTEGER NOT NULL DEFAULT 0",
    "resume_block_reason": "TEXT",
    "resume_count": "INTEGER NOT NULL DEFAULT 0",
    "error_code": "TEXT",
    "recovery_json": "TEXT",
    "result_json": "TEXT",
    "checkpoint_mode": "TEXT",
    "checkpoint_window_seconds": "INTEGER",
    "checkpoint_overlap_seconds": "INTEGER",
    "checkpoint_cursor": "REAL NOT NULL DEFAULT 0",
    "checkpoint_window_index": "INTEGER NOT NULL DEFAULT -1",
    "active_checkpoint_window": "INTEGER",
    "audio_duration": "REAL",
    "detected_language": "TEXT",
    "runtime_auth_required": "INTEGER NOT NULL DEFAULT 0",
}

_DEFAULT_HISTORY_SECONDS = 7 * 24 * 3600


class JobStore:
    """任务生命周期的 SQLite 持久层(线程安全,短连接)。

    Parameters
    ----------
    db_path
        数据库文件路径;父目录不存在时自动创建。
    history_seconds
        purge 保留窗口(秒),默认 7 天。
    """

    def __init__(self, db_path: Path, *, history_seconds: float = _DEFAULT_HISTORY_SECONDS):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._history_seconds = history_seconds
        self._lock = threading.Lock()
        # WAL 是库级持久属性 —— 一次设置,后续短连接继承。
        with self._conn(create=True) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add durable event tables and snapshot columns without replacing legacy rows."""
        conn.execute("BEGIN")
        conn.execute(_EVENT_SCHEMA)
        conn.execute(_SEGMENT_SCHEMA)
        segment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(job_segments)")}
        if "words_json" not in segment_columns:
            conn.execute("ALTER TABLE job_segments ADD COLUMN words_json TEXT")
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        missing = set(_SNAPSHOT_COLUMNS) - existing
        for name, definition in _SNAPSHOT_COLUMNS.items():
            if name in missing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
        if "status" in missing:
            conn.execute(
                """
                UPDATE jobs SET status = CASE
                    WHEN cancelled = 1 THEN 'cancelled'
                    WHEN error LIKE 'interrupted:%' THEN 'interrupted'
                    WHEN finished = 1 AND error IS NOT NULL THEN 'failed'
                    WHEN finished = 1 THEN 'succeeded'
                    ELSE 'queued'
                END
                """
            )
        conn.commit()

    def close(self) -> None:
        """兼容位:短连接模型下无常驻连接可关,保留 API 供 lifespan 调用。"""

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    @contextmanager
    def _conn(self, *, create: bool = False) -> Iterator[sqlite3.Connection]:
        """短连接 contextmanager:进出即开/关。

        注意 ``with sqlite3.Connection`` 是**事务**上下文(commit/rollback),
        不关连接 —— 必须用本包装保证 Windows 句柄即时释放。

        ``create=False``(默认)以 ``mode=rw`` 打开:**库文件不存在时连接
        失败而不是重建** —— worker 线程的终态写若与测试/停机的 workspace
        清理赛跑,只会安静失败,不会在删除中的目录里把文件重新造出来
        (否则 ``TemporaryDirectory`` 清理撞 ``WinError 32``)。调用方对
        连接失败自行容错(所有写路径都包了 try/except)。
        """
        if create:
            target = str(self._db_path)
            conn = sqlite3.connect(target, check_same_thread=False)
        else:
            conn = sqlite3.connect(
                f"{self._db_path.as_uri()}?mode=rw", uri=True, check_same_thread=False
            )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 写路径
    # ------------------------------------------------------------------
    def upsert(self, job_id: str, url: str, *, out_path: Optional[str] = None) -> None:
        """任务创建时登记(或覆盖更新现有行的时间戳)。"""
        now = time.time()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO jobs (job_id, url, out_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    url = excluded.url,
                    out_path = COALESCE(excluded.out_path, jobs.out_path),
                    updated_at = excluded.updated_at
                """,
                (job_id, url, out_path, now, now),
            )
            conn.commit()

    def commit_event(
        self,
        event: JobEvent,
        *,
        snapshot: dict[str, Any] | None = None,
        segment: dict[str, Any] | None = None,
    ) -> bool:
        """Atomically append an event and apply its job and segment projections."""
        payload = event.encoded_payload()
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT job_id, attempt_id, type, payload_json FROM job_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if row is not None:
                identical = tuple(row) == (
                    event.job_id,
                    event.attempt_id,
                    event.type,
                    payload,
                )
                if identical:
                    conn.rollback()
                    return False
                raise CheckpointConflict(
                    f"event id reused with different content: {event.event_id}"
                )

            seq = event.seq
            while True:
                try:
                    conn.execute(
                        "INSERT INTO job_events (event_id, job_id, attempt_id, seq, type, payload_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            event.event_id,
                            event.job_id,
                            event.attempt_id,
                            seq,
                            event.type,
                            payload,
                            event.created_at,
                        ),
                    )
                    break
                except sqlite3.IntegrityError:
                    row = conn.execute(
                        "SELECT COALESCE(MAX(seq), -1) + 1 FROM job_events WHERE job_id = ?",
                        (event.job_id,),
                    ).fetchone()
                    seq = int(row[0])

            if snapshot:
                allowed = set(_SNAPSHOT_COLUMNS) | {
                    "stage",
                    "finished",
                    "cancelled",
                    "error",
                    "out_path",
                    "engine",
                    "title",
                }
                unknown = set(snapshot) - allowed - {"job_id", "url", "created_at"}
                if unknown:
                    raise ValueError(f"unknown snapshot columns: {sorted(unknown)}")
                mutable = {
                    key: value
                    for key, value in snapshot.items()
                    if key not in {"job_id", "url", "created_at"}
                }
                if mutable:
                    assignments = ", ".join(f"{key} = ?" for key in mutable)
                    cursor = conn.execute(
                        f"UPDATE jobs SET {assignments}, updated_at = ? WHERE job_id = ?",
                        (*mutable.values(), event.created_at, event.job_id),
                    )
                    job_exists = cursor.rowcount != 0
                else:
                    job_exists = (
                        conn.execute(
                            "SELECT 1 FROM jobs WHERE job_id = ?", (event.job_id,)
                        ).fetchone()
                        is not None
                    )
                if not job_exists:
                    if "url" not in snapshot:
                        raise ValueError("snapshot for a new job must include url")
                    row = {
                        "job_id": event.job_id,
                        "url": snapshot["url"],
                        "created_at": event.created_at,
                        "updated_at": event.created_at,
                        **mutable,
                    }
                    columns = ", ".join(row)
                    placeholders = ", ".join("?" for _ in row)
                    conn.execute(
                        f"INSERT INTO jobs ({columns}) VALUES ({placeholders})",
                        tuple(row.values()),
                    )

            if segment:
                existing = conn.execute(
                    "SELECT text, start, end, text_sha256, engine, model, decode_params_hash, words_json "
                    "FROM job_segments WHERE job_id = ? AND segment_index = ?",
                    (event.job_id, int(segment["segment_index"])),
                ).fetchone()
                incoming = (
                    segment["text"],
                    float(segment["start"]),
                    float(segment["end"]),
                    segment["text_sha256"],
                    segment["engine"],
                    segment["model"],
                    segment["decode_params_hash"],
                    segment.get("words_json"),
                )
                if existing is not None and tuple(existing) != incoming:
                    raise CheckpointConflict(
                        f"segment conflict for {event.job_id} index {segment['segment_index']}"
                    )
                if existing is None:
                    conn.execute(
                        "INSERT INTO job_segments (job_id, segment_index, attempt_id, start, end, text, "
                        "text_sha256, engine, model, decode_params_hash, words_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            event.job_id,
                            int(segment["segment_index"]),
                            segment["attempt_id"],
                            float(segment["start"]),
                            float(segment["end"]),
                            segment["text"],
                            segment["text_sha256"],
                            segment["engine"],
                            segment["model"],
                            segment["decode_params_hash"],
                            segment.get("words_json"),
                            event.created_at,
                        ),
                    )
            conn.commit()
            return True

    def get_snapshot(self, job_id: str) -> dict[str, Any] | None:
        return self.get(job_id)

    def get_segments(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM job_segments WHERE job_id = ? ORDER BY segment_index",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_segment(self, job_id: str, segment_index: int) -> Optional[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM job_segments WHERE job_id = ? AND segment_index = ?",
                (job_id, segment_index),
            ).fetchone()
        return dict(row) if row is not None else None

    def next_event_seq(self, job_id: str) -> int:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 FROM job_events WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return int(row[0])

    def get_latest_event_payload(self, job_id: str, event_type: str) -> Optional[Dict[str, Any]]:
        """Return the newest decoded payload for one durable event type."""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT payload_json FROM job_events "
                "WHERE job_id = ? AND type = ? ORDER BY seq DESC LIMIT 1",
                (job_id, event_type),
            ).fetchone()
        return json.loads(row["payload_json"]) if row is not None else None

    def mark_terminal(
        self,
        job_id: str,
        *,
        finished: bool = True,
        cancelled: bool = False,
        error: Optional[str] = None,
        out_path: Optional[str] = None,
        engine: Optional[str] = None,
        title: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> None:
        """Write legacy terminal columns; JobControl owns event-sourced transitions."""
        now = time.time()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE jobs SET
                    finished = ?,
                    cancelled = ?,
                    error = COALESCE(?, error),
                    out_path = COALESCE(?, out_path),
                    engine = COALESCE(?, engine),
                    title = COALESCE(?, title),
                    stage = COALESCE(?, stage),
                    updated_at = ?
                WHERE job_id = ?
                """,
                (
                    1 if finished else 0,
                    1 if cancelled else 0,
                    error,
                    out_path,
                    engine,
                    title,
                    stage,
                    now,
                    job_id,
                ),
            )
            conn.commit()

    def list_unfinished(self) -> list[dict[str, Any]]:
        """Return durable snapshots whose current attempt has not finished."""
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE finished = 0").fetchall()
        return [dict(row) for row in rows]

    def delete_segments(self, job_id: str) -> int:
        """Delete a job's segment projection while retaining its snapshot and events."""
        with self._lock, self._conn() as conn:
            cursor = conn.execute("DELETE FROM job_segments WHERE job_id = ?", (job_id,))
            conn.commit()
            return max(0, cursor.rowcount)

    def purge_older_than(self, seconds: Optional[float] = None) -> int:
        """Delete expired terminal jobs and their event/segment rows atomically."""
        window = self._history_seconds if seconds is None else seconds
        cutoff = time.time() - window
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT job_id FROM jobs WHERE finished = 1 AND updated_at < ?",
                (cutoff,),
            ).fetchall()
            job_ids = [row["job_id"] for row in rows]
            for job_id in job_ids:
                conn.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
                conn.execute("DELETE FROM job_segments WHERE job_id = ?", (job_id,))
                conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            conn.commit()
            return len(job_ids)

    # ------------------------------------------------------------------
    # 读路径
    # ------------------------------------------------------------------
    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row is not None else None

    def list(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        """按创建时间倒序列出历史任务(含未终态的,调用方自行合并 live 态)。"""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, job_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
