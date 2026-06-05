"""Tests for browser.py — Playwright wrapper for Douyin page scraping.

All Playwright interactions are mocked. We never start a real browser.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _make_mock_page(html_sequence: List[str] = None, raise_on_goto: Exception = None):
    """Build a MagicMock that mimics Playwright's Page API."""
    html_sequence = list(html_sequence or [])
    page = mock.MagicMock(name="Page")
    page._handlers = {}

    def content_side_effect():
        if html_sequence:
            return html_sequence.pop(0)
        return "<html></html>"

    page.content.side_effect = content_side_effect

    if raise_on_goto is not None:
        page.goto.side_effect = raise_on_goto
    else:
        page.goto.return_value = None

    def on(event, handler):
        page._handlers[event] = handler

    page.on.side_effect = on
    return page


def _fake_browser_manager(page: mock.MagicMock, headless: bool = True):
    """Return a BrowserManager-shaped object with __init__ bypassed."""
    from douyin_batch import browser as br_mod

    mgr = br_mod.BrowserManager.__new__(br_mod.BrowserManager)
    mgr._initialized = True
    mgr._headless = headless
    # Page factory returns the supplied page every time
    mgr._context = mock.MagicMock(name="Context")
    mgr._context.new_page.return_value = page
    mgr._browser = mock.MagicMock(name="Browser")
    mgr._playwright = mock.MagicMock(name="Playwright")
    # The singleton slot
    br_mod.BrowserManager._instance = mgr
    return mgr


class TestBrowserManagerLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        from douyin_batch import browser as br_mod

        br_mod.BrowserManager._instance = None

    def tearDown(self) -> None:
        from douyin_batch import browser as br_mod

        br_mod.BrowserManager._instance = None

    def test_singleton_returns_same_instance(self):
        from douyin_batch import browser as br_mod

        page = _make_mock_page()
        with mock.patch("playwright.sync_api.sync_playwright") as sp_cls:
            sp = sp_cls.return_value
            sp.start.return_value.chromium.launch.return_value.new_context.return_value = (
                mock.MagicMock()
            )
            mgr_a = br_mod.BrowserManager(headless=True)
            mgr_b = br_mod.BrowserManager(headless=True)
        self.assertIs(mgr_a, mgr_b)
        mgr_a.close()

    def test_new_page_uses_context(self):
        from douyin_batch import browser as br_mod

        page = _make_mock_page()
        mgr = _fake_browser_manager(page)
        result = mgr.new_page()
        self.assertIs(result, page)
        mgr._context.new_page.assert_called()

    def test_close_handles_exceptions(self):
        from douyin_batch import browser as br_mod

        mgr = _fake_browser_manager(_make_mock_page())
        mgr._context.close.side_effect = RuntimeError("already closed")
        mgr._browser.close.side_effect = RuntimeError("boom")
        mgr._playwright.stop.side_effect = RuntimeError("stop fail")
        # Must NOT raise
        mgr.close()
        # Singleton reset
        self.assertIsNone(br_mod.BrowserManager._instance)
        self.assertFalse(mgr._initialized)

    def test_close_with_clean_state(self):
        from douyin_batch import browser as br_mod

        mgr = _fake_browser_manager(_make_mock_page())
        mgr.close()
        mgr._context.close.assert_called_once()
        mgr._browser.close.assert_called_once()
        mgr._playwright.stop.assert_called_once()


class TestGetUserUrlFromVideo(unittest.TestCase):
    def setUp(self) -> None:
        from douyin_batch import browser as br_mod

        br_mod.BrowserManager._instance = None

    def tearDown(self) -> None:
        from douyin_batch import browser as br_mod

        br_mod.BrowserManager._instance = None

    def test_extracts_sec_uid_from_json_pattern(self):
        from douyin_batch import browser as br_mod

        sec_uid = "MS4wLjABAAAAEXAMPLE0000000000000000000000"
        html = f'<html><body><script>window._ROUTER_DATA = {{"sec_uid":"{sec_uid}"}};</script></body></html>'
        page = _make_mock_page([html])
        _fake_browser_manager(page)
        out = br_mod.get_user_url_from_video("https://www.douyin.com/video/123")
        self.assertEqual(out, f"https://www.douyin.com/user/{sec_uid}")
        page.close.assert_called()

    def test_extracts_sec_uid_from_query_param_pattern(self):
        from douyin_batch import browser as br_mod

        sec_uid = "MS4wLjABAAAAQUERY000000000000000000000000"
        html = f'<a href="/?sec_uid={sec_uid}&other=1">profile</a>'
        page = _make_mock_page([html])
        _fake_browser_manager(page)
        out = br_mod.get_user_url_from_video("https://www.douyin.com/video/456")
        self.assertEqual(out, f"https://www.douyin.com/user/{sec_uid}")

    def test_returns_none_when_no_match(self):
        from douyin_batch import browser as br_mod

        page = _make_mock_page(["<html><body>no sec_uid here</body></html>"])
        _fake_browser_manager(page)
        out = br_mod.get_user_url_from_video("https://www.douyin.com/video/789")
        self.assertIsNone(out)

    def test_returns_none_on_exception(self):
        from douyin_batch import browser as br_mod

        page = _make_mock_page(raise_on_goto=RuntimeError("network"))
        _fake_browser_manager(page)
        out = br_mod.get_user_url_from_video("https://www.douyin.com/video/000")
        self.assertIsNone(out)

    def test_picks_first_match_across_polls(self):
        from douyin_batch import browser as br_mod

        sec_uid = "MS4wLjABAAAAEVENTUAL00000000000000000000"
        # First two polls: nothing, third: hit
        html_seq = [
            "<html>nothing</html>",
            "<html>still nothing</html>",
            f'<html>"sec_uid":"{sec_uid}"</html>',
        ]
        page = _make_mock_page(html_seq)
        _fake_browser_manager(page)
        with mock.patch("douyin_batch.browser.time.sleep"):  # avoid real wait
            out = br_mod.get_user_url_from_video("https://www.douyin.com/video/1")
        self.assertEqual(out, f"https://www.douyin.com/user/{sec_uid}")


class TestGetUserVideos(unittest.TestCase):
    def setUp(self) -> None:
        from douyin_batch import browser as br_mod

        br_mod.BrowserManager._instance = None

    def tearDown(self) -> None:
        from douyin_batch import browser as br_mod

        br_mod.BrowserManager._instance = None

    def test_collects_videos_until_max(self):
        from douyin_batch import browser as br_mod

        # Each scroll returns the same set (no new), should stop after 2 rounds
        html_no_new = '<html><body><a href="/video/100">v</a></body></html>'
        page = _make_mock_page([html_no_new] * 20)
        _fake_browser_manager(page)
        with mock.patch("douyin_batch.browser.time.sleep"), mock.patch(
            "douyin_batch.browser.print"
        ) as mp:
            out = br_mod.get_user_videos("https://www.douyin.com/user/ABC", max_videos=5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["video_id"], "100")
        self.assertEqual(
            out[0]["url"], "https://www.douyin.com/video/100"
        )
        page.close.assert_called()

    def test_collects_multiple_videos(self):
        from douyin_batch import browser as br_mod

        html = (
            '<html><body>'
            '<a href="/video/1">a</a>'
            '<a href="/video/2">b</a>'
            '<a href="/video/3">c</a>'
            "</body></html>"
        )
        page = _make_mock_page([html] * 20)
        _fake_browser_manager(page)
        with mock.patch("douyin_batch.browser.time.sleep"), mock.patch(
            "douyin_batch.browser.print"
        ):
            out = br_mod.get_user_videos("https://www.douyin.com/user/ABC", max_videos=10)
        ids = sorted(v["video_id"] for v in out)
        self.assertEqual(ids, ["1", "2", "3"])
        # each entry has expected keys
        for v in out:
            self.assertIn("url", v)
            self.assertIn("video_id", v)
            self.assertTrue(v["url"].startswith("https://www.douyin.com/video/"))

    def test_deduplicates_videos(self):
        from douyin_batch import browser as br_mod

        html = '<html><a href="/video/1">v</a></html>'
        page = _make_mock_page([html] * 10)
        _fake_browser_manager(page)
        with mock.patch("douyin_batch.browser.time.sleep"), mock.patch(
            "douyin_batch.browser.print"
        ):
            out = br_mod.get_user_videos("https://www.douyin.com/user/X", max_videos=5)
        # Same video appearing many times → dedup → only 1
        self.assertEqual(len(out), 1)

    def test_returns_empty_on_goto_exception(self):
        from douyin_batch import browser as br_mod

        page = _make_mock_page(raise_on_goto=RuntimeError("nope"))
        _fake_browser_manager(page)
        with mock.patch("douyin_batch.browser.print"):
            out = br_mod.get_user_videos("https://www.douyin.com/user/X")
        self.assertEqual(out, [])

    def test_caps_at_max_videos(self):
        from douyin_batch import browser as br_mod

        # always new content each scroll
        htmls = [
            f'<html><a href="/video/{i}">v</a></html>' for i in range(1, 100)
        ]
        page = _make_mock_page(htmls)
        _fake_browser_manager(page)
        with mock.patch("douyin_batch.browser.time.sleep"), mock.patch(
            "douyin_batch.browser.print"
        ):
            out = br_mod.get_user_videos("https://www.douyin.com/user/X", max_videos=5)
        self.assertLessEqual(len(out), 5)


class TestGetMediaUrlFast(unittest.TestCase):
    def setUp(self) -> None:
        from douyin_batch import browser as br_mod

        br_mod.BrowserManager._instance = None

    def tearDown(self) -> None:
        from douyin_batch import browser as br_mod

        br_mod.BrowserManager._instance = None

    def test_captures_audio_url(self):
        from douyin_batch import browser as br_mod

        page = _make_mock_page()
        _fake_browser_manager(page)

        captured: Dict[str, Any] = {"url": None}

        def on_side_effect(event, handler):
            # simulate the page firing a response event
            if event == "response":
                handler(
                    type(
                        "Resp",
                        (),
                        {"url": "https://media-audio.douyinvod.com/abc.m4a"},
                    )()
                )

        page.on.side_effect = on_side_effect

        with mock.patch("douyin_batch.browser.time.sleep"):
            out = br_mod.get_media_url_fast("https://www.douyin.com/video/123", timeout=2)
        self.assertEqual(out, "https://media-audio.douyinvod.com/abc.m4a")

    def test_returns_none_on_no_capture(self):
        from douyin_batch import browser as br_mod

        page = _make_mock_page()
        _fake_browser_manager(page)

        with mock.patch("douyin_batch.browser.time.sleep"):
            out = br_mod.get_media_url_fast("https://www.douyin.com/video/123", timeout=1)
        self.assertIsNone(out)

    def test_returns_none_on_goto_exception(self):
        from douyin_batch import browser as br_mod

        page = _make_mock_page(raise_on_goto=RuntimeError("net"))
        _fake_browser_manager(page)
        out = br_mod.get_media_url_fast("https://www.douyin.com/video/123")
        self.assertIsNone(out)

    def test_ignores_non_audio_douyinvod_responses(self):
        from douyin_batch import browser as br_mod

        page = _make_mock_page()
        _fake_browser_manager(page)

        def on_side_effect(event, handler):
            if event == "response":
                # video response, not audio — should be ignored
                handler(
                    type(
                        "Resp",
                        (),
                        {"url": "https://video.douyinvod.com/abc.mp4"},
                    )()
                )

        page.on.side_effect = on_side_effect
        with mock.patch("douyin_batch.browser.time.sleep"):
            out = br_mod.get_media_url_fast("https://www.douyin.com/video/1", timeout=1)
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
