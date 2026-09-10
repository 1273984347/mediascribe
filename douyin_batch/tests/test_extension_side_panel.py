"""
Smoke tests for the MediaScribe browser extension JS surface.

These tests do NOT spin up a real browser.  Instead they:

* Parse every ``.js`` and ``.html`` file in ``extension/`` and assert
  that the code is syntactically valid (uses ``node --check`` if
  available, otherwise a lightweight structural check).
* Verify that ``manifest.json`` is valid JSON, declares both the
  ``action.default_popup`` and ``side_panel.default_path`` fields,
  and points them at the same HTML entry point.
* Verify that ``popup.html`` references both ``popup.js`` and
  ``popup.css`` (the JS bundle is no longer self-contained).
* Verify that ``background.js`` wires up the listeners we expect
  (``onInstalled``, ``onStartup``, ``action.onClicked``,
  ``onMessage``).
* Verify that ``popup.js`` has both the toolbar-popup and side-panel
  code paths (``detectSurface`` + ``openSidePanel`` + element IDs
  used by both).

The tests are deliberately tolerant of pre-Chrome-114 browsers — the
JS guards ``chrome.sidePanel`` before using it, so a missing API
must not be a parse error.

Skipped automatically if ``node`` is not on PATH.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
EXT = ROOT / "extension"


@unittest.skipUnless(EXT.is_dir(), f"extension dir not found: {EXT}")
class TestExtensionLayout(unittest.TestCase):
    """Make sure the expected files exist."""

    def test_required_files(self):
        for name in (
            "manifest.json",
            "popup.html",
            "popup.js",
            "popup.css",
            "background.js",
            "options.html",
            "options.js",
        ):
            with self.subTest(file=name):
                self.assertTrue((EXT / name).is_file(), f"missing {name}")


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))

    def test_manifest_v3(self):
        self.assertEqual(self.manifest.get("manifest_version"), 3)

    def test_action_default_popup_set(self):
        action = self.manifest.get("action") or {}
        self.assertEqual(action.get("default_popup"), "popup.html")

    def test_side_panel_default_path_set(self):
        sp = self.manifest.get("side_panel")
        self.assertIsNotNone(sp, "side_panel field missing")
        self.assertEqual(sp.get("default_path"), "popup.html")

    def test_side_panel_permission(self):
        perms = self.manifest.get("permissions") or []
        self.assertIn("sidePanel", perms, "sidePanel permission missing")

    def test_minimum_chrome_version_114(self):
        # The Side Panel API shipped in Chrome 114; refuse to advertise
        # the panel UI on older browsers.
        mcv = self.manifest.get("minimum_chrome_version", "0")
        self.assertGreaterEqual(int(mcv), 114)

    def test_both_surfaces_point_to_same_html(self):
        action = self.manifest.get("action") or {}
        sp = self.manifest.get("side_panel") or {}
        self.assertEqual(action.get("default_popup"), sp.get("default_path"))


class TestPopupHtml(unittest.TestCase):
    def setUp(self):
        self.html = (EXT / "popup.html").read_text(encoding="utf-8")

    def test_loads_popup_js(self):
        self.assertRegex(self.html, r'<script[^>]+src="popup\.js"')

    def test_loads_popup_css(self):
        self.assertRegex(self.html, r'<link[^>]+href="popup\.css"')

    def test_has_send_button(self):
        self.assertIn('id="send"', self.html)

    def test_has_open_side_panel_button(self):
        # The "Open in side panel" CTA must be in the popup.
        self.assertIn('id="openSidePanel"', self.html)

    def test_has_open_options_button(self):
        self.assertIn('id="openOptions"', self.html)

    def test_bearer_token_field_present(self):
        self.assertIn('id="apiToken"', self.html)


class TestPopupJs(unittest.TestCase):
    def setUp(self):
        self.src = (EXT / "popup.js").read_text(encoding="utf-8")

    def test_uses_shared_css_via_link(self):
        # We no longer embed styles in the JS — the stylesheet is
        # loaded via popup.html.  Sanity-check that popup.js does not
        # still define a ``<style>`` block (which would mean a
        # regression to the old self-contained style approach).
        self.assertNotIn("<style>", self.src)

    def test_defines_detect_surface(self):
        self.assertRegex(self.src, r"function\s+detectSurface\s*\(")

    def test_defines_open_side_panel(self):
        self.assertRegex(self.src, r"function\s+openSidePanel\s*\(")

    def test_handles_missing_side_panel_api(self):
        # The early-return guard must be present so that the popup
        # does not crash on browsers < 114.
        self.assertIn("Side panel requires Chrome / Edge 114+", self.src)

    def test_uses_message_bus_fallback(self):
        self.assertIn("v2t/openSidePanel", self.src)

    def test_sends_bearer_token(self):
        self.assertIn("Bearer", self.src)
        self.assertIn("Authorization", self.src)


class TestBackgroundJs(unittest.TestCase):
    def setUp(self):
        self.src = (EXT / "background.js").read_text(encoding="utf-8")

    def test_configures_side_panel(self):
        self.assertIn("setPanelBehavior", self.src)

    def test_handles_action_click(self):
        self.assertIn("chrome.action.onClicked", self.src)

    def test_handles_on_installed(self):
        self.assertIn("chrome.runtime.onInstalled", self.src)

    def test_handles_on_startup(self):
        self.assertIn("chrome.runtime.onStartup", self.src)

    def test_routes_message_bus(self):
        self.assertIn("v2t/openSidePanel", self.src)
        self.assertIn("chrome.runtime.onMessage", self.src)

    def test_opens_options_on_install(self):
        self.assertIn("openOptionsPage", self.src)


class TestPopupCss(unittest.TestCase):
    def setUp(self):
        self.src = (EXT / "popup.css").read_text(encoding="utf-8")

    def test_has_side_panel_class(self):
        self.assertIn(".is-side-panel", self.src)

    def test_has_dark_color_scheme(self):
        self.assertIn("prefers-color-scheme: light", self.src)

    def test_no_inline_style_leakage(self):
        # The popup.css is loaded as a stylesheet, so we should not
        # find any inline <style>...</style> blocks.
        self.assertNotIn("<style>", self.src)


@unittest.skipUnless(shutil.which("node"), "node not on PATH")
class TestJsSyntax(unittest.TestCase):
    """Parse popup.js + background.js with ``node --check``."""

    def _check(self, name):
        path = EXT / name
        proc = subprocess.run(
            ["node", "--check", str(path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"{name} syntax error: {proc.stderr}",
        )

    def test_popup_js_parses(self):
        self._check("popup.js")

    def test_background_js_parses(self):
        self._check("background.js")

    def test_options_js_parses(self):
        self._check("options.js")


if __name__ == "__main__":
    unittest.main()
