"""
测试新下载源：YouTube / 小红书

覆盖范围：
- video2text.inputs.parse_source 对 YouTube / 小红书 URL 的路由
- video2text.downloaders.YouTubeDownloader 的 supports() 与参数
- video2text.downloaders.XiaohongshuDownloader 的 supports() 与 URL 提取兜底
- video2text.models.SourceRef kind 字符串
- downloaders/__init__.py 导出
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


class TestInputRouting(unittest.TestCase):
    """parse_source 对 YouTube / 小红书 URL 的路由"""

    def setUp(self):
        from video2text.inputs import parse_source

        self.parse_source = parse_source

    def test_youtube_watch_url(self):
        s = self.parse_source("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(s.kind, "youtube")
        self.assertIn("youtube.com", s.url)

    def test_youtube_short_url(self):
        s = self.parse_source("https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(s.kind, "youtube")
        self.assertIn("youtu.be", s.url)

    def test_youtube_mobile_url(self):
        s = self.parse_source("https://m.youtube.com/watch?v=abc")
        self.assertEqual(s.kind, "youtube")

    def test_youtube_nocookie_url(self):
        s = self.parse_source("https://www.youtube-nocookie.com/embed/abc")
        self.assertEqual(s.kind, "youtube")

    def test_xiaohongshu_explore_url(self):
        s = self.parse_source("https://www.xiaohongshu.com/explore/abc123?xsec_token=foo")
        self.assertEqual(s.kind, "xiaohongshu")

    def test_xiaohongshu_discovery_url(self):
        s = self.parse_source("https://www.xiaohongshu.com/discovery/item/abc123")
        self.assertEqual(s.kind, "xiaohongshu")

    def test_xiaohongshu_xhslink(self):
        s = self.parse_source("https://xhslink.com/a/abcdef")
        self.assertEqual(s.kind, "xiaohongshu")

    def test_bilibili_still_works(self):
        s = self.parse_source("https://www.bilibili.com/video/BV1xx411c7mD")
        self.assertEqual(s.kind, "bilibili")

    def test_douyin_still_works(self):
        s = self.parse_source("https://www.douyin.com/video/1234567890")
        self.assertEqual(s.kind, "douyin")

    def test_local_file_still_works(self):
        # 不存在的本地文件，fallback 到 kind="video"
        s = self.parse_source("Z:/nonexistent/path/file.mp4")
        # 路径在 Windows 上不存在，会走 fallback
        self.assertIn(s.kind, ("video", "audio"))


class TestYouTubeDownloader(unittest.TestCase):
    """YouTubeDownloader 单元测试"""

    def test_import_and_registered(self):
        from video2text.downloaders import YouTubeDownloader

        self.assertEqual(YouTubeDownloader.name, "youtube")

    def test_supports_youtube_url(self):
        from video2text.downloaders import YouTubeDownloader
        from video2text.models import SourceRef

        d = YouTubeDownloader()
        self.assertTrue(d.supports(SourceRef(raw_input="x", kind="youtube", url="https://www.youtube.com/watch?v=x")))
        self.assertTrue(d.supports(SourceRef(raw_input="x", kind="video", url="https://youtu.be/abc")))
        self.assertTrue(d.supports(SourceRef(raw_input="x", kind="video", url="https://m.youtube.com/watch?v=abc")))
        self.assertTrue(d.supports(SourceRef(raw_input="x", kind="video", url="https://www.youtube-nocookie.com/embed/abc")))

    def test_does_not_support_bilibili(self):
        from video2text.downloaders import YouTubeDownloader
        from video2text.models import SourceRef

        d = YouTubeDownloader()
        self.assertFalse(d.supports(SourceRef(raw_input="x", kind="bilibili", url="https://www.bilibili.com/video/BV1xx")))
        self.assertFalse(d.supports(SourceRef(raw_input="x", kind="douyin", url="https://www.douyin.com/video/1")))

    def test_player_clients_list(self):
        from video2text.downloaders import YouTubeDownloader

        clients = YouTubeDownloader.YOUTUBE_PLAYER_CLIENTS
        # 必须包含 web_safari 与 ios
        self.assertIn("web_safari", clients)
        self.assertIn("ios", clients)
        # 必须 ≥ 2 个客户端（轮换）
        self.assertGreaterEqual(len(clients), 2)


class TestXiaohongshuDownloader(unittest.TestCase):
    """XiaohongshuDownloader 单元测试"""

    def test_import_and_registered(self):
        from video2text.downloaders import XiaohongshuDownloader

        self.assertEqual(XiaohongshuDownloader.name, "xiaohongshu")

    def test_supports_xhs_url(self):
        from video2text.downloaders import XiaohongshuDownloader
        from video2text.models import SourceRef

        d = XiaohongshuDownloader()
        self.assertTrue(d.supports(SourceRef(raw_input="x", kind="xiaohongshu", url="https://www.xiaohongshu.com/explore/abc")))
        self.assertTrue(d.supports(SourceRef(raw_input="x", kind="video", url="https://xhslink.com/a/abcdef")))
        self.assertTrue(d.supports(SourceRef(raw_input="x", kind="video", url="https://www.xiaohongshu.com/discovery/item/abc")))

    def test_does_not_support_other(self):
        from video2text.downloaders import XiaohongshuDownloader
        from video2text.models import SourceRef

        d = XiaohongshuDownloader()
        self.assertFalse(d.supports(SourceRef(raw_input="x", kind="youtube", url="https://www.youtube.com/watch?v=x")))
        self.assertFalse(d.supports(SourceRef(raw_input="x", kind="bilibili", url="https://www.bilibili.com/video/BV1xx")))

    def test_grep_video_url(self):
        from video2text.downloaders.xiaohongshu import XiaohongshuDownloader

        # 视频 CDN
        text1 = 'window.__INITIAL_STATE__ = {"video":{"media":{"stream":{"h264":["https://sns-video-bd.xhscdn.com/stream/abc.mp4"]}}}'
        self.assertIn("sns-video-bd.xhscdn.com", XiaohongshuDownloader._grep_video_url(text1))

        # 图文 CDN
        text2 = 'src="https://sns-img-bd.xhscdn.com/abc.jpg"'
        self.assertIn("sns-img-bd.xhscdn.com", XiaohongshuDownloader._grep_video_url(text2))

        # 无关内容
        self.assertIsNone(XiaohongshuDownloader._grep_video_url("plain text without any url"))

    def test_resolve_short_url(self):
        from video2text.downloaders.xiaohongshu import XiaohongshuDownloader

        d = XiaohongshuDownloader()
        # 短链接解析失败时返回 None
        with patch("requests.head", side_effect=Exception("network")):
            self.assertIsNone(d._resolve_short_url("https://xhslink.com/a/abc"))


class TestDownloadersPackage(unittest.TestCase):
    """downloaders 包导出"""

    def test_all_exports(self):
        from video2text import downloaders

        for name in (
            "Downloader",
            "YtDlpDownloader",
            "DouyinDownloader",
            "YouTubeDownloader",
            "XiaohongshuDownloader",
        ):
            self.assertIn(name, downloaders.__all__)
            self.assertTrue(hasattr(downloaders, name))


class TestSourceRefKind(unittest.TestCase):
    """SourceRef.kind 字符串约定"""

    def test_kind_constants_in_docstring(self):
        # 注释里的 kind 列表必须包含新加的 youtube/xiaohongshu
        from video2text import models

        src = Path(models.__file__).read_text(encoding="utf-8")
        self.assertIn("youtube", src)
        self.assertIn("xiaohongshu", src)


if __name__ == "__main__":
    unittest.main()
