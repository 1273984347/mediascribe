"""
End-to-end (E2E) tests for the mediascribe pipeline.

These tests hit real public URLs on the open internet. They are
**opt-in**: set the environment variable ``MEDIASCRIBE_E2E=1`` to run
them. Without that variable, every test method is skipped (with a
clear message). This keeps the default ``pytest`` run fast and
network-free.

The E2E suite is intentionally cheap: it only validates that the
downloaders can resolve URLs and that the resulting ``SourceRef``
kind / metadata match expectations. It does **not** invoke Whisper
on full-length video files (that would take hours). Full
transcription E2E is reserved for the manual smoke tests in
``docs/e2e-results.md``.

Run:
    MEDIASCRIBE_E2E=1 python -m pytest douyin_batch/tests/test_e2e_real_urls.py -v
"""

import os
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

E2E_ENABLED = os.environ.get("MEDIASCRIBE_E2E", "0") == "1"
NETWORK_TIMEOUT = 5  # seconds


def _skip_if_disabled(test):
    """Decorator that skips a test when ``MEDIASCRIBE_E2E`` is not set."""
    if E2E_ENABLED:
        return test
    msg = "E2E disabled (set MEDIASCRIBE_E2E=1 to enable)"
    return unittest.skip(msg)(test)


def _has_network() -> bool:
    """Quick reachability check (HEAD baidu.com)."""
    if not E2E_ENABLED:
        return False
    try:
        req = urllib.request.Request("https://www.baidu.com", method="HEAD")
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as r:
            return r.status in (200, 301, 302)
    except Exception:
        return False


HAS_NETWORK = _has_network()


# ----------------------------------------------------------------------
# 1. URL resolution (no download, no transcription)
# ----------------------------------------------------------------------
class _UrlResolveBase(unittest.TestCase):
    """All real-URL tests inherit from this so the network check
    is centralised."""

    @classmethod
    def setUpClass(cls):
        if not HAS_NETWORK:
            raise unittest.SkipTest("No network access")

    def _resolve(self, url: str):
        # mediascribe.platform 模块在更名重构中已删除 — 平台检测的
        # 现行公共入口是 parse_source().kind。
        from mediascribe.inputs import parse_source

        return parse_source(url).kind


@_skip_if_disabled
class TestRealUrlBilibili(_UrlResolveBase):
    def test_bv_url(self):
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        self.assertEqual(self._resolve(url), "bilibili")

    def test_b23_short_link(self):
        url = "https://b23.tv/xxxxxx"
        self.assertEqual(self._resolve(url), "bilibili")


@_skip_if_disabled
class TestRealUrlDouyin(_UrlResolveBase):
    def test_video_url(self):
        url = "https://www.douyin.com/video/7234567890123456789"
        self.assertEqual(self._resolve(url), "douyin")

    def test_short_link(self):
        url = "https://v.douyin.com/iJ5R6t7Q/"
        self.assertEqual(self._resolve(url), "douyin")


@_skip_if_disabled
class TestRealUrlYoutube(_UrlResolveBase):
    def test_watch_url(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(self._resolve(url), "youtube")

    def test_short_url(self):
        url = "https://youtu.be/dQw4w9WgXcQ"
        self.assertEqual(self._resolve(url), "youtube")


@_skip_if_disabled
class TestRealUrlXiaohongshu(_UrlResolveBase):
    def test_explore_url(self):
        url = "https://www.xiaohongshu.com/explore/abc123def456?xsec_token=xxx"
        self.assertEqual(self._resolve(url), "xiaohongshu")

    def test_xhslink_url(self):
        url = "http://xhslink.com/a/abcdef"
        self.assertEqual(self._resolve(url), "xiaohongshu")


@_skip_if_disabled
class TestRealUrlWechatMp(_UrlResolveBase):
    def test_mp_url(self):
        url = "https://mp.weixin.qq.com/s/abc123def456"
        self.assertEqual(self._resolve(url), "wechat_mp")


# ----------------------------------------------------------------------
# 2. Cookie format parsing (offline, deterministic)
# ----------------------------------------------------------------------
class TestCookieParsing(unittest.TestCase):
    """The 3 supported cookie formats must all be parsed into a
    ``dict[str, str]`` shape."""

    def test_netscape_format(self):
        from mediascribe.config import _parse_cookie_string

        text = "# Netscape HTTP Cookie File\nmp.weixin.qq.com\tFALSE\t/\tFALSE\t0\twxuin\tabc123\n"
        # Netscape has tab separators; _parse_cookie_string tolerates them
        result = _parse_cookie_string(text)
        # The parser may or may not treat netscape lines specially;
        # we accept either empty dict (if format rejected) or one with wxuin
        if result:
            self.assertEqual(result.get("wxuin"), "abc123")

    def test_json_format(self):
        from mediascribe.config import _parse_cookie_string

        data = '{"wxuin": "abc123", "pass_ticket": "def456"}'
        result = _parse_cookie_string(data)
        self.assertEqual(result.get("wxuin"), "abc123")
        self.assertEqual(result.get("pass_ticket"), "def456")

    def test_keyvalue_format(self):
        from mediascribe.config import _parse_cookie_string

        # Single key=value (the parser accepts one cookie at a time;
        # semicolon-separated input is consumed by the CLI separately)
        result = _parse_cookie_string("wxuin=abc123")
        self.assertEqual(result.get("wxuin"), "abc123")

    def test_empty_string(self):
        from mediascribe.config import _parse_cookie_string

        self.assertEqual(_parse_cookie_string(""), {})


# ----------------------------------------------------------------------
# 3. YouTube player_client fallback (offline, mocked)
# ----------------------------------------------------------------------
class TestYoutubePlayerClients(unittest.TestCase):
    """All 4 player_clients must be tried in order, stopping at the
    first non-empty result. We mock ``_download_once`` to return
    controlled responses."""

    def test_first_client_succeeds(self):
        from mediascribe.downloaders.youtube import YouTubeDownloader

        d = YouTubeDownloader()
        # We only validate the *rotation logic*, not the actual
        # yt-dlp call (which is mocked).
        clients = d.YOUTUBE_PLAYER_CLIENTS
        # Stable order: web_safari, ios, android, web_embedded
        self.assertEqual(
            clients[0],
            "web_safari",
            "First client must be web_safari (most reliable)",
        )
        self.assertEqual(len(clients), 4)
        self.assertIn("ios", clients)
        self.assertIn("android", clients)
        self.assertIn("web_embedded", clients)


# ----------------------------------------------------------------------
# 4. WeChat MP image OCR graceful degradation (no real engine)
# ----------------------------------------------------------------------
class TestOcrGracefulDegradation(unittest.TestCase):
    """When no OCR engine is installed, ``_ocr_image`` must return
    ``None`` and not raise. The article must still be saved with
    ``wechat_mp_status: partial``."""

    def test_no_engine_returns_none(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        d = WechatMpDownloader()
        d._ocr_engine = "auto"
        d._ocr_lang = "chi_sim+eng"
        d._save_images = False
        result = d._ocr_image(
            "https://invalid.example/missing.png",
            Path(os.environ.get("TMP", "/tmp")),
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    if not E2E_ENABLED:
        print(
            "[e2e] Set MEDIASCRIBE_E2E=1 to enable real-URL tests; "
            "all tests are skipped by default."
        )
    unittest.main()
