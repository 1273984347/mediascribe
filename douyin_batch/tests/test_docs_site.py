"""
Validate the docs_site/ structure and mkdocs.yml.

The mkdocs config uses a custom ``!!python/name:`` YAML tag that
PyYAML's safe loader does not understand; the test installs a
stub multi-constructor before parsing so it can introspect the
file without actually resolving the python objects.
"""
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "docs_site"
MKDOCS_YML = ROOT / "mkdocs.yml"


def _safe_load_mkdocs(path: Path) -> dict:
    """Parse mkdocs.yml, treating the python/name tag as a string."""
    yaml.SafeLoader.add_multi_constructor(
        "tag:yaml.org,2002:python/name",
        lambda loader, suffix, node: f"python/name:{suffix}",
    )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestMkdocsConfig(unittest.TestCase):

    def test_file_exists(self):
        self.assertTrue(MKDOCS_YML.exists(), f"missing {MKDOCS_YML}")

    def test_site_name_set(self):
        cfg = _safe_load_mkdocs(MKDOCS_YML)
        self.assertIn("site_name", cfg)
        self.assertTrue(cfg["site_name"])

    def test_theme_uses_material(self):
        cfg = _safe_load_mkdocs(MKDOCS_YML)
        self.assertEqual(cfg["theme"]["name"], "material")

    def test_nav_has_minimum_pages(self):
        cfg = _safe_load_mkdocs(MKDOCS_YML)
        self.assertGreaterEqual(len(cfg["nav"]), 5)

    def test_nav_pages_exist(self):
        cfg = _safe_load_mkdocs(MKDOCS_YML)
        missing = []
        for entry in cfg["nav"]:
            # Each entry is { Title: filename.md } or a list
            if isinstance(entry, dict):
                for title, fname in entry.items():
                    f = DOCS / fname
                    if not f.exists():
                        missing.append(f"{title} -> {fname}")
        self.assertEqual(missing, [],
                         f"missing docs files: {missing}")


class TestDocsContent(unittest.TestCase):

    def test_index_has_intro(self):
        text = (DOCS / "index.md").read_text(encoding="utf-8")
        for needle in ("MediaScribe", "Quick start", "Why MediaScribe?"):
            self.assertIn(needle, text)

    def test_getting_started_has_install(self):
        text = (DOCS / "getting-started.md").read_text(encoding="utf-8")
        self.assertIn("pip install", text)
        self.assertIn("python -m mediascribe", text)

    def test_plugins_doc_has_hookspecs(self):
        text = (DOCS / "plugins.md").read_text(encoding="utf-8")
        for needle in ("entry_points", "mediascribe.downloaders",
                       "mediascribe.transcribers", "mediascribe.url_transformers"):
            self.assertIn(needle, text)

    def test_chunking_doc_explains_strategy(self):
        text = (DOCS / "chunking.md").read_text(encoding="utf-8")
        for needle in ("ChunkedTranscriber", "chunk_seconds", "overlap"):
            self.assertIn(needle, text)

    def test_observability_doc_explains_otel(self):
        text = (DOCS / "observability.md").read_text(encoding="utf-8")
        for needle in ("OpenTelemetry", "install_opentelemetry_exporter"):
            self.assertIn(needle, text)


class TestDocsWorkflow(unittest.TestCase):

    def test_github_pages_workflow_exists(self):
        wf = ROOT / ".github" / "workflows" / "docs.yml"
        self.assertTrue(wf.exists(), f"missing {wf}")
        text = wf.read_text(encoding="utf-8")
        for needle in ("mkdocs", "github-pages", "deploy-pages"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
