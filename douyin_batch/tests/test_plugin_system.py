"""
Unit tests for the plugin system.

The ``mediascribe.downloaders`` entry point is registered with the
in-tree example plugin in ``pyproject.toml``.  When pyproject is
present, the plugin should be discoverable via
``plugins.list_downloaders()`` and instantiable via
``plugins.get_downloader()``.

Tests are designed to fail gracefully (skip) when the package
is not installed in editable mode, because ``importlib.metadata``
only sees entry points from *installed* distributions.
"""
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


def _package_is_installed() -> bool:
    """Return True if the mediascribe package is installed (editable or otherwise)."""
    try:
        importlib.metadata.distribution("mediascribe")
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


@unittest.skipUnless(_package_is_installed(), "mediascribe not installed; entry_points not visible")
class TestPluginDiscovery(unittest.TestCase):
    """The example vimeo plugin must show up in the registry."""

    @classmethod
    def setUpClass(cls):
        # Force a fresh discovery in case other tests cached it.
        from mediascribe.plugins import clear_cache
        clear_cache()

    def test_list_downloaders_includes_vimeo(self):
        from mediascribe.plugins import list_downloaders
        names = list_downloaders()
        self.assertIn("vimeo", names,
                      f"vimeo plugin missing; got: {names}")

    def test_get_downloader_returns_class(self):
        from mediascribe.plugins import get_downloader
        cls = get_downloader("vimeo")
        self.assertIsNotNone(cls)
        self.assertEqual(cls.name, "vimeo")

    def test_get_downloader_unknown_returns_none(self):
        from mediascribe.plugins import get_downloader
        self.assertIsNone(get_downloader("__no_such_plugin__"))

    def test_vimeo_supports(self):
        from mediascribe.models import SourceRef
        from mediascribe.plugins import get_downloader
        cls = get_downloader("vimeo")
        ref = SourceRef(raw_input="https://vimeo.com/123", kind="vimeo", url="https://vimeo.com/123")
        self.assertTrue(cls().supports(ref))

    def test_clear_cache_forces_rediscovery(self):
        from mediascribe import plugins
        plugins.clear_cache()
        # First call populates the cache; second is a no-op.
        plugins.list_downloaders()
        plugins.clear_cache()
        # Should still work after clearing.
        self.assertIn("vimeo", plugins.list_downloaders())


class TestPluginHookspecs(unittest.TestCase):
    """Hookspecs are abstract hints, not enforced base classes."""

    def test_downloader_hookspec_is_class(self):
        from mediascribe.plugins import DownloaderHookSpec
        self.assertTrue(hasattr(DownloaderHookSpec, "supports"))
        self.assertTrue(hasattr(DownloaderHookSpec, "download"))

    def test_transcriber_hookspec_is_class(self):
        from mediascribe.plugins import TranscriberHookSpec
        self.assertTrue(hasattr(TranscriberHookSpec, "transcribe"))

    def test_url_transformer_hookspec_is_class(self):
        from mediascribe.plugins import URLTransformerHookSpec
        self.assertTrue(hasattr(URLTransformerHookSpec, "transform"))


class TestUrlTransformerIteration(unittest.TestCase):
    """The pipeline uses iter_url_transformers() at runtime."""

    def test_iter_returns_iterable(self):
        from mediascribe.plugins import iter_url_transformers
        # In a clean test env the iterator may be empty; that's fine.
        result = list(iter_url_transformers())
        self.assertIsInstance(result, list)


class TestExampleTransformer(unittest.TestCase):
    """The t.cn example transformer rejects non-matching URLs."""

    def test_tcn_rejects_non_matching(self):
        # Add examples dir to path so we can import the example module.
        examples = ROOT / "examples" / "plugins"
        sys.path.insert(0, str(examples))
        try:
            from tcn_transformer import transform  # type: ignore
        except ImportError:
            self.skipTest("examples/plugins not importable")
        self.assertIsNone(transform("https://example.com/x"))
        # Matching URL passes through (stub does no real network call).
        self.assertEqual(transform("https://t.cn/abc123"), "https://t.cn/abc123")


if __name__ == "__main__":
    unittest.main()
