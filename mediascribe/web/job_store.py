"""SQLite 任务表 — Web 任务生命周期的持久层(P1-3,2026-11-20 架构评审)。

此前任务态 100% 进程内存(``app.state.jobs`` / ``app.state.job_results``):
重启即全部蒸发(进行中任务不可观测、已完成成果的取回路径丢失),
``/api/health`` 携带的 1h purge 还会让 ``/api/jobs/{id}/result`` 在一小时后
开始 404 —— markdown 明明已落 ``<workspace>/out/*.md``,却没有任何索引指路。

本模块补一张 SQLite 任务表,只做**最小持久化**:

* 提交时 upsert;终态(成功/失败/取消)写回 out_path / engine / title / error;
* 启动时把上一次进程遗留的 unfinished 任务标记为 lost(重启可观测,
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

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

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
        """终态写回(成功 / 失败 / 取消 / 启动重放 lost)。幂等,可重复调用。"""
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

    def recover_unfinished(self) -> int:
        """启动重放:上一次进程遗留的 unfinished 任务标记为 lost。

        重启后这些任务在 ``/api/jobs`` 里以失败态可见,而不是永远悬挂。
        返回标记的行数。
        """
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE jobs SET
                    finished = 1,
                    error = COALESCE(error, ?),
                    updated_at = ?
                WHERE finished = 0
                """,
                ("interrupted: server restarted while the job was in flight", time.time()),
            )
            conn.commit()
            return max(0, cur.rowcount)

    def purge_older_than(self, seconds: Optional[float] = None) -> int:
        """删除超过保留窗口的终态行;进行中的行永不清理。"""
        window = self._history_seconds if seconds is None else seconds
        cutoff = time.time() - window
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM jobs WHERE finished = 1 AND updated_at < ?",
                (cutoff,),
            )
            conn.commit()
            return max(0, cur.rowcount)

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
