"""
Unit tests for the MediaScribe Web UI.

These tests do NOT require the ``fastapi`` package.  They cover
the import-time and runtime fallback paths: ``create_app`` must
raise a clear error when FastAPI is missing, and the Pydantic
models must validate inputs the way we promise in the docs.
"""

import os
import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mediascribe" / "web"))


class TestOptionalFastAPIImport(unittest.TestCase):
    """The web app must fail gracefully if fastapi is missing."""

    def test_module_imports_without_fastapi(self):
        import importlib

        # If fastapi is present the module imports fine; if not,
        # the import also succeeds but create_app() raises later.
        m = importlib.import_module("app")
        self.assertTrue(hasattr(m, "create_app"))

    def test_create_app_raises_when_fastapi_missing(self):
        import app

        # Simulate missing FastAPI
        original = app._FASTAPI_AVAILABLE
        app._FASTAPI_AVAILABLE = False
        try:
            with self.assertRaises(RuntimeError) as cm:
                app.create_app()
            self.assertIn("FastAPI", str(cm.exception))
        finally:
            app._FASTAPI_AVAILABLE = original


class TestStaticIndexHtml(unittest.TestCase):
    """The bundled HTML must contain the form, the API path, and JS hooks."""

    def test_index_html_present(self):
        index = ROOT / "mediascribe" / "web" / "static" / "index.html"
        self.assertTrue(index.exists())
        text = index.read_text(encoding="utf-8")
        # v3.2.0c: form now POSTs to /api/jobs (async + cancel + WS).
        # /api/health is still referenced by the GPU pill polling loop.
        for needle in ("/api/jobs", "/api/health", "textarea", "engine", "model", "gpu-pill"):
            self.assertIn(needle, text)


class TestPydanticModelsIfAvailable(unittest.TestCase):
    """When FastAPI is installed, the request/response models must validate."""

    def test_models_validate(self):
        try:
            from app import TranscribeRequest, TranscribeResponse  # type: ignore
        except Exception:
            self.skipTest("fastapi not installed; skipping pydantic tests")
        # Valid request
        req = TranscribeRequest(
            urls=["https://example.com/v"],
            engine="whisper",
            model="small",
        )
        self.assertEqual(req.engine, "whisper")
        self.assertEqual(len(req.urls), 1)
        # Invalid engine must raise
        with self.assertRaises(Exception):
            TranscribeRequest(urls=["https://example.com/v"], engine="bogus", model="small")
        # Empty urls must raise
        with self.assertRaises(Exception):
            TranscribeRequest(urls=[], engine="whisper", model="small")


###########################################################################
# 迁入自 tests/test_coverage_gaps.py —— TestWebAppInstallMd(mediascribe.web.app)
###########################################################################


class TestWebAppInstallMd:
    """The install.md endpoint has two formats (html vs raw)."""

    def test_install_md_html_default(self):
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        c = TestClient(create_app())
        r = c.get("/api/extension/install.md")
        assert r.status_code == 200
        # HTML wrapper renders the markdown inside a <pre> block with a
        # back-link to the install page.
        assert "<pre>" in r.text
        assert "&larr; Back" in r.text or "Back" in r.text
        # And the markdown payload itself is included verbatim.
        assert "Load unpacked" in r.text

    def test_install_md_raw_markdown(self):
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        c = TestClient(create_app())
        r = c.get("/api/extension/install.md?raw=1")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        # Body is pure markdown — starts with a ``#`` and contains install steps.
        assert r.text.lstrip().startswith("#")
        assert "chrome://extensions" in r.text or "Edge" in r.text


# _HAS_WEB / TestClient scaffold —— 必须先于下方 skipif 装饰器求值(迁入自
# tests/test_coverage_gaps_3.py 的模块级 try/except, 原样保留)。
try:
    from starlette.testclient import TestClient

    from mediascribe.web.app import create_app

    _HAS_WEB = True
except Exception:  # pragma: no cover
    _HAS_WEB = False


###########################################################################
# 迁入自 tests/test_coverage_gaps_3.py —— TestWebAppJobEndpoints(mediascribe.web.app)
###########################################################################


@pytest.mark.skipif(not _HAS_WEB, reason="web app not importable")
class TestWebAppJobEndpoints:
    def _build_app(self, workspace: Path):
        # Clean any old env leakage
        os.environ.pop("MEDIASCRIBE_API_TOKEN", None)
        return create_app(workspace=workspace)

    def test_ws_progress_unknown_job_sends_error_and_closes(self, tmp_path: Path) -> None:
        """The /ws/progress/{id} handler must reply with an error event
        when ``job_id`` is unknown."""
        app = self._build_app(tmp_path / "ws-unknown")
        with TestClient(app) as client:
            with client.websocket_connect("/ws/progress/does-not-exist") as ws:
                msg = ws.receive_json()
                assert msg["event"] == "error"
                assert "unknown" in msg["message"].lower()

    def test_ws_progress_sends_snapshot_for_existing_job(self, tmp_path: Path) -> None:
        """Connecting to a real job should yield a snapshot event first."""
        from mediascribe.progress import ProgressRegistry

        app = self._build_app(tmp_path / "ws-snapshot")
        # Replace the default registry with one holding a fresh job
        reg = ProgressRegistry()
        job = reg.create("https://example.com/v")
        app.state.jobs = reg

        with TestClient(app) as client:
            with client.websocket_connect(f"/ws/progress/{job.job_id}") as ws:
                msg = ws.receive_json()
                assert msg["event"] == "snapshot"
                assert msg["job_id"] == job.job_id
                # Finish so the handler's while-loop exits
                job.succeed(result={"text": "ok"})
                # Drain remaining snapshot
                while True:
                    try:
                        ev = ws.receive_json()
                    except Exception:
                        break
                    if ev.get("event") == "snapshot" and ev.get("finished"):
                        break

    def test_api_jobs_status_unknown_returns_404(self, tmp_path: Path) -> None:
        """GET /api/jobs/{id} with an unknown id → 404."""
        app = self._build_app(tmp_path / "api-status-unknown")
        # Set a token so the auth dependency passes
        os.environ["MEDIASCRIBE_API_TOKEN"] = "test-token-xyz"
        try:
            app2 = self._build_app(tmp_path / "api-status-unknown-2")
            with TestClient(app2) as client:
                r = client.get(
                    "/api/jobs/nope",
                    headers={"Authorization": "Bearer test-token-xyz"},
                )
                assert r.status_code == 404
        finally:
            os.environ.pop("MEDIASCRIBE_API_TOKEN", None)

    def test_api_jobs_cancel_unknown_returns_404(self, tmp_path: Path) -> None:
        """POST /api/jobs/{id}/cancel on an unknown id → 404 (v3.2.0a design)."""
        app = self._build_app(tmp_path / "api-cancel-unknown")
        os.environ["MEDIASCRIBE_API_TOKEN"] = "test-token-xyz"
        try:
            with TestClient(app) as client:
                r = client.post(
                    "/api/jobs/nope/cancel",
                    headers={"Authorization": "Bearer test-token-xyz"},
                )
                assert r.status_code == 404
        finally:
            os.environ.pop("MEDIASCRIBE_API_TOKEN", None)

    def test_api_jobs_cancel_known_returns_200(self, tmp_path: Path) -> None:
        """POST /api/jobs/{id}/cancel on a real job → 200 + cancelled=True."""
        from mediascribe.progress import ProgressRegistry

        app = self._build_app(tmp_path / "api-cancel-known")
        reg = ProgressRegistry()
        job = reg.create("https://example.com/v")
        app.state.jobs = reg
        os.environ["MEDIASCRIBE_API_TOKEN"] = "test-token-xyz"
        try:
            with TestClient(app) as client:
                r = client.post(
                    f"/api/jobs/{job.job_id}/cancel",
                    headers={"Authorization": "Bearer test-token-xyz"},
                )
                assert r.status_code == 200
                data = r.json()
                assert data["cancelled"] is True
        finally:
            os.environ.pop("MEDIASCRIBE_API_TOKEN", None)


if __name__ == "__main__":
    unittest.main()
