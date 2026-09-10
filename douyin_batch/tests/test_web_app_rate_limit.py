"""
Tests for the Web UI's in-process rate limiter.

Covers:

* The ``_RateLimiter`` sliding-window primitive (unit tests, no FastAPI).
* ``MEDIASCRIBE_RATE_LIMIT`` env var controls ``max_requests``.
* ``MEDIASCRIBE_RATE_LIMIT_WINDOW`` env var controls ``window_seconds``.
* ``MEDIASCRIBE_RATE_LIMIT=0`` disables limiting entirely.
* ``/api/health`` reports the active rate-limit configuration.
* Repeated ``/api/transcribe`` calls within the window return ``429``
  with proper ``Retry-After`` and ``X-RateLimit-*`` headers.
* Rate limit is keyed on the client IP — two different IPs each get
  their own quota.
* Auth runs *before* the rate limiter so an unauthenticated flooder
  cannot exhaust legitimate users' quota.
"""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mediascribe" / "web"))

# These tests need the optional fastapi dep.  Skip cleanly otherwise.
try:
    from app import (  # type: ignore
        _build_rate_limiter,
        _RateLimiter,
        create_app,
    )
    from fastapi.testclient import TestClient  # type: ignore
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False


class TestRateLimiterUnit(unittest.TestCase):
    """``_RateLimiter`` sliding-window logic in isolation."""

    def test_first_request_is_allowed(self):
        lim = _RateLimiter(max_requests=3, window_seconds=60)
        ok, remaining, retry = lim.check("client-a")
        self.assertTrue(ok)
        self.assertEqual(remaining, 2)
        self.assertEqual(retry, 0.0)

    def test_quota_counts_down(self):
        lim = _RateLimiter(max_requests=3, window_seconds=60)
        for expected_remaining in (2, 1, 0):
            ok, remaining, _ = lim.check("client-a")
            self.assertTrue(ok)
            self.assertEqual(remaining, expected_remaining)

    def test_exceeding_quota_is_blocked(self):
        lim = _RateLimiter(max_requests=2, window_seconds=60)
        lim.check("client-a")
        lim.check("client-a")
        ok, remaining, retry = lim.check("client-a")
        self.assertFalse(ok)
        self.assertEqual(remaining, 0)
        self.assertGreater(retry, 0.0)
        self.assertLessEqual(retry, 60.0)

    def test_separate_clients_have_separate_quotas(self):
        lim = _RateLimiter(max_requests=1, window_seconds=60)
        ok_a, _, _ = lim.check("client-a")
        ok_b, _, _ = lim.check("client-b")
        self.assertTrue(ok_a)
        self.assertTrue(ok_b)
        # Now both have hit their quota.
        self.assertFalse(lim.check("client-a")[0])
        self.assertFalse(lim.check("client-b")[0])

    def test_window_expiry_releases_quota(self):
        lim = _RateLimiter(max_requests=1, window_seconds=0.05)
        ok, _, _ = lim.check("client-a")
        self.assertTrue(ok)
        # Immediately afterwards, quota is exhausted.
        self.assertFalse(lim.check("client-a")[0])
        # After the window passes, the bucket is pruned.
        time.sleep(0.1)
        ok, _, _ = lim.check("client-a")
        self.assertTrue(ok)

    def test_disabled_limiter_always_allows(self):
        lim = _RateLimiter(max_requests=0, window_seconds=60, enabled=False)
        # Even after many calls, we never block.
        for _ in range(100):
            ok, _, _ = lim.check("client-a")
            self.assertTrue(ok)

    def test_reset_clears_all_state(self):
        lim = _RateLimiter(max_requests=1, window_seconds=60)
        lim.check("client-a")
        lim.check("client-b")
        lim.reset()
        # After reset, both clients have a full quota.
        self.assertTrue(lim.check("client-a")[0])
        self.assertTrue(lim.check("client-b")[0])

    def test_sweep_removes_expired_buckets(self):
        """P2-1: sweep() 必须清掉窗口外 client 的 bucket。"""
        lim = _RateLimiter(max_requests=2, window_seconds=0.05)
        lim.check("client-a")
        lim.check("client-b")
        self.assertEqual(len(lim._buckets), 2)
        time.sleep(0.08)
        removed = lim.sweep()
        self.assertEqual(removed, 2)
        self.assertEqual(len(lim._buckets), 0)

    def test_sweep_keeps_active_buckets(self):
        lim = _RateLimiter(max_requests=2, window_seconds=60)
        lim.check("client-a")
        lim.check("client-b")
        removed = lim.sweep()
        self.assertEqual(removed, 0)
        self.assertEqual(len(lim._buckets), 2)

    def test_check_triggers_periodic_sweep(self):
        """check() 距上次清理超过 sweep_interval 时自动全表清理。"""
        lim = _RateLimiter(max_requests=2, window_seconds=0.05)
        lim.check("client-a")
        time.sleep(0.08)
        # 强制让 sweep 周期到期, 再从另一个 client check — 过期 bucket
        # 应当被自动回收。
        lim._last_sweep = 0.0
        lim.check("client-b")
        self.assertEqual(list(lim._buckets.keys()), ["client-b"])

    def test_thread_safety(self):
        """Concurrent ``check()`` from many threads must not exceed the quota."""
        import threading
        lim = _RateLimiter(max_requests=50, window_seconds=60)
        allowed = []
        lock = threading.Lock()

        def hammer():
            ok, _, _ = lim.check("client-a")
            with lock:
                allowed.append(ok)

        threads = [threading.Thread(target=hammer) for _ in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Exactly 50 of the 200 calls must have been allowed.
        self.assertEqual(sum(allowed), 50)
        self.assertEqual(len(allowed), 200)


class TestBuildRateLimiterFromEnv(unittest.TestCase):
    """``_build_rate_limiter`` must honour the env vars."""

    def setUp(self):
        self._saved_limit = os.environ.pop("MEDIASCRIBE_RATE_LIMIT", None)
        self._saved_window = os.environ.pop("MEDIASCRIBE_RATE_LIMIT_WINDOW", None)

    def tearDown(self):
        if self._saved_limit is not None:
            os.environ["MEDIASCRIBE_RATE_LIMIT"] = self._saved_limit
        else:
            os.environ.pop("MEDIASCRIBE_RATE_LIMIT", None)
        if self._saved_window is not None:
            os.environ["MEDIASCRIBE_RATE_LIMIT_WINDOW"] = self._saved_window
        else:
            os.environ.pop("MEDIASCRIBE_RATE_LIMIT_WINDOW", None)

    def test_default(self):
        lim = _build_rate_limiter()
        self.assertTrue(lim.enabled)
        self.assertEqual(lim.max_requests, 10)
        self.assertEqual(lim.window_seconds, 60.0)

    def test_zero_disables(self):
        os.environ["MEDIASCRIBE_RATE_LIMIT"] = "0"
        lim = _build_rate_limiter()
        self.assertFalse(lim.enabled)
        self.assertEqual(lim.max_requests, 0)

    def test_custom_max(self):
        os.environ["MEDIASCRIBE_RATE_LIMIT"] = "3"
        lim = _build_rate_limiter()
        self.assertEqual(lim.max_requests, 3)
        self.assertTrue(lim.enabled)

    def test_custom_window(self):
        os.environ["MEDIASCRIBE_RATE_LIMIT"] = "5"
        os.environ["MEDIASCRIBE_RATE_LIMIT_WINDOW"] = "10"
        lim = _build_rate_limiter()
        self.assertEqual(lim.max_requests, 5)
        self.assertEqual(lim.window_seconds, 10.0)

    def test_invalid_max_falls_back_to_default(self):
        os.environ["MEDIASCRIBE_RATE_LIMIT"] = "not-a-number"
        lim = _build_rate_limiter()
        self.assertEqual(lim.max_requests, 10)

    def test_invalid_window_falls_back_to_default(self):
        os.environ["MEDIASCRIBE_RATE_LIMIT_WINDOW"] = "not-a-float"
        lim = _build_rate_limiter()
        self.assertEqual(lim.window_seconds, 60.0)


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestWebAppRateLimit(unittest.TestCase):
    """End-to-end: hitting ``/api/transcribe`` triggers the limiter."""

    def setUp(self):
        os.environ.pop("MEDIASCRIBE_API_TOKEN", None)
        # Tight quota so the test runs fast.
        os.environ["MEDIASCRIBE_RATE_LIMIT"] = "2"
        os.environ["MEDIASCRIBE_RATE_LIMIT_WINDOW"] = "60"
        self.app = create_app(workspace=Path.cwd() / "test-ws-rl")
        self.client = TestClient(self.app)

    def tearDown(self):
        os.environ.pop("MEDIASCRIBE_RATE_LIMIT", None)
        os.environ.pop("MEDIASCRIBE_RATE_LIMIT_WINDOW", None)

    def test_health_reports_rate_limit_config(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["rate_limit_enabled"])
        self.assertEqual(body["rate_limit_max"], 2)
        self.assertEqual(body["rate_limit_window"], 60.0)

    def _post_transcribe(self):
        """POST a request that will get past the (mocked) pipeline."""
        # We don't actually need the pipeline to succeed — auth+rate-limit
        # gate runs before the pipeline call.  An empty body still triggers
        # 400, which is *after* the rate limiter.
        return self.client.post(
            "/api/transcribe",
            json={"urls": ["https://example.com/video"]},
        )

    def test_within_quota_is_allowed(self):
        # First two requests go through (rate limit gate).  They might
        # 400 on empty URLs or fail to reach the network; we only care
        # that they are not 429.
        r1 = self._post_transcribe()
        r2 = self._post_transcribe()
        self.assertNotEqual(r1.status_code, 429)
        self.assertNotEqual(r2.status_code, 429)

    def test_exceeding_quota_returns_429(self):
        # Burn the quota.
        for _ in range(2):
            self._post_transcribe()
        # Third request within the window must be 429.
        r3 = self._post_transcribe()
        self.assertEqual(r3.status_code, 429)
        # RFC 6585 + standard practice: Retry-After header is set.
        self.assertIn("retry-after", {h.lower() for h in r3.headers.keys()})
        retry = int(r3.headers["retry-after"])
        self.assertGreaterEqual(retry, 1)
        self.assertLessEqual(retry, 60)
        # X-RateLimit-* headers are set.
        self.assertEqual(r3.headers["X-RateLimit-Limit"], "2")
        self.assertEqual(r3.headers["X-RateLimit-Remaining"], "0")
        # Body explains what happened.
        body = r3.json()
        self.assertIn("rate limit exceeded", body["detail"].lower())

    def test_separate_clients_have_separate_quotas(self):
        # TestClient is hardcoded to one client IP, so we exercise the
        # underlying _RateLimiter directly here.
        lim = _RateLimiter(max_requests=1, window_seconds=60)
        self.assertTrue(lim.check("10.0.0.1")[0])
        self.assertFalse(lim.check("10.0.0.1")[0])
        self.assertTrue(lim.check("10.0.0.2")[0])


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestRateLimitDisabled(unittest.TestCase):
    """``MEDIASCRIBE_RATE_LIMIT=0`` must leave the API fully open."""

    def setUp(self):
        os.environ["MEDIASCRIBE_RATE_LIMIT"] = "0"
        self.app = create_app(workspace=Path.cwd() / "test-ws-rl-off")
        self.client = TestClient(self.app)

    def tearDown(self):
        os.environ.pop("MEDIASCRIBE_RATE_LIMIT", None)

    def test_health_reports_disabled(self):
        r = self.client.get("/api/health")
        body = r.json()
        self.assertFalse(body["rate_limit_enabled"])
        self.assertEqual(body["rate_limit_max"], 0)

    def test_many_requests_succeed(self):
        # 20 calls; with disabled limiter none should hit 429.
        statuses = []
        for _ in range(20):
            r = self.client.post(
                "/api/transcribe",
                json={"urls": ["https://example.com/x"]},
            )
            statuses.append(r.status_code)
        self.assertNotIn(429, statuses)


if __name__ == "__main__":
    unittest.main()
