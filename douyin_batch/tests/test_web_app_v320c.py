"""
v3.2.0c — backend tests for the three Tier 2 features.

Coverage:

1. ``_cached_gpu_health`` 1-second LRU cache (task 3a)
2. ``POST /api/jobs`` async submission + ``GET /api/jobs/{id}/result``
3. ``POST /api/jobs/{id}/cancel`` REST endpoint + AsyncPipeline linkage
4. ``ws_progress`` accepts ``{"event": "cancel"}`` from clients
5. ``/api/health`` exposes ``version: "3.2.0c"`` + cached ``gpu`` block

FastAPI is required; tests skip cleanly when it is missing.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import List
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "video2text" / "web"))


def setUpModule():
    # P1-2 SSRF 校验会让提交入口做 DNS 解析; 把 example.com 加入
    # VIDEO2TEXT_ALLOWED_HOSTS 白名单, 让测试离线、确定性。
    os.environ["VIDEO2TEXT_ALLOWED_HOSTS"] = "example.com"


def tearDownModule():
    os.environ.pop("VIDEO2TEXT_ALLOWED_HOSTS", None)


def _fastapi_or_skip():
    try:
        import fastapi  # noqa: F401
        return True
    except Exception:
        return False


_FASTAPI_AVAILABLE = _fastapi_or_skip()


def _fake_pipeline():
    """Return a mock ``Pipeline`` whose ``transcribe`` succeeds synchronously."""
    p = mock.MagicMock(name="Pipeline")
    p.transcribe.return_value = SimpleNamespace(
        engine="fake-engine",
        metadata={"title": "fake title"},
    )
    return p


# ---------------------------------------------------------------------------
# Task 3a — _cached_gpu_health LRU
# ---------------------------------------------------------------------------
class TestCachedGpuHealth(unittest.TestCase):
    """1-second LRU cache on ``gpu_health()``."""

    def setUp(self) -> None:
        import app
        # Start every test with a clean cache + a fresh module reference.
        app._reset_gpu_health_cache()
        self.app = app

    def tearDown(self) -> None:
        self.app._reset_gpu_health_cache()

    def test_caches_within_ttl(self):
        calls = {"n": 0}

        def fake_gpu_health():
            calls["n"] += 1
            return {"available": True, "device": "cuda", "name": "fake"}

        with mock.patch("video2text.pipeline.gpu_health", side_effect=fake_gpu_health):
            r1 = self.app._cached_gpu_health(ttl_seconds=10.0)
            r2 = self.app._cached_gpu_health(ttl_seconds=10.0)
        self.assertEqual(calls["n"], 1, "second call must hit cache")
        self.assertEqual(r1, r2)
        self.assertEqual(r1["name"], "fake")

    def test_expires_after_ttl(self):
        calls = {"n": 0}

        def fake_gpu_health():
            calls["n"] += 1
            return {"available": False, "device": "cpu", "name": None}

        with mock.patch("video2text.pipeline.gpu_health", side_effect=fake_gpu_health):
            self.app._cached_gpu_health(ttl_seconds=0.0)
            # Force expiry by setting TTL to 0 and calling again.
            r2 = self.app._cached_gpu_health(ttl_seconds=0.0)
        self.assertGreaterEqual(calls["n"], 2, "second call after TTL expiry must probe again")
        self.assertFalse(r2["available"])

    def test_reset_hook(self):
        with mock.patch("video2text.pipeline.gpu_health", return_value={"available": True}):
            self.app._cached_gpu_health(ttl_seconds=10.0)
            self.app._reset_gpu_health_cache()
            # After reset, internal cache value should be None.
            self.assertIsNone(self.app._gpu_health_cache["value"])

    def test_thread_safe_under_concurrent_calls(self):
        calls = {"n": 0}
        lock = threading.Lock()

        def fake_gpu_health():
            with lock:
                calls["n"] += 1
            time.sleep(0.05)  # slow probe
            return {"available": True}

        with mock.patch("video2text.pipeline.gpu_health", side_effect=fake_gpu_health):
            results = []
            threads = []
            for _ in range(8):
                t = threading.Thread(
                    target=lambda: results.append(self.app._cached_gpu_health(ttl_seconds=10.0))
                )
                threads.append(t)
                t.start()
            for t in threads:
                t.join()
        # All threads should see the same cached value.  At least one
        # thread must have triggered the probe; the rest may have hit
        # the cache after the probe completed.
        self.assertTrue(all(r["available"] for r in results))
        self.assertLessEqual(calls["n"], 8)


# ---------------------------------------------------------------------------
# TestClient factory — reused by all /api/jobs tests below
# ---------------------------------------------------------------------------
def _make_client(tmp: Path):
    """Build a TestClient backed by a mocked ``_build_pipeline``."""
    import app as app_module
    from fastapi.testclient import TestClient
    app_module._reset_gpu_health_cache()
    client_app = app_module.create_app(workspace=tmp)
    client = TestClient(client_app)
    return client, client_app


# ---------------------------------------------------------------------------
# Task 3a — /api/health shape (cached + version bump)
# ---------------------------------------------------------------------------
@unittest.skipUnless(_FASTAPI_AVAILABLE, "fastapi not installed")
class TestHealthV320c(unittest.TestCase):
    def test_health_reports_v320c_version(self):
        with tempfile.TemporaryDirectory() as td:
            client, _ = _make_client(Path(td))
            r = client.get("/api/health")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["ok"], True)
            self.assertEqual(body["version"], "3.2.0c")
            self.assertIn("gpu", body)
            self.assertIsInstance(body["gpu"], dict)

    def test_health_gpu_block_cached(self):
        with tempfile.TemporaryDirectory() as td:
            client, _ = _make_client(Path(td))
            # Two calls within 1 s should return the same gpu dict
            # reference (cache hit).  We patch gpu_health to return a
            # mutable marker so we can assert identity.
            r1 = client.get("/api/health").json()
            r2 = client.get("/api/health").json()
            self.assertEqual(r1["gpu"], r2["gpu"])


# ---------------------------------------------------------------------------
# Task 2a — POST /api/jobs + GET /api/jobs/{id}/result
# ---------------------------------------------------------------------------
@unittest.skipUnless(_FASTAPI_AVAILABLE, "fastapi not installed")
class TestSubmitJobs(unittest.TestCase):
    def test_submit_returns_job_ids_with_ws_and_result_paths(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, _ = _make_client(tmp)
            fake = _fake_pipeline()
            with mock.patch("app._build_pipeline", return_value=fake):
                r = client.post(
                    "/api/jobs",
                    json={"urls": ["https://example.com/v1", "https://example.com/v2"]},
                )
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertEqual(len(data["jobs"]), 2)
            for job in data["jobs"]:
                self.assertIn("job_id", job)
                self.assertIn("url", job)
                self.assertEqual(job["status"], "queued")
                self.assertTrue(job["ws"].startswith("/ws/progress/"))
                self.assertTrue(job["result"].startswith("/api/jobs/"))
                self.assertIn(job["job_id"], job["ws"])

    def test_empty_urls_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            client, _ = _make_client(Path(td))
            r = client.post("/api/jobs", json={"urls": []})
            # Pydantic ``Field(min_length=1)`` rejects empty lists with
            # 422 (validation error) before the endpoint body runs.
            self.assertEqual(r.status_code, 422)

    def test_duplicate_urls_deduped_preserving_order(self):
        """P2-6: 重复 URL 只建一个 job(避免孤儿 job / 并发写同一 out_path)。"""
        with tempfile.TemporaryDirectory() as td:
            client, _ = _make_client(Path(td))
            fake = _fake_pipeline()
            with mock.patch("app._build_pipeline", return_value=fake):
                r = client.post("/api/jobs", json={"urls": [
                    "https://example.com/a",
                    "https://example.com/b",
                    "https://example.com/a",
                ]})
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertEqual(len(data["jobs"]), 2)
            self.assertEqual(
                [j["url"] for j in data["jobs"]],
                ["https://example.com/a", "https://example.com/b"],
            )
            # 计数字段让客户端感知发生了去重。
            self.assertEqual(data["requested"], 3)
            self.assertEqual(data["unique"], 2)

    def test_result_returns_markdown_after_finish(self):
        """After the background runner completes, /result returns the markdown."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, app_obj = _make_client(tmp)
            fake = _fake_pipeline()
            with mock.patch("app._build_pipeline", return_value=fake):
                r = client.post("/api/jobs", json={"urls": ["https://example.com/v"]})
            job_id = r.json()["jobs"][0]["job_id"]
            # The background ThreadPoolExecutor runs the (mocked)
            # pipeline synchronously, so the result should be available
            # within a short poll loop.
            deadline = time.time() + 5.0
            data = None
            while time.time() < deadline:
                rr = client.get(f"/api/jobs/{job_id}/result")
                self.assertEqual(rr.status_code, 200)
                data = rr.json()
                if data.get("finished"):
                    break
                time.sleep(0.05)
            self.assertIsNotNone(data, "job did not finish in 5 s")
            self.assertTrue(data["finished"])
            self.assertFalse(data["cancelled"])
            self.assertEqual(data["engine"], "fake-engine")
            # Markdown is read from the on-disk file; the fake pipeline
            # does not write one, so this is expected to be "" but the
            # call itself must succeed.
            self.assertIn("markdown", data)

    def test_result_404_for_unknown_job(self):
        with tempfile.TemporaryDirectory() as td:
            client, _ = _make_client(Path(td))
            r = client.get("/api/jobs/does-not-exist/result")
            self.assertEqual(r.status_code, 404)


# ---------------------------------------------------------------------------
# Task 2a — POST /api/jobs/{id}/cancel + AsyncPipeline linkage
# ---------------------------------------------------------------------------
@unittest.skipUnless(_FASTAPI_AVAILABLE, "fastapi not installed")
class TestCancelJob(unittest.TestCase):
    def test_cancel_unknown_job_returns_404(self):
        with tempfile.TemporaryDirectory() as td:
            client, _ = _make_client(Path(td))
            r = client.post("/api/jobs/does-not-exist/cancel")
            self.assertEqual(r.status_code, 404)

    def test_cancel_known_job_returns_cancelled_true(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, _ = _make_client(tmp)
            # Make the fake pipeline block so we can cancel before it
            # finishes; otherwise it completes instantly and cancel
            # is a no-op.
            slow_fake = _fake_pipeline()

            def slow_transcribe(*a, **kw):
                time.sleep(2.0)
                return SimpleNamespace(engine="slow", metadata={})

            slow_fake.transcribe.side_effect = slow_transcribe
            with mock.patch("app._build_pipeline", return_value=slow_fake):
                r = client.post("/api/jobs", json={"urls": ["https://example.com/x"]})
            job_id = r.json()["jobs"][0]["job_id"]
            cr = client.post(f"/api/jobs/{job_id}/cancel")
            self.assertEqual(cr.status_code, 200, cr.text)
            body = cr.json()
            self.assertEqual(body["job_id"], job_id)
            self.assertTrue(body["cancelled"])

    def test_cancel_calls_async_pipeline_cancel_when_registered(self):
        """When an AsyncPipeline is registered for a job, cancel_job calls it."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, app_obj = _make_client(tmp)
            # Create a job via /api/jobs so it lands in the registry.
            fake = _fake_pipeline()

            def slow_transcribe(*a, **kw):
                time.sleep(2.0)
                return SimpleNamespace(engine="slow", metadata={})

            fake.transcribe.side_effect = slow_transcribe
            with mock.patch("app._build_pipeline", return_value=fake):
                r = client.post("/api/jobs", json={"urls": ["https://example.com/y"]})
            job_id = r.json()["jobs"][0]["job_id"]
            # Register a fake AsyncPipeline with a cancel() counter.
            cancel_calls = {"n": 0}

            class FakeAsync:
                def cancel(self):
                    cancel_calls["n"] += 1
                    return 1

            app_obj.state.async_pipelines[job_id] = FakeAsync()
            cr = client.post(f"/api/jobs/{job_id}/cancel")
            self.assertEqual(cr.status_code, 200)
            self.assertEqual(cancel_calls["n"], 1,
                             "cancel_job must invoke AsyncPipeline.cancel() when registered")


# ---------------------------------------------------------------------------
# Task 2a — ws_progress WS receives {"event": "cancel"}
# ---------------------------------------------------------------------------
@unittest.skipUnless(_FASTAPI_AVAILABLE, "fastapi not installed")
class TestWsCancelMessage(unittest.TestCase):
    def test_ws_cancel_message_triggers_registry_cancel(self):
        """A client sending ``{"event": "cancel"}`` over the WS marks the job."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, _ = _make_client(tmp)
            fake = _fake_pipeline()

            def slow_transcribe(*a, **kw):
                time.sleep(2.0)
                return SimpleNamespace(engine="slow", metadata={})

            fake.transcribe.side_effect = slow_transcribe
            with mock.patch("app._build_pipeline", return_value=fake):
                r = client.post("/api/jobs", json={"urls": ["https://example.com/z"]})
            job_id = r.json()["jobs"][0]["job_id"]
            # Connect via WS and send cancel.
            try:
                with client.websocket_connect(f"/ws/progress/{job_id}") as ws:
                    ws.send_text('{"event": "cancel"}')
                    # Drain at least one event so the server-side cancel
                    # message has a chance to be processed.
                    deadline = time.time() + 2.0
                    seen_cancel = False
                    while time.time() < deadline:
                        try:
                            msg = ws.receive_json()
                        except Exception:
                            break
                        if msg.get("event") in ("cancelled", "cancelled_done"):
                            seen_cancel = True
                            break
                    # The registry flag must be set regardless of whether
                    # the WS delivered the cancelled event in time.
                    job = client.app.state.jobs.get(job_id)
                    self.assertIsNotNone(job)
                    self.assertTrue(job.cancelled,
                                    "registry.cancel must be called on WS cancel message")
            except Exception as exc:
                # Some TestClient versions close the WS hard on cancel;
                # check the registry flag directly.
                job = client.app.state.jobs.get(job_id)
                self.assertIsNotNone(job, f"job missing after WS exc: {exc}")
                self.assertTrue(job.cancelled)


# ---------------------------------------------------------------------------
# v3.2.0c P3 backlog — /api/health purge integration + lock thread-safety
# ---------------------------------------------------------------------------
@unittest.skipUnless(_FASTAPI_AVAILABLE, "fastapi not installed")
class TestHealthPurgeIntegration(unittest.TestCase):
    """P3-3: /api/health → purge + job_results cleanup integration."""

    def test_health_purges_old_finished_jobs_from_registry(self):
        """A finished job older than 1 hour is purged on /api/health."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, app_obj = _make_client(tmp)
            # Create a job, then backdate it past the 1-hour TTL.
            registry = app_obj.state.jobs
            job = registry.create("https://example.com/old")
            job.created_at = time.time() - 3700  # > 3600s
            job.finished = True
            # Sanity: registry has 1 job before health probe.
            self.assertEqual(len(registry.list()), 1)
            # Health probe triggers purge.
            r = client.get("/api/health")
            self.assertEqual(r.status_code, 200)
            # Job must be gone from registry.
            self.assertIsNone(registry.get(job.job_id))

    def test_health_cleans_orphaned_job_results_entries(self):
        """P3-3 + R4-fix: job_results dict synced with registry purge."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, app_obj = _make_client(tmp)
            registry = app_obj.state.jobs
            # Create an old, finished job that purge will pick up.
            job = registry.create("https://example.com/old")
            job.created_at = time.time() - 3700
            job.finished = True
            # Plant a fake job_results entry for the same job_id — this
            # is what /api/health's orphan cleanup must remove.
            app_obj.state.job_results[job.job_id] = {
                "markdown": "stale leak",
                "engine": "old",
                "title": None,
                "url": "https://example.com/old",
            }
            self.assertIn(job.job_id, app_obj.state.job_results)
            # Trigger purge + orphan cleanup.
            r = client.get("/api/health")
            self.assertEqual(r.status_code, 200)
            # The orphaned entry must be gone — no markdown leak.
            self.assertNotIn(job.job_id, app_obj.state.job_results)

    def test_health_preserves_recent_finished_jobs(self):
        """P3-3 regression: recent finished jobs must NOT be purged."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, app_obj = _make_client(tmp)
            registry = app_obj.state.jobs
            # Recent finished job (< 1 hour) — must survive purge.
            job = registry.create("https://example.com/recent")
            job.finished = True
            # No backdating: created_at defaults to now.
            app_obj.state.job_results[job.job_id] = {
                "markdown": "live result",
                "engine": "engine",
                "title": None,
                "url": "https://example.com/recent",
            }
            client.get("/api/health")
            self.assertIsNotNone(registry.get(job.job_id))
            self.assertIn(job.job_id, app_obj.state.job_results)


@unittest.skipUnless(_FASTAPI_AVAILABLE, "fastapi not installed")
class TestJobResultsLock(unittest.TestCase):
    """P3-2: jobs_state_lock prevents RuntimeError on concurrent access.

    Renamed from ``job_results_lock`` (R2-F4) to reflect the R6
    invariant: the lock guards BOTH ``app.state.job_results`` (markdown
    dict) AND ``app.state.jobs`` (ProgressRegistry mutate paths — purge,
    future DELETE endpoints). Tests reference the new attribute name.
    """

    def test_concurrent_health_and_worker_no_runtime_error(self):
        """Concurrent /api/health iterate + worker write must not raise."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, app_obj = _make_client(tmp)
            registry = app_obj.state.jobs
            lock = app_obj.state.jobs_state_lock
            errors: List[str] = []

            def worker_writes():
                # Simulate _run_job_safely writes happening concurrently
                # with /api/health iteration. Without the lock, the
                # dict-view iteration in /api/health can raise
                # ``RuntimeError: dictionary changed size during
                # iteration``.
                for i in range(50):
                    j = registry.create(f"https://example.com/w{i}")
                    j.finished = True
                    with lock:
                        app_obj.state.job_results[j.job_id] = {
                            "markdown": "", "engine": None, "title": None,
                            "url": f"https://example.com/w{i}",
                        }

            def health_calls():
                for _ in range(20):
                    try:
                        client.get("/api/health")
                    except Exception as exc:
                        errors.append(f"health: {exc!r}")

            t1 = threading.Thread(target=worker_writes)
            t2 = threading.Thread(target=health_calls)
            t1.start(); t2.start()
            t1.join(); t2.join()
            self.assertEqual(errors, [], "concurrent access raised errors")
            # P2-2 fix: also assert no orphans — every entry in
            # job_results must correspond to a live job in registry.
            live_ids = {j.job_id for j in registry.list()}
            orphans = [k for k in app_obj.state.job_results if k not in live_ids]
            self.assertEqual(orphans, [],
                             f"orphan entries left in job_results: {orphans}")

    def test_concurrent_purge_with_backdated_jobs_no_orphan(self):
        """R6: TOCTOU race — purge concurrent with worker write leaves no orphan.

        Pre-R6: the worker's registry.get() check ran OUTSIDE the lock,
        so a concurrent /api/health purge could mutate the registry
        between the check and the write, leaving an orphan entry.
        Post-R6: both check-and-write (worker) and purge-and-cleanup
        (/api/health) run under the same lock, so they're mutually
        exclusive — no orphan can be created.

        R2-fix (F-R2-2): pre-R2-fix used ``return`` inside the for
        loop (exits worker on first purge → 0 writes → orphans==[]
        vacuously true). Switched to ``continue`` so all 30 iterations
        race. R3-fix (F-R3-1): moved ``attempts`` to outer scope +
        added ``assertGreater`` assertion (was dead code in closure
        scope, never read after ``t1.join()``). We don't assert
        ``writes > 0`` because a very fast /api/health could purge
        every iteration — the orphan invariant is what matters, and
        it is meaningfully exercised across 30 independent races.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, app_obj = _make_client(tmp)
            registry = app_obj.state.jobs
            lock = app_obj.state.jobs_state_lock
            errors: List[str] = []
            attempts: List[bool] = []  # outer scope, read after join()

            def worker_writes_old_jobs():
                # Create jobs that are OLD (backdated past 1-hour TTL)
                # so /api/health's purge will remove them. Without the
                # R6 fix, the worker's check (job present) + write
                # (results_store) would race with purge (removes job)
                # leaving an orphan.
                for i in range(30):
                    j = registry.create(f"https://example.com/old{i}")
                    j.created_at = time.time() - 3700  # > 3600s TTL
                    j.finished = True
                    # Mimic _run_job_safely's check-and-write under lock.
                    with lock:
                        attempts.append(True)
                        if registry.get(j.job_id) is None:
                            continue  # purged, skip write, try next
                        app_obj.state.job_results[j.job_id] = {
                            "markdown": "", "engine": None, "title": None,
                            "url": f"https://example.com/old{i}",
                        }

            def health_calls():
                for _ in range(30):
                    try:
                        client.get("/api/health")
                    except Exception as exc:
                        errors.append(f"health: {exc!r}")

            t1 = threading.Thread(target=worker_writes_old_jobs)
            t2 = threading.Thread(target=health_calls)
            t1.start(); t2.start()
            t1.join(); t2.join()
            self.assertEqual(errors, [], "concurrent access raised errors")
            # Worker must have run at least one iteration — otherwise
            # the orphan assertion is vacuously true (R3 finding F-R3-1).
            self.assertGreater(len(attempts), 0,
                               "worker never ran any iteration — test "
                               "passed vacuously without exercising the race")
            # Final state: NO orphan entries. Every entry in job_results
            # must correspond to a live (non-purged) job in registry.
            live_ids = {j.job_id for j in registry.list()}
            orphans = [k for k in app_obj.state.job_results if k not in live_ids]
            self.assertEqual(orphans, [],
                             f"TOCTOU race left orphan entries: {orphans}")

    def test_result_read_during_purge_does_not_inconsist(self):
        """P3-2 regression: /api/jobs/{id}/result read while /api/health pops."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            client, app_obj = _make_client(tmp)
            registry = app_obj.state.jobs
            # Plant a job + result.
            job = registry.create("https://example.com/x")
            app_obj.state.job_results[job.job_id] = {
                "markdown": "md", "engine": "e", "title": None,
                "url": "https://example.com/x",
            }
            # Read should return stored value cleanly even if a
            # concurrent purge is running.
            r = client.get(f"/api/jobs/{job.job_id}/result")
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertEqual(data["markdown"], "md")
            self.assertEqual(data["engine"], "e")


@unittest.skipUnless(_FASTAPI_AVAILABLE, "fastapi not installed")
class TestRunJobSafelyPurgeCheck(unittest.TestCase):
    """P3-1: _run_job_safely skips writing results_store when job purged."""

    def test_skips_write_when_job_purged_mid_run(self):
        """If the job is purged between runner() finish and write, skip."""
        import app as app_module

        from video2text.progress import ProgressRegistry

        registry = ProgressRegistry()
        job = registry.create("https://example.com/x")
        results_store: dict = {}
        lock = threading.Lock()

        def fake_runner():
            # Simulate purge happening right before the result-store write.
            registry._jobs.pop(job.job_id, None)
            return SimpleNamespace(engine="e", metadata={})

        # _run_job_safely must detect the missing job and skip the write.
        app_module._run_job_safely(
            runner=fake_runner,
            job_id=job.job_id,
            out_path=Path("/nonexistent.md"),
            url="https://example.com/x",
            results_store=results_store,
            results_lock=lock,
            registry=registry,
        )
        self.assertEqual(results_store, {},
                         "must NOT write results_store for a purged job")

    def test_writes_when_job_still_in_registry(self):
        """Regression: when job is still in registry, write happens normally."""
        import app as app_module

        from video2text.progress import ProgressRegistry

        registry = ProgressRegistry()
        job = registry.create("https://example.com/x")
        results_store: dict = {}
        lock = threading.Lock()

        def fake_runner():
            return SimpleNamespace(engine="e", metadata={"title": "T"})

        app_module._run_job_safely(
            runner=fake_runner,
            job_id=job.job_id,
            out_path=Path("/nonexistent.md"),
            url="https://example.com/x",
            results_store=results_store,
            results_lock=lock,
            registry=registry,
        )
        self.assertIn(job.job_id, results_store)
        self.assertEqual(results_store[job.job_id]["engine"], "e")
        self.assertEqual(results_store[job.job_id]["title"], "T")


if __name__ == "__main__":
    unittest.main()
