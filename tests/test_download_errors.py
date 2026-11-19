"""P1-5(2026-11-20 架构评审)— 下载错误分类 / 主机节流 / 微信风控检测。

覆盖:
1. classify_status:gone/bot_wall/transient/fatal 状态码映射
2. classify_exception:HTTPError / 超时 / 未知 / 幂等
3. raise_for_status_classified:HTTPError → DownloadError 升格
4. throttle_host:同主机间隔、跨主机不受影响、0 关闭
5. wechat_mp 1023/1024 风控码检测 → bot_wall 穿透
"""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import requests

from mediascribe.downloaders._http_download import _LAST_REQUEST_TS, throttle_host
from mediascribe.downloaders.download_errors import (
    CATEGORY_BOT_WALL,
    CATEGORY_FATAL,
    CATEGORY_GONE,
    CATEGORY_TRANSIENT,
    DownloadError,
    classify_exception,
    classify_status,
    raise_for_status_classified,
)


class TestClassifyStatus(unittest.TestCase):
    def test_gone(self):
        self.assertEqual(classify_status(404), CATEGORY_GONE)
        self.assertEqual(classify_status(410), CATEGORY_GONE)

    def test_bot_wall(self):
        for status in (403, 412, 429):
            self.assertEqual(classify_status(status), CATEGORY_BOT_WALL, status)

    def test_transient(self):
        for status in (408, 500, 502, 503, 504):
            self.assertEqual(classify_status(status), CATEGORY_TRANSIENT, status)

    def test_fatal_and_none(self):
        self.assertEqual(classify_status(400), CATEGORY_FATAL)
        self.assertEqual(classify_status(200), CATEGORY_FATAL)
        self.assertEqual(classify_status(None), CATEGORY_FATAL)


class TestClassifyException(unittest.TestCase):
    def test_http_error_404_is_gone(self):
        resp = SimpleNamespace(status_code=404)
        exc = requests.HTTPError("404 Client Error", response=resp)
        err = classify_exception(exc)
        self.assertEqual(err.category, CATEGORY_GONE)
        self.assertEqual(err.status, 404)

    def test_timeout_is_transient(self):
        err = classify_exception(requests.Timeout("timed out"))
        self.assertEqual(err.category, CATEGORY_TRANSIENT)

    def test_unknown_is_fatal(self):
        err = classify_exception(ValueError("bad"))
        self.assertEqual(err.category, CATEGORY_FATAL)

    def test_download_error_passthrough(self):
        original = DownloadError(CATEGORY_BOT_WALL, "wall")
        self.assertIs(classify_exception(original), original)

    def test_invalid_category_falls_to_fatal(self):
        err = DownloadError("nonsense", "msg")
        self.assertEqual(err.category, CATEGORY_FATAL)


class TestRaiseForStatusClassified(unittest.TestCase):
    def test_http_error_upgraded(self):
        resp = SimpleNamespace(
            status_code=403,
            raise_for_status=lambda: (_ for _ in ()).throw(
                requests.HTTPError("403", response=SimpleNamespace(status_code=403))
            ),
        )
        with self.assertRaises(DownloadError) as ctx:
            raise_for_status_classified(resp)
        self.assertEqual(ctx.exception.category, CATEGORY_BOT_WALL)

    def test_ok_passes_through(self):
        resp = SimpleNamespace(status_code=200, raise_for_status=lambda: None)
        raise_for_status_classified(resp)  # 不抛


class TestThrottleHost(unittest.TestCase):
    def setUp(self):
        _LAST_REQUEST_TS.clear()

    def tearDown(self):
        _LAST_REQUEST_TS.clear()

    def test_same_host_waits_interval(self):
        env = mock.patch.dict("os.environ", {"MEDIASCRIBE_HOST_MIN_INTERVAL_S": "0.2"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        sleeps = []
        with mock.patch(
            "mediascribe.downloaders._http_download.time.sleep",
            side_effect=sleeps.append,
        ):
            throttle_host("https://example.com/a")
            throttle_host("https://example.com/b")
        self.assertEqual(len(sleeps), 1, "第二次同主机请求必须等待")
        self.assertGreater(sleeps[0], 0.0)

    def test_cross_host_not_affected(self):
        env = mock.patch.dict("os.environ", {"MEDIASCRIBE_HOST_MIN_INTERVAL_S": "0.2"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        sleeps = []
        with mock.patch(
            "mediascribe.downloaders._http_download.time.sleep",
            side_effect=sleeps.append,
        ):
            throttle_host("https://a.example.com/x")
            throttle_host("https://b.example.com/x")
        self.assertEqual(sleeps, [], "跨主机不受同主机节流影响")

    def test_zero_disables(self):
        env = mock.patch.dict("os.environ", {"MEDIASCRIBE_HOST_MIN_INTERVAL_S": "0"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        slept = []
        with mock.patch(
            "mediascribe.downloaders._http_download.time.sleep",
            side_effect=slept.append,
        ):
            throttle_host("https://example.com/a")
            throttle_host("https://example.com/b")
        self.assertEqual(slept, [])

    def test_concurrent_calls_stay_spaced(self):
        """并发线程经锁串行化记账,间隔不被吞掉。"""
        env = mock.patch.dict(
            "os.environ", {"MEDIASCRIBE_HOST_MIN_INTERVAL_S": "0.05"}, clear=False
        )
        env.start()
        self.addCleanup(env.stop)
        sleeps: list = []
        lock = threading.Lock()

        def fake_sleep(sec):
            with lock:
                sleeps.append(sec)

        with mock.patch(
            "mediascribe.downloaders._http_download.time.sleep", side_effect=fake_sleep
        ):
            threads = [
                threading.Thread(target=throttle_host, args=("https://example.com/x",))
                for _ in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(sleeps), 3, "首请求不等,后续 3 个都要等")


class TestWechatRiskDetection(unittest.TestCase):
    def test_errcode_1023_detected(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        html = '<html><script>{"errcode":1023,"errmsg":"freq"}</script></html>'
        with self.assertRaises(DownloadError) as ctx:
            WechatMpDownloader._assert_not_risk_limited(html)
        self.assertEqual(ctx.exception.category, CATEGORY_BOT_WALL)
        self.assertIn("1023", str(ctx.exception))

    def test_errcode_1024_detected(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        html = '{"errcode": 1024}'
        with self.assertRaises(DownloadError):
            WechatMpDownloader._assert_not_risk_limited(html)

    def test_normal_html_passes(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        html = '<div id="js_content">正常正文 1023 次播放</div>'
        WechatMpDownloader._assert_not_risk_limited(html)  # 不抛


class TestFetchHtmlPropagatesBotWall(unittest.TestCase):
    def test_fetch_html_reraises_download_error(self):
        """_fetch_html 的兜底 except 不得吞掉 DownloadError(批处理要按类别路由)。"""
        from mediascribe.downloaders import wechat_mp as wm

        dl = wm.WechatMpDownloader.__new__(wm.WechatMpDownloader)
        dl._cookies = {}
        dl._active_cookies = {}  # SEC-002 会话态字段,_cookies_for 会读
        html_with_code = '{"errcode":1023}'
        with (
            mock.patch.object(
                wm,
                "cred_get",
                return_value=SimpleNamespace(
                    status_code=200,
                    raise_for_status=lambda: None,
                    apparent_encoding="utf-8",
                    text=html_with_code,
                ),
            ),
            mock.patch.object(wm, "is_tencent_cdn_url", side_effect=lambda u: True),
        ):
            with self.assertRaises(DownloadError):
                dl._fetch_html("https://mp.weixin.qq.com/s/abc")


if __name__ == "__main__":
    unittest.main()
