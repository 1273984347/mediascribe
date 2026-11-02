"""2026-11-02 安全审计 SEC-001/002/003 修复的回归测试。

* SEC-001: 携带 cookies 的请求手动接管重定向, 逐跳断言腾讯域, 跨跳不带凭据
* SEC-002: WechatMpDownloader cookies 按调用刷新 + Web 管线缓存不再复用 downloader
* SEC-003: MCP HTTP 强制 application/json + 拒绝外部 Origin + URL 域名白名单
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

import requests

from mediascribe.downloaders._http_download import cred_get, stream_download
from mediascribe.mcp_server import _origin_allowed


class _FakeResp:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def iter_content(self, chunk_size=64 * 1024):
        yield b"data"


class TestCredGetRedirectGuard(unittest.TestCase):
    """SEC-001: cred_get 逐跳守卫。"""

    def test_first_hop_carries_cookies_later_hops_do_not(self):
        seen = []

        def fake_get(url, **kw):
            seen.append((url, kw.get("cookies"), kw.get("allow_redirects")))
            if url == "https://mp.weixin.qq.com/s/abc":
                return _FakeResp(302, {"Location": "https://mp.weixin.qq.com/s/def"})
            return _FakeResp(200)

        with mock.patch.object(requests, "get", side_effect=fake_get):
            resp = cred_get(
                "https://mp.weixin.qq.com/s/abc",
                cookies={"SESSDATA_X": "v"},
                redirect_guard=lambda u: True,
            )
        self.assertEqual(resp.status_code, 200)
        first_url, first_cookies, first_ar = seen[0]
        self.assertIn("SESSDATA_X", first_cookies or {})
        self.assertFalse(first_ar)  # 手动接管
        second_url, second_cookies, _ = seen[1]
        self.assertTrue(second_cookies is None, "后续跳不得携带 cookies")
        self.assertIn("/s/def", second_url)

    def test_off_domain_redirect_aborts(self):
        def fake_get(url, **kw):
            return _FakeResp(302, {"Location": "https://evil.example.com/pickup"})

        with mock.patch.object(requests, "get", side_effect=fake_get):
            with self.assertRaisesRegex(RuntimeError, "白名单|outside the allowed"):
                cred_get(
                    "https://mp.weixin.qq.com/s/abc",
                    cookies={"a": "b"},
                    redirect_guard=lambda u: "qq.com" in u or "weixin" in u,
                )

    def test_redirect_loop_aborts(self):
        calls = {"n": 0}

        def fake_get(url, **kw):
            calls["n"] += 1
            return _FakeResp(302, {"Location": "https://mp.weixin.qq.com/s/abc"})

        with mock.patch.object(requests, "get", side_effect=fake_get):
            with self.assertRaisesRegex(RuntimeError, "重定向次数过多|too many redirects"):
                cred_get(
                    "https://mp.weixin.qq.com/s/abc",
                    cookies={"a": "b"},
                    redirect_guard=lambda u: True,
                )
        self.assertLessEqual(calls["n"], 7)


class TestStreamDownloadGuard(unittest.TestCase):
    def test_stream_download_with_guard_blocks_off_domain(self):
        def fake_sess_get(url, **kw):
            return _FakeResp(302, {"Location": "https://evil.example.com/x.mp4"})

        sess = requests.Session()
        with mock.patch.object(sess, "get", side_effect=fake_sess_get):
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as td:
                with self.assertRaisesRegex(RuntimeError, "白名单|outside the allowed"):
                    stream_download(
                        "https://mp.weixin.qq.com/v.mp4",
                        Path(td) / "out.mp4",
                        session=sess,
                        cookies={"a": "b"},
                        redirect_guard=lambda u: "qq.com" in u,
                        timeout=1,
                    )


class TestWechatCookieRefresh(unittest.TestCase):
    """SEC-002: cookies 按调用刷新 — B 无 cookies 不再吃 A 的会话。"""

    def test_active_cookies_refreshed_per_call(self):
        from types import SimpleNamespace

        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        dl = WechatMpDownloader()
        dl._refresh_cookies(SimpleNamespace(wechat_cookies={"A": "1"}))
        self.assertEqual(dl._active_cookies, {"A": "1"})

        dl._refresh_cookies(SimpleNamespace(wechat_cookies={}))
        self.assertEqual(dl._active_cookies, {}, "无 cookies 的请求不得继承上一请求的会话")

    def test_explicit_cookies_not_swallowed(self):
        from types import SimpleNamespace

        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        dl = WechatMpDownloader()
        dl._refresh_cookies(SimpleNamespace(wechat_cookies={"A": "1"}))
        dl._refresh_cookies(SimpleNamespace(wechat_cookies={"C": "3"}))
        self.assertEqual(dl._active_cookies, {"C": "3"}, "显式 cookies 不得被静默丢弃")


class TestMcpOriginAllowlist(unittest.TestCase):
    """SEC-003: Origin 白名单。"""

    def test_loopback_origins_allowed(self):
        for origin in ("http://127.0.0.1:8080", "http://localhost:3000"):
            self.assertTrue(_origin_allowed(origin), origin)

    def test_extension_origin_allowed(self):
        self.assertTrue(_origin_allowed("chrome-extension://abcdef/home"))

    def test_foreign_origin_rejected(self):
        for origin in ("https://evil.example.com", "http://192.168.1.5:9999"):
            self.assertFalse(_origin_allowed(origin), origin)


class TestMcpDoPostJsonGate(unittest.TestCase):
    """SEC-003: text/plain 简单请求必须被 415 拦下; 外部 Origin 403。"""

    @classmethod
    def setUpClass(cls):
        import threading

        from mediascribe.mcp_server import _make_http_server

        cls.server = _make_http_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, ctype=None, origin=None):
        headers = {}
        if ctype:
            headers["Content-Type"] = ctype
        if origin:
            headers["Origin"] = origin
        return requests.post(
            f"http://127.0.0.1:{self.port}",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            headers=headers,
            timeout=5,
        )

    def test_text_plain_is_415(self):
        resp = self._post(ctype="text/plain")
        self.assertEqual(resp.status_code, 415)

    def test_missing_ctype_is_415(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 415)

    def test_foreign_origin_is_403(self):
        resp = self._post(ctype="application/json", origin="https://evil.example.com")
        self.assertEqual(resp.status_code, 403)

    def test_valid_json_passes_gate(self):
        resp = self._post(ctype="application/json")
        self.assertEqual(resp.status_code, 200)


class TestMcpTranscribeUrlAllowlist(unittest.TestCase):
    """SEC-003: transcribe_video 的 URL 源过域名白名单。"""

    def test_evil_url_rejected(self):
        from mediascribe.mcp_server import _tool_transcribe_video

        with self.assertRaises(ValueError):
            _tool_transcribe_video({"source": "https://evil.example.com/video.mp4"})


if __name__ == "__main__":
    unittest.main()
