"""
Tests for the "Download the browser extension" flow.

The Web UI exposes three new surfaces:

* ``GET  /extension``                       — install landing page
* ``GET  /api/extension/download``          — fresh ZIP of ``extension/``
* ``GET  /api/extension/install.md``        — markdown install guide
  (with ``?raw=1`` for the raw text/markdown)

The test suite covers both the unit-level ``extension_builder`` API
and the FastAPI endpoints (with a test client).
"""

import io
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mediascribe" / "web"))

try:
    from app import create_app  # type: ignore
    from extension_builder import (  # type: ignore
        build_extension_zip,
        build_install_markdown,
    )
    from fastapi.testclient import TestClient  # type: ignore

    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False


# ---------------------------------------------------------------------------
# Unit tests — extension_builder.build_extension_zip
# ---------------------------------------------------------------------------
class TestBuildExtensionZip(unittest.TestCase):
    """The in-memory ZIP must be valid and contain the source tree."""

    def setUp(self):
        self.root = ROOT / "extension"
        self.assertTrue(self.root.is_dir(), f"extension/ not found: {self.root}")

    def test_zip_contains_manifest_at_root(self):
        data, filename = build_extension_zip(self.root)
        self.assertTrue(data, "empty ZIP returned")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            self.assertIn("manifest.json", names)
            # The manifest must sit at the top level (Load unpacked
            # requires it; the user unzips into a folder they pick).
            self.assertNotIn("extension/manifest.json", names)

    def test_zip_contains_popup_html(self):
        data, _ = build_extension_zip(self.root)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for required in (
                "popup.html",
                "popup.js",
                "popup.css",
                "background.js",
                "options.html",
                "options.js",
                "README.md",
            ):
                self.assertIn(required, zf.namelist(), f"missing {required}")

    def test_zip_contains_icons(self):
        data, _ = build_extension_zip(self.root)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for icon in ("icons/icon16.png", "icons/icon48.png", "icons/icon128.png"):
                self.assertIn(icon, zf.namelist(), f"missing {icon}")

    def test_zip_manifest_is_valid_json(self):
        data, _ = build_extension_zip(self.root)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            import json as _json

            manifest = _json.loads(zf.read("manifest.json"))
            self.assertEqual(manifest["manifest_version"], 3)
            self.assertIn("name", manifest)
            self.assertIn("version", manifest)

    def test_filename_contains_version(self):
        _, filename = build_extension_zip(self.root)
        self.assertTrue(
            filename.startswith("mediascribe-extension-v"),
            f"unexpected filename: {filename}",
        )
        self.assertTrue(filename.endswith(".zip"))
        # The version segment should be sanitised to ASCII alnum only.
        m = re.match(r"mediascribe-extension-v(.+)\.zip$", filename)
        self.assertIsNotNone(m)
        self.assertRegex(m.group(1), r"^[A-Za-z0-9._-]+$")

    def test_extra_files_injected(self):
        data, _ = build_extension_zip(
            self.root,
            extra_files={"INSTALL.md": "# Hello\n"},
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.assertIn("INSTALL.md", zf.namelist())
            self.assertEqual(zf.read("INSTALL.md").decode(), "# Hello\n")

    def test_extra_files_path_traversal_blocked(self):
        # The builder must refuse to write into a path that escapes
        # the archive root.
        for evil in ("../evil.md", "/abs/path.md", "", "  "):
            with self.subTest(path=evil):
                data, _ = build_extension_zip(
                    self.root,
                    extra_files={evil: "boom"},
                )
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    self.assertNotIn(
                        evil,
                        zf.namelist(),
                        f"path-traversal entry {evil!r} was added",
                    )

    def test_missing_root_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "does-not-exist"
            with self.assertRaises(FileNotFoundError):
                build_extension_zip(fake)

    def test_excludes_pycache(self):
        # If __pycache__ files happen to leak in (e.g. from a partial
        # checkout) they must be silently dropped.
        data, _ = build_extension_zip(self.root)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                self.assertNotIn("__pycache__", name)
                self.assertFalse(
                    name.endswith(".pyc"),
                    f"pyc file leaked into archive: {name}",
                )


# ---------------------------------------------------------------------------
# Unit tests — build_install_markdown
# ---------------------------------------------------------------------------
class TestBuildInstallMarkdown(unittest.TestCase):
    def test_includes_origin(self):
        md = build_install_markdown(web_ui_origin="http://example.com:1234")
        self.assertIn("http://example.com:1234", md)
        # The token warning only appears when the default origin is
        # in use; with a custom origin we just mention it as the
        # default.
        self.assertIn("Install Guide", md)
        self.assertIn("Developer mode", md)

    def test_includes_timestamp(self):
        md = build_install_markdown(web_ui_origin="http://x")
        self.assertRegex(md, r"\d{4}-\d{2}-\d{2}")

    def test_includes_chrome_steps(self):
        md = build_install_markdown(web_ui_origin="http://x", chrome=True, edge=False)
        self.assertIn("chrome://extensions/", md)
        self.assertNotIn("edge://extensions/", md)

    def test_includes_edge_steps(self):
        md = build_install_markdown(web_ui_origin="http://x", chrome=False, edge=True)
        self.assertIn("edge://extensions/", md)
        self.assertNotIn("chrome://extensions/", md)

    def test_includes_both_when_requested(self):
        md = build_install_markdown(web_ui_origin="http://x", chrome=True, edge=True)
        self.assertIn("chrome://extensions/", md)
        self.assertIn("edge://extensions/", md)

    def test_no_browser_steps_when_disabled(self):
        md = build_install_markdown(web_ui_origin="http://x", chrome=False, edge=False)
        self.assertNotIn("chrome://extensions/", md)
        self.assertNotIn("edge://extensions/", md)
        # Header + troubleshooting must still render.
        self.assertIn("Install Guide", md)
        self.assertIn("Troubleshooting", md)


# ---------------------------------------------------------------------------
# Integration tests — FastAPI endpoints
# ---------------------------------------------------------------------------
@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestExtensionDownloadEndpoint(unittest.TestCase):
    def setUp(self):
        os.environ.pop("MEDIASCRIBE_API_TOKEN", None)
        # Generous rate limit so the test does not trip the limiter.
        os.environ["MEDIASCRIBE_RATE_LIMIT"] = "50"
        self.app = create_app(workspace=Path.cwd() / "test-ws-ext")
        self.client = TestClient(self.app)

    def tearDown(self):
        os.environ.pop("MEDIASCRIBE_RATE_LIMIT", None)

    def test_install_page_renders(self):
        r = self.client.get("/extension")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers.get("content-type", ""))
        body = r.text
        # Must contain the three primary affordances.
        for needle in (
            "Download extension",
            "Install guide",
            "chrome://extensions/",
            "edge://extensions/",
        ):
            self.assertIn(needle, body, f"install page missing: {needle}")

    def test_install_page_mentions_auth_when_token_set(self):
        os.environ["MEDIASCRIBE_API_TOKEN"] = "secret"
        try:
            app = create_app(workspace=Path.cwd() / "test-ws-ext-auth")
            client = TestClient(app)
            r = client.get("/extension")
            self.assertEqual(r.status_code, 200)
            self.assertIn("Bearer token", r.text)
        finally:
            os.environ.pop("MEDIASCRIBE_API_TOKEN", None)

    def test_install_page_silent_when_no_auth(self):
        r = self.client.get("/extension")
        self.assertEqual(r.status_code, 200)
        self.assertIn("No token is required", r.text)

    def test_download_returns_zip(self):
        r = self.client.get("/api/extension/download")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-type"), "application/zip")
        # Filename must come from Content-Disposition.
        cd = r.headers.get("content-disposition", "")
        self.assertIn("attachment", cd)
        self.assertRegex(cd, r'filename="mediascribe-extension-v[^"]+\.zip"')
        # Body must be a valid ZIP with manifest.json at the root.
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            self.assertIn("manifest.json", zf.namelist())
            self.assertIn("INSTALL.md", zf.namelist(), "INSTALL.md missing")
            # The auto-generated INSTALL.md should embed the test
            # server's origin so the user gets working instructions.
            install = zf.read("INSTALL.md").decode("utf-8")
            self.assertIn("testserver", install)

    def test_download_install_md_endpoint_html(self):
        r = self.client.get("/api/extension/install.md")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers.get("content-type", ""))
        self.assertIn("Install Guide", r.text)

    def test_download_install_md_endpoint_raw(self):
        r = self.client.get("/api/extension/install.md?raw=1")
        self.assertEqual(r.status_code, 200)
        # Raw mode returns the actual markdown MIME.
        self.assertIn("text/markdown", r.headers.get("content-type", ""))
        self.assertIn("Content-Disposition", r.headers)
        # The body must NOT be HTML-wrapped in raw mode.
        self.assertNotIn("<!doctype", r.text.lower())
        self.assertIn("Install Guide", r.text)

    def test_download_zip_does_not_require_auth(self):
        # The download endpoint must work without an API token
        # because the user is *installing* the extension, not
        # hitting the transcribe API.  Auth only protects the
        # transcribe and transcribe-adjacent endpoints.
        r = self.client.get("/api/extension/download")
        self.assertEqual(r.status_code, 200)

    def test_download_rate_limited(self):
        # Tighten the limit and verify a flood returns 429.
        os.environ["MEDIASCRIBE_RATE_LIMIT"] = "1"
        try:
            app = create_app(workspace=Path.cwd() / "test-ws-ext-rl")
            client = TestClient(app)
            r1 = client.get("/api/extension/download")
            r2 = client.get("/api/extension/download")
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r2.status_code, 429)
        finally:
            os.environ.pop("MEDIASCRIBE_RATE_LIMIT", None)

    def test_index_page_links_to_install(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/extension", r.text)


if __name__ == "__main__":
    unittest.main()
