"""平台路由与凭据防线的安全回归测试。

2026-10-29 对抗性审查（P0-1/SEC-001/SEC-003）的防回归锚点：

1. 平台识别必须按**主机名**匹配 —— 子串匹配可被
   ``https://evil.com/mp.weixin.qq.com/s`` 这类路径伪装绕过，把微信
   登录 cookies 发往攻击者主机。
2. ``TRUSTED_DOMAINS`` 必须是平台指纹表与短链域名表的超集 ——
   batch 下载前检查（``check_url_safety``）曾因两表漂移误拒受支持平台。
3. 下载器入口必须断言目标域名；微信 cookies 只准出腾讯系域名。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mediascribe.inputs import parse_source
from mediascribe.models import SourceRef
from mediascribe.security import (
    TENCENT_CDN_DOMAINS,
    TRUSTED_DOMAINS,
    check_url_safety,
    is_tencent_cdn_url,
)
from mediascribe.url_utils import (
    _PLATFORM_FINGERPRINTS,
    _RESOLVABLE_SHORT_DOMAINS,
    match_platform,
)


class TestPlatformRoutingHostname(unittest.TestCase):
    """match_platform 必须按主机名匹配，路径伪装一律不命中。"""

    def test_real_platform_hosts_match(self):
        cases = {
            "https://www.bilibili.com/video/BV1xx411c7mD": "bilibili",
            "https://b23.tv/abc": "bilibili",
            "https://www.douyin.com/video/123": "douyin",
            "https://v.douyin.com/abc/": "douyin",
            "https://www.douyinvod.com/x.mp4": "douyin",
            "https://youtu.be/abc": "youtube",
            "https://www.tiktok.com/@x/video/1": "tiktok",
            "https://www.xiaohongshu.com/explore/abc": "xiaohongshu",
            "https://www.xhslink.com/abc": "xiaohongshu",
            "https://mp.weixin.qq.com/s?__biz=MzA": "wechat_mp",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(match_platform(url), expected)

    def test_substring_disguise_does_not_match(self):
        """平台域名出现在路径/query/userinfo 时不得命中 —— 这是
        cookies 外泄攻击链（P0-1）的根。"""
        evil_urls = [
            "https://evil.com/mp.weixin.qq.com/s?__biz=x",
            "https://evil.com/?douyin.com",
            "https://evil.com/?x=bilibili.com",
            "http://douyin.com@127.0.0.1/video/1",
            "https://evil.com/xhslink.com/abc",
        ]
        for url in evil_urls:
            with self.subTest(url=url):
                self.assertIsNone(match_platform(url))

    def test_subdomain_matches_and_case_insensitive(self):
        self.assertEqual(match_platform("https://music.youtube.com/watch?v=1"), "youtube")
        self.assertEqual(match_platform("https://MP.WEIXIN.QQ.COM/s?__biz=MzA"), "wechat_mp")
        # 近似域名（前缀拼接）不算子域
        self.assertIsNone(match_platform("https://notbilibili.com/video/1"))


class TestParseSourceRouting(unittest.TestCase):
    """伪装 URL 不得进入平台下载器分支。"""

    def test_evil_wechat_url_falls_back_to_generic(self):
        src = parse_source("https://evil.com/mp.weixin.qq.com/s?__biz=x")
        self.assertEqual(src.kind, "video")  # 通用 yt-dlp 路径，无微信 cookies

    def test_real_wechat_url_routes_to_wechat(self):
        src = parse_source("https://mp.weixin.qq.com/s?__biz=MzA&mid=1")
        self.assertEqual(src.kind, "wechat_mp")


class TestTrustedDomainsConsistency(unittest.TestCase):
    """TRUSTED_DOMAINS 必须覆盖全部平台域名与短链域名（防两表漂移）。"""

    def test_trusted_domains_superset_of_fingerprints(self):
        missing = {d for _, domains in _PLATFORM_FINGERPRINTS for d in domains} - TRUSTED_DOMAINS
        self.assertEqual(missing, set(), f"指纹表域名未进 TRUSTED_DOMAINS: {missing}")

    def test_trusted_domains_superset_of_short_domains(self):
        # 只约束真正会被跟随解析的短链域（_SHORT_DOMAINS 里的 t.cn 是
        # 微博短链，本产品不处理，不要求进下载白名单）
        missing = set(_RESOLVABLE_SHORT_DOMAINS) - TRUSTED_DOMAINS
        self.assertEqual(missing, set(), f"短链域名未进 TRUSTED_DOMAINS: {missing}")

    def test_batch_style_check_accepts_all_platforms(self):
        """batch 下载前的 check_url_safety 不得误拒任何受支持平台。"""
        urls = [
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "https://www.douyin.com/video/123",
            "https://www.douyinvod.com/x.mp4",
            "https://www.xiaohongshu.com/explore/abc",
            "https://www.tiktok.com/@x/video/1",
            "https://mp.weixin.qq.com/s?__biz=MzA",
            "https://youtu.be/abc",
        ]
        for url in urls:
            with self.subTest(url=url):
                ok, reason = check_url_safety(url)
                self.assertTrue(ok, f"{url} 被误拒: {reason}")


class TestDownloaderEntryGuards(unittest.TestCase):
    """下载器入口必须按主机名拒绝伪装 URL（异常须发生在任何网络请求前）。"""

    def _source(self, url: str) -> SourceRef:
        return SourceRef(raw_input=url, kind="video", url=url)

    def test_wechat_rejects_non_wechat_host(self):
        from mediascribe.config import Settings
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                WechatMpDownloader().download(
                    self._source("https://evil.com/mp.weixin.qq.com/s?x"),
                    Settings(workspace_root=Path(tmp)),
                )

    def test_wechat_accepts_real_host_entry(self):
        from mediascribe.config import Settings
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        dl = WechatMpDownloader()
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(WechatMpDownloader, "_fetch_html", return_value=None),
        ):
            with self.assertRaises(RuntimeError):
                dl.download(
                    self._source("https://mp.weixin.qq.com/s?__biz=MzA"),
                    Settings(workspace_root=Path(tmp)),
                )

    def test_douyin_rejects_non_douyin_host(self):
        from mediascribe.config import Settings
        from mediascribe.downloaders.douyin import DouyinDownloader

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                DouyinDownloader().download(
                    self._source("https://evil.com/?douyin.com"),
                    Settings(workspace_root=Path(tmp)),
                )

    def test_xiaohongshu_rejects_non_xhs_host(self):
        from mediascribe.config import Settings
        from mediascribe.downloaders.xiaohongshu import XiaohongshuDownloader

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                XiaohongshuDownloader().download(
                    self._source("https://evil.com/xhslink.com/abc"),
                    Settings(workspace_root=Path(tmp)),
                )


class TestWechatCredentialGating(unittest.TestCase):
    """微信 cookies 只准发往腾讯系域名。"""

    def test_is_tencent_cdn_url(self):
        for url in (
            "https://mp.weixin.qq.com/s?__biz=x",
            "https://mmbiz.qpic.cn/abc.jpg",
            "https://mpvideo.qpic.cn/x.mp4",
            "https://srv.qlogo.cn/av.png",
            "https://mmbiz.wpimg.cn/p.png",
        ):
            with self.subTest(url=url):
                self.assertTrue(is_tencent_cdn_url(url))
        for url in ("https://evil.com/pixel.png", "https://notqpic.cn/x.jpg"):
            with self.subTest(url=url):
                self.assertFalse(is_tencent_cdn_url(url))

    def test_tencent_domains_cover_wechat_host(self):
        self.assertIn("qq.com", TENCENT_CDN_DOMAINS)

    def test_cookies_for_withholds_on_non_tencent(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        dl = WechatMpDownloader()
        dl._active_cookies = {"SESSION": "secret"}
        self.assertEqual(dl._cookies_for("https://mmbiz.qpic.cn/a.jpg"), {"SESSION": "secret"})
        self.assertIsNone(dl._cookies_for("https://evil.com/pixel.png"))

    def test_extract_video_url_ignores_non_tencent(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        dl = WechatMpDownloader()
        evil_html = '<script>var video_url = "https://evil.com/x.mp4";</script>'
        self.assertIsNone(dl._extract_video_url(evil_html))
        good_html = '<script>var video_url = "https://mpvideo.qpic.cn/x.mp4";</script>'
        self.assertEqual(dl._extract_video_url(good_html), "https://mpvideo.qpic.cn/x.mp4")


if __name__ == "__main__":
    unittest.main()
