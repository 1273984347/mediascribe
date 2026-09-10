"""
Tests for the Web UI's authentication + CORS additions.

Covers:

* ``/api/health`` is always public and reports ``auth_enabled`` truthfully.
* ``/api/transcribe`` is reachable without a token when no token is
  configured, and refused with HTTP 401 / 403 when one is.
* CORS middleware is wired and serves the configured origins.
* The ``VIDEO2TEXT_CORS_ORIGINS`` env var overrides the defaults.
* ``VIDEO2TEXT_API_TOKEN`` env var enables auth in ``create_app()``.
* P1-1: the WS handshake enforces the same token (``?token=`` or
  ``Sec-WebSocket-Protocol``) and ``cancel`` needs an authenticated
  connection.
* P1-2: SSRF — private/loopback/link-local URLs are rejected at the
  submit endpoints; ``VIDEO2TEXT_ALLOWED_HOSTS`` opts out.
* P1-3: local-path sources outside the workspace are rejected.
* P2-3: JSON POST endpoints require ``Content-Type: application/json``.
* P2-7: ``_run_batch_job`` logs exceptions and never swallows
  ``SystemExit`` / ``KeyboardInterrupt``.
* P2-10: INSTALL.md origin prefers ``VIDEO2TEXT_PUBLIC_BASE_URL`` and
  never reflects Host userinfo.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "video2text" / "web"))

# These tests need the optional fastapi dep.  Skip cleanly otherwise.
try:
    from app import (  # type: ignore
        _is_local_path_source,
        _public_base_url,
        _run_batch_job,
        _validate_public_url,
        create_app,
    )
    from fastapi.testclient import TestClient  # type: ignore
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestWebAppPublicHealth(unittest.TestCase):
    """The health probe must not require auth — Docker / launcher hit it."""

    def setUp(self):
        # Make sure no token is leaking from a parent test environment.
        os.environ.pop("VIDEO2TEXT_API_TOKEN", None)
        self.app = create_app(workspace=Path.cwd() / "test-ws-public")

    def test_health_is_public(self):
        client = TestClient(self.app)
        r = client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["auth_enabled"])

    def test_index_is_public(self):
        client = TestClient(self.app)
        r = client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<html", r.text.lower())


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestWebAppAuth(unittest.TestCase):
    """Bearer-token enforcement on ``/api/transcribe``."""

    def setUp(self):
        # Per-test token: never commit a real secret; just use a sentinel.
        self._token = "test-token-deadbeef"
        os.environ["VIDEO2TEXT_API_TOKEN"] = self._token
        self.app = create_app(workspace=Path.cwd() / "test-ws-auth")
        self.client = TestClient(self.app)

    def tearDown(self):
        os.environ.pop("VIDEO2TEXT_API_TOKEN", None)

    def test_health_reports_auth_enabled(self):
        r = self.client.get("/api/health")
        self.assertTrue(r.json()["auth_enabled"])

    def test_transcribe_without_token_is_401(self):
        r = self.client.post("/api/transcribe", json={"urls": ["https://x/v"]})
        self.assertEqual(r.status_code, 401)
        self.assertIn("Bearer", r.headers.get("www-authenticate", ""))

    def test_transcribe_with_wrong_token_is_403(self):
        r = self.client.post(
            "/api/transcribe",
            json={"urls": ["https://x/v"]},
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(r.status_code, 403)

    def test_transcribe_with_right_token_passes_auth(self):
        # The downstream pipeline would fail (no real network) but the
        # auth layer must let the request through.  We assert that the
        # response is NOT 401 / 403 — anything else (including a 500
        # from the pipeline) is the auth dependency's job done.
        r = self.client.post(
            "/api/transcribe",
            json={"urls": ["https://x/v"]},
            headers={"Authorization": f"Bearer {self._token}"},
        )
        self.assertNotIn(r.status_code, (401, 403))


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestWebAppCORS(unittest.TestCase):
    """CORS preflight must succeed for the configured origins."""

    def setUp(self):
        os.environ.pop("VIDEO2TEXT_API_TOKEN", None)
        self.app = create_app(workspace=Path.cwd() / "test-ws-cors")
        self.client = TestClient(self.app)

    def test_cors_preflight_for_chrome_extension(self):
        r = self.client.options(
            "/api/transcribe",
            headers={
                "Origin": "chrome-extension://abcdefghijklmnop",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn(
            "chrome-extension://abcdefghijklmnop",
            r.headers.get("access-control-allow-origin", ""),
        )

    def test_cors_preflight_for_localhost(self):
        r = self.client.options(
            "/api/transcribe",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.headers.get("access-control-allow-origin"),
            "http://localhost:5173",
        )

    def test_cors_env_var_overrides_defaults(self):
        os.environ["VIDEO2TEXT_CORS_ORIGINS"] = (
            "https://my-dashboard.example.com, https://other.example.com"
        )
        try:
            app = create_app(workspace=Path.cwd() / "test-ws-cors-env")
            client = TestClient(app)
            # ``https://my-dashboard.example.com`` is in the env list.
            r = client.options(
                "/api/transcribe",
                headers={
                    "Origin": "https://my-dashboard.example.com",
                    "Access-Control-Request-Method": "POST",
                },
            )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(
                r.headers.get("access-control-allow-origin"),
                "https://my-dashboard.example.com",
            )
            # ``http://localhost:5173`` is NOT in the env list, so its
            # preflight must be denied (no ``access-control-allow-origin``
            # in the response for a non-listed origin).
            r2 = client.options(
                "/api/transcribe",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "POST",
                },
            )
            self.assertNotEqual(
                r2.headers.get("access-control-allow-origin"),
                "http://localhost:5173",
            )
        finally:
            os.environ.pop("VIDEO2TEXT_CORS_ORIGINS", None)


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestJobIdUniqueness(unittest.TestCase):
    """The /api/transcribe job IDs must avoid the old hash() % 10**8 hack."""

    def test_run_one_uses_uuid_for_output_path(self):
        import ast
        import inspect

        from app import _run_one
        src = inspect.getsource(_run_one)
        tree = ast.parse(src)
        # The historical anti-pattern is gone from the *executable* code
        # (the docstring may still mention it as historical context).
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
                if "hash" in func:
                    self.fail(f"_run_one still calls hash(): {func}")
        # The replacement must be wired up.
        self.assertIn("uuid.uuid4", src)
        self.assertIn("time.time", src)


# ---------------------------------------------------------------------------
# P1-1 — WebSocket handshake auth
# ---------------------------------------------------------------------------
@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestWsAuth(unittest.TestCase):
    """/ws/progress 握手必须与 HTTP 侧共用同一 token 语义。"""

    def setUp(self):
        os.environ.pop("VIDEO2TEXT_API_TOKEN", None)
        self._tmp = tempfile.TemporaryDirectory()
        self.app_obj = create_app(workspace=Path(self._tmp.name))
        self.client = TestClient(self.app_obj)

    def tearDown(self):
        os.environ.pop("VIDEO2TEXT_API_TOKEN", None)
        self._tmp.cleanup()

    def _make_job(self):
        return self.app_obj.state.jobs.create("https://example.com/v")

    def test_ws_open_without_token_when_auth_disabled(self):
        job = self._make_job()
        with self.client.websocket_connect(f"/ws/progress/{job.job_id}") as ws:
            msg = ws.receive_json()
        self.assertEqual(msg["event"], "snapshot")

    def test_ws_rejected_without_token_when_auth_enabled(self):
        os.environ["VIDEO2TEXT_API_TOKEN"] = "tok-123"
        job = self._make_job()
        rejected = False
        try:
            with self.client.websocket_connect(f"/ws/progress/{job.job_id}"):
                pass
        except Exception:
            # Starlette TestClient raises WebSocketDisconnect on the
            # 1008 handshake rejection — any failure to connect counts.
            rejected = True
        self.assertTrue(rejected, "WS without token must be closed with 1008")

    def test_ws_rejected_with_wrong_token(self):
        os.environ["VIDEO2TEXT_API_TOKEN"] = "tok-123"
        job = self._make_job()
        rejected = False
        try:
            with self.client.websocket_connect(
                f"/ws/progress/{job.job_id}?token=wrong"
            ):
                pass
        except Exception:
            rejected = True
        self.assertTrue(rejected, "WS with a wrong token must be closed with 1008")

    def test_ws_accepts_query_token(self):
        os.environ["VIDEO2TEXT_API_TOKEN"] = "tok-123"
        job = self._make_job()
        with self.client.websocket_connect(
            f"/ws/progress/{job.job_id}?token=tok-123"
        ) as ws:
            msg = ws.receive_json()
        self.assertEqual(msg["event"], "snapshot")

    def test_ws_accepts_token_via_subprotocol(self):
        os.environ["VIDEO2TEXT_API_TOKEN"] = "tok-123"
        job = self._make_job()
        with self.client.websocket_connect(
            f"/ws/progress/{job.job_id}", subprotocols=["tok-123"],
        ) as ws:
            msg = ws.receive_json()
        self.assertEqual(msg["event"], "snapshot")

    def test_ws_cancel_works_with_valid_token(self):
        os.environ["VIDEO2TEXT_API_TOKEN"] = "tok-123"
        job = self._make_job()
        try:
            with self.client.websocket_connect(
                f"/ws/progress/{job.job_id}?token=tok-123"
            ) as ws:
                ws.receive_json()  # snapshot
                ws.send_text('{"event": "cancel"}')
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    try:
                        msg = ws.receive_json()
                    except Exception:
                        break
                    if msg.get("event") in ("cancelled", "cancelled_done"):
                        break
        except Exception:
            pass
        current = self.app_obj.state.jobs.get(job.job_id)
        self.assertIsNotNone(current)
        self.assertTrue(current.cancelled,
                        "authenticated WS cancel must hit the registry")


# ---------------------------------------------------------------------------
# P1-2 — SSRF hardening
# ---------------------------------------------------------------------------
class TestSsrfUrlValidation(unittest.TestCase):
    """``_validate_public_url`` 拒绝私网/环回/链路本地/userinfo/坏 scheme。"""

    def test_rejects_non_http_scheme(self):
        for url in (
            "ftp://example.com/f",
            "file:///etc/passwd",
            "data:text/html,x",
            "gopher://example.com",
        ):
            with self.assertRaises(ValueError):
                _validate_public_url(url)

    def test_rejects_userinfo(self):
        for url in (
            "http://user@example.com/",
            "http://user:pass@example.com/",
            "https://operator@127.0.0.1/",
        ):
            with self.assertRaises(ValueError):
                _validate_public_url(url)

    def test_rejects_private_loopback_linklocal_reserved(self):
        for url in (
            "http://127.0.0.1:8080/",
            "http://169.254.169.254/latest/meta-data/",
            "http://192.168.1.10/",
            "http://10.0.0.1/",
            "http://172.16.0.5/",
            "http://[::1]/",
            "http://0.0.0.0/",
        ):
            with self.assertRaises(ValueError, msg=url):
                _validate_public_url(url)

    def test_rejects_unresolvable_hostname(self):
        with self.assertRaises(ValueError):
            _validate_public_url("http://no-such-host-v2t.invalid/")

    def test_public_url_passes(self):
        url = "http://no-such-host-v2t.invalid"
        # Whitelisted host skips the resolver check entirely (offline-safe).
        old = os.environ.pop("VIDEO2TEXT_ALLOWED_HOSTS", None)
        os.environ["VIDEO2TEXT_ALLOWED_HOSTS"] = "no-such-host-v2t.invalid"
        try:
            self.assertEqual(_validate_public_url(url), url)
        finally:
            os.environ.pop("VIDEO2TEXT_ALLOWED_HOSTS", None)
            if old is not None:
                os.environ["VIDEO2TEXT_ALLOWED_HOSTS"] = old


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestSsrfSubmitRejected(unittest.TestCase):
    """提交入口(/api/jobs、/api/transcribe)必须拒绝私网 URL。"""

    def setUp(self):
        os.environ.pop("VIDEO2TEXT_API_TOKEN", None)
        self._tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(workspace=Path(self._tmp.name)))

    def tearDown(self):
        self._tmp.cleanup()

    def test_private_url_rejected_on_both_submit_paths(self):
        for url in ("http://127.0.0.1:8080/x", "http://169.254.169.254/"):
            r = self.client.post("/api/jobs", json={"urls": [url]})
            self.assertEqual(r.status_code, 400, url)
            self.assertIn("SSRF", r.json()["detail"])
            r2 = self.client.post("/api/transcribe", json={"urls": [url]})
            self.assertEqual(r2.status_code, 400, url)


# ---------------------------------------------------------------------------
# P1-3 — local-path sources outside the workspace
# ---------------------------------------------------------------------------
@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestLocalPathSource(unittest.TestCase):
    """本地路径仅允许 workspace 目录内; 其余必须 http(s) URL。"""

    def setUp(self):
        os.environ.pop("VIDEO2TEXT_API_TOKEN", None)

    def test_outside_workspace_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            ws.mkdir()
            client = TestClient(create_app(workspace=ws))
            outside = Path(td) / "secret.mp4"
            outside.write_bytes(b"x")
            r = client.post("/api/jobs", json={"urls": [str(outside)]})
            self.assertEqual(r.status_code, 400)
            # POSIX 风格绝对路径(即使 Windows 也必须拒绝)
            r2 = client.post("/api/jobs", json={"urls": ["/etc/passwd"]})
            self.assertEqual(r2.status_code, 400)

    def test_inside_workspace_allowed(self):
        from types import SimpleNamespace
        from unittest import mock

        import app as app_module

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            ws.mkdir()
            client = TestClient(create_app(workspace=ws))
            inside = ws / "clip.mp4"
            inside.write_bytes(b"x")
            fake = mock.MagicMock(name="Pipeline")
            fake.transcribe.return_value = SimpleNamespace(
                engine="fake", metadata={},
            )
            with mock.patch.object(app_module, "_build_pipeline", return_value=fake):
                r = client.post("/api/jobs", json={"urls": [str(inside)]})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(len(r.json()["jobs"]), 1)

    def test_is_local_path_source_classification(self):
        self.assertTrue(_is_local_path_source("/etc/passwd"))
        self.assertTrue(_is_local_path_source(str(Path(tempfile.gettempdir()) / "x.mp4")))
        # 分享文本(无盘符/无根, 磁盘上不存在)不算本地路径。
        self.assertFalse(_is_local_path_source("7.2 抖音好物分享 https://v.douyin.com/x"))
        self.assertFalse(_is_local_path_source(""))


# ---------------------------------------------------------------------------
# P2-3 — JSON POST endpoints require application/json
# ---------------------------------------------------------------------------
@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestJsonContentTypeRequired(unittest.TestCase):
    """text/plain "简单请求" 不能驱动 JSON API(415)。"""

    def setUp(self):
        os.environ.pop("VIDEO2TEXT_API_TOKEN", None)
        self._tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(workspace=Path(self._tmp.name)))

    def tearDown(self):
        self._tmp.cleanup()

    def test_text_plain_body_is_415(self):
        body = json.dumps({"urls": ["https://example.com/v"]})
        for path in ("/api/jobs", "/api/transcribe"):
            r = self.client.post(
                path, content=body, headers={"Content-Type": "text/plain"},
            )
            self.assertEqual(r.status_code, 415, path)

    def test_missing_content_type_is_415(self):
        for path in ("/api/jobs", "/api/transcribe"):
            r = self.client.post(path, content='{"urls": ["https://example.com/v"]}')
            self.assertEqual(r.status_code, 415, path)

    def test_json_content_type_passes_gate(self):
        # 空 urls 是 422(校验错误)而不是 415 — 说明 JSON 请求已过 CSRF 门。
        r = self.client.post("/api/jobs", json={"urls": []})
        self.assertEqual(r.status_code, 422)


# ---------------------------------------------------------------------------
# P2-7 — _run_batch_job no longer swallows BaseException
# ---------------------------------------------------------------------------
class TestRunBatchJobErrorHandling(unittest.TestCase):
    def test_exception_is_logged(self):
        import logging

        import app as app_module

        class FakeAP:
            async def run_batch(self, urls, runners=None):
                raise ValueError("boom")

        logger_name = app_module.__name__
        with self.assertLogs(logging.getLogger(logger_name), level="ERROR"):
            app_module._run_batch_job(FakeAP(), ["u"], {}, [], {})

    def test_system_exit_propagates(self):
        import app as app_module

        class FakeAP:
            async def run_batch(self, urls, runners=None):
                raise SystemExit(3)

        with self.assertRaises(SystemExit):
            app_module._run_batch_job(FakeAP(), ["u"], {}, [], {})


# ---------------------------------------------------------------------------
# P2-10 — INSTALL.md origin hardening
# ---------------------------------------------------------------------------
@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestPublicBaseUrl(unittest.TestCase):
    def setUp(self):
        os.environ.pop("VIDEO2TEXT_PUBLIC_BASE_URL", None)
        self._tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(workspace=Path(self._tmp.name)))

    def tearDown(self):
        os.environ.pop("VIDEO2TEXT_PUBLIC_BASE_URL", None)
        self._tmp.cleanup()

    def test_env_override_wins(self):
        os.environ["VIDEO2TEXT_PUBLIC_BASE_URL"] = "https://v2t.example.internal"
        r = self.client.get("/api/extension/install.md?raw=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("https://v2t.example.internal", r.text)

    def test_host_userinfo_stripped(self):
        r = self.client.get(
            "/api/extension/install.md?raw=1",
            headers={"Host": "user@evil.example.com:8000"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("user@", r.text)

    def test_host_injection_chars_not_reflected(self):
        r = self.client.get(
            "/api/extension/install.md?raw=1",
            headers={"Host": 'evil"onmouseover="x'},
        )
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('"onmouseover', r.text)


if __name__ == "__main__":
    unittest.main()
