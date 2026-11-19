"""P1-3(2026-11-20 架构评审)— :mod:`mediascribe.web.job_store` 任务表测试。

覆盖:
1. CRUD 基本回路(upsert / mark_terminal / get / list)
2. 启动重放:unfinished → lost 终态,已终态行不动
3. purge:只清超过保留窗口的终态行,进行中的行永不清理
4. Web 集成:重启后 /api/jobs 列出历史、result 端点回源 out 文件
   (评审验收指标:重启不丢成果取回路径;1h purge 后仍 200)
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mediascribe.web.job_store import JobStore

try:
    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app

    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover - optional
    _HAS_FASTAPI = False


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

    def test_recover_unfinished_marks_lost(self):
        self.store.upsert("a", "u1")
        self.store.upsert("b", "u2")
        self.store.mark_terminal("b", finished=True)
        n = self.store.recover_unfinished()
        self.assertEqual(n, 1)
        a = self.store.get("a")
        self.assertEqual(a["finished"], 1)
        self.assertIn("interrupted", a["error"])
        # 已终态的行不动
        self.assertIsNone(self.store.get("b")["error"])
        # 重放幂等
        self.assertEqual(self.store.recover_unfinished(), 0)

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

    def transcribe(self, url, output=None, **kwargs):
        Path(output).write_text("# fake transcript\n", encoding="utf-8")
        return SimpleNamespace(engine="fake", metadata={"title": "Fake Title"})


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


if __name__ == "__main__":
    unittest.main()
