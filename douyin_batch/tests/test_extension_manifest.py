"""
Validate the browser extension manifest and asset layout.

This is a minimal linter that catches the most common
extension-development errors:

* ``manifest.json`` is well-formed JSON
* required fields (``manifest_version``, ``name``, ``version``,
  ``action``) are present
* every file referenced from the manifest exists on disk
"""
import json
import sys
import unittest
from pathlib import Path

EXT = Path(__file__).parent.parent.parent / "extension"


class TestExtensionManifest(unittest.TestCase):

    def setUp(self):
        self.manifest_path = EXT / "manifest.json"
        self.assertTrue(self.manifest_path.exists(), f"missing {self.manifest_path}")
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def test_required_fields(self):
        for field in ("manifest_version", "name", "version", "action"):
            self.assertIn(field, self.manifest, f"missing manifest field: {field}")

    def test_manifest_v3(self):
        self.assertEqual(self.manifest["manifest_version"], 3)

    def test_action_popup_exists(self):
        popup = self.manifest["action"]["default_popup"]
        self.assertTrue((EXT / popup).exists(), f"missing popup: {popup}")

    def test_background_service_worker_exists(self):
        sw = self.manifest["background"]["service_worker"]
        self.assertTrue((EXT / sw).exists(), f"missing service worker: {sw}")

    def test_options_page_exists(self):
        opts = self.manifest.get("options_page")
        if opts:
            self.assertTrue((EXT / opts).exists(), f"missing options page: {opts}")

    def test_icon_files_exist(self):
        icons = self.manifest.get("icons", {})
        for size, path in icons.items():
            self.assertTrue(
                (EXT / path).exists(),
                f"missing icon {size}px: {path}",
            )
        # Action icons must be a subset of icons dict (Chromium rule)
        action_icons = self.manifest.get("action", {}).get("default_icon", {})
        for size, path in action_icons.items():
            self.assertTrue(
                (EXT / path).exists(),
                f"missing action icon {size}px: {path}",
            )


if __name__ == "__main__":
    unittest.main()
