"""Tests for performance optimisations (round 6 / task F)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


class TestEasyOcrReaderCache(unittest.TestCase):
    """The easyocr Reader must be cached so subsequent calls don't
    pay the ~3s load cost."""

    def setUp(self):
        from mediascribe.downloaders import wechat_mp
        wechat_mp.clear_ocr_cache()
        self.mod = wechat_mp

    def tearDown(self):
        self.mod.clear_ocr_cache()

    def test_cache_hit_returns_same_object(self):
        # Pre-populate the cache with a fake reader
        sentinel = object()
        self.mod._EASYOCR_READER_CACHE[("ch_sim", "en")] = sentinel
        # Should return the cached object without importing easyocr
        r1 = self.mod._get_easyocr_reader(["ch_sim", "en"])
        r2 = self.mod._get_easyocr_reader(("ch_sim", "en"))
        self.assertIs(r1, sentinel)
        self.assertIs(r2, sentinel)

    def test_cache_miss_raises_when_easyocr_missing(self):
        """When easyocr is not installed and the cache is empty,
        the import will fail. We assert this propagates the
        ImportError so callers can fall back gracefully."""
        # Force a cache miss
        self.assertNotIn(("missing", "lang"), self.mod._EASYOCR_READER_CACHE)
        with self.assertRaises(ImportError):
            self.mod._get_easyocr_reader(["missing", "lang"])

    def test_clear_cache_empties_dict(self):
        self.mod._EASYOCR_READER_CACHE[("a",)] = object()
        self.assertEqual(len(self.mod._EASYOCR_READER_CACHE), 1)
        self.mod.clear_ocr_cache()
        self.assertEqual(self.mod._EASYOCR_READER_CACHE, {})


class TestParallelOcr(unittest.TestCase):
    """``_ocr_images`` uses a ThreadPoolExecutor to process multiple
    images in parallel."""

    def test_concurrent_invocation(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader
        d = WechatMpDownloader()
        d._ocr_engine = "auto"
        d._ocr_lang = "chi_sim+eng"
        d._save_images = False
        # Patch _ocr_image to a fast stub so the test doesn't need
        # network or PIL.
        from unittest.mock import patch

        def stub_ocr(url, save_dir):
            return f"text-for-{url}"

        with patch.object(d, "_ocr_image", side_effect=stub_ocr):
            texts, success, total = d._ocr_images(
                ["u1", "u2", "u3", "u4"],
                Path("/tmp"),
            )
        self.assertEqual(total, 4)
        self.assertEqual(success, 4)
        self.assertEqual(set(texts), {
            "text-for-u1", "text-for-u2",
            "text-for-u3", "text-for-u4",
        })

    def test_empty_list_returns_zero(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader
        d = WechatMpDownloader()
        texts, success, total = d._ocr_images([], Path("/tmp"))
        self.assertEqual(total, 0)
        self.assertEqual(success, 0)
        self.assertEqual(texts, [])

    def test_partial_failures_counted_correctly(self):
        from unittest.mock import patch

        from mediascribe.downloaders.wechat_mp import WechatMpDownloader
        d = WechatMpDownloader()

        def stub_ocr(url, save_dir):
            if "fail" in url:
                return None
            return f"text-for-{url}"

        with patch.object(d, "_ocr_image", side_effect=stub_ocr):
            texts, success, total = d._ocr_images(
                ["u1", "u2-fail", "u3", "u4-fail"],
                Path("/tmp"),
            )
        self.assertEqual(total, 4)
        self.assertEqual(success, 2)
        self.assertEqual(set(texts), {"text-for-u1", "text-for-u3"})


if __name__ == "__main__":
    unittest.main()
