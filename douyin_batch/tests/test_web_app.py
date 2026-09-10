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

ROOT = Path(__file__).parent.parent.parent
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


if __name__ == "__main__":
    unittest.main()
