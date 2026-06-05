"""
Tests for the Web UI's authentication + CORS additions.

Covers:

* ``/api/health`` is always public and reports ``auth_enabled`` truthfully.
* ``/api/transcribe`` is reachable without a token when no token is
  configured, and refused with HTTP 401 / 403 when one is.
* CORS middleware is wired and serves the configured origins.
* The ``VIDEO2TEXT_CORS_ORIGINS`` env var overrides the defaults.
* ``VIDEO2TEXT_API_TOKEN`` env var enables auth in ``create_app()``.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "video2text" / "web"))

# These tests need the optional fastapi dep.  Skip cleanly otherwise.
try:
    from app import create_app  # type: ignore
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


if __name__ == "__main__":
    unittest.main()
