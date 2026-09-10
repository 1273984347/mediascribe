"""
测试 pipeline 下载器路由（fallback 链）+ 微信公众号文本/视频分流

覆盖：
- Pipeline._get_downloader 对每个 kind 的选择
- 路由失败时的 fallback 行为
- WechatMpDownloader 的解析/下载
- WechatMpDownloader 与 pipeline 的整合（mocked）
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


def _make_source(kind, url=None, raw="x"):
    """构造一个 SourceRef，跳过严格的路径检查。"""
    from mediascribe.models import SourceRef

    return SourceRef(raw_input=raw, kind=kind, url=url)


class TestPipelineRouter(unittest.TestCase):
    """Pipeline._get_downloader fallback 链"""

    def setUp(self):
        # 构造一个最小可用的 Pipeline，绕开 Settings 构造副作用
        from mediascribe.config import Settings
        from mediascribe.pipeline import Pipeline

        self.settings = Settings()
        self.pipeline = Pipeline(settings=self.settings, transcriber=MagicMock())

    def test_douyin_uses_douyin_downloader(self):
        src = _make_source("douyin", url="https://www.douyin.com/video/1")
        d = self.pipeline._get_downloader(src)
        self.assertEqual(d.name, "douyin")

    def test_xiaohongshu_uses_xhs_downloader(self):
        src = _make_source("xiaohongshu", url="https://www.xiaohongshu.com/explore/abc")
        d = self.pipeline._get_downloader(src)
        self.assertEqual(d.name, "xiaohongshu")

    def test_youtube_uses_youtube_downloader(self):
        src = _make_source("youtube", url="https://www.youtube.com/watch?v=abc")
        d = self.pipeline._get_downloader(src)
        self.assertEqual(d.name, "youtube")

    def test_wechat_mp_uses_wechat_downloader(self):
        src = _make_source("wechat_mp", url="https://mp.weixin.qq.com/s?__biz=MzA&mid=1&idx=1")
        d = self.pipeline._get_downloader(src)
        self.assertEqual(d.name, "wechat_mp")

    def test_bilibili_falls_back_to_ytdlp(self):
        src = _make_source("bilibili", url="https://www.bilibili.com/video/BV1xx")
        d = self.pipeline._get_downloader(src)
        # 显式下载器未注册 bilibili → 落到 YtDlpDownloader
        self.assertEqual(d.name, "yt-dlp")

    def test_unknown_kind_falls_back_to_ytdlp(self):
        src = _make_source("unknown_platform", url="https://example.com/v")
        d = self.pipeline._get_downloader(src)
        self.assertEqual(d.name, "yt-dlp")

    def test_explicit_downloader_overrides_routing(self):
        from mediascribe.downloaders.ytdlp import YtDlpDownloader

        explicit = YtDlpDownloader()
        p = self.pipeline
        p.downloader = explicit
        # 即便是 douyin 源，也使用显式下载器
        src = _make_source("douyin", url="https://www.douyin.com/video/1")
        self.assertIs(p._get_downloader(src), explicit)

    def test_fallback_when_constructor_raises(self):
        """XhsDownloader 构造异常时回退到 YtDlpDownloader。

        P2-14 后 fallback 链唯一实现在 pipeline_stages._smart_pick_downloader
        (Pipeline._get_downloader 委托过去),patch 点随之迁移。
        """

        src = _make_source("xiaohongshu", url="https://www.xiaohongshu.com/explore/abc")
        with patch(
            "mediascribe.pipeline_stages.XiaohongshuDownloader",
            side_effect=RuntimeError("simulated init failure"),
        ):
            d = self.pipeline._get_downloader(src)
            self.assertEqual(d.name, "yt-dlp")


class TestWechatMpDownloader(unittest.TestCase):
    """WechatMpDownloader 单元测试（mocked 网络）"""

    def setUp(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        self.d = WechatMpDownloader()

    def test_supports_kind(self):
        from mediascribe.models import SourceRef

        self.assertTrue(
            self.d.supports(
                SourceRef(raw_input="x", kind="wechat_mp", url="https://mp.weixin.qq.com/s?abc")
            )
        )

    def test_supports_url(self):
        from mediascribe.models import SourceRef

        self.assertTrue(
            self.d.supports(
                SourceRef(
                    raw_input="x",
                    kind="video",
                    url="https://mp.weixin.qq.com/s?__biz=MzA&mid=1&idx=1",
                )
            )
        )

    def test_does_not_support_other(self):
        from mediascribe.models import SourceRef

        self.assertFalse(
            self.d.supports(
                SourceRef(
                    raw_input="x", kind="bilibili", url="https://www.bilibili.com/video/BV1xx"
                )
            )
        )

    def test_extract_text_from_html(self):
        html = """
        <html><head><meta property="og:title" content="测试标题"></head><body>
        <div id="js_content">
            <p>第一段正文内容。</p>
            <p>第二段也是测试。</p>
            <script>alert(1)</script>
        </div>
        <a id="js_name">测试公众号</a>
        </body></html>
        """
        text = self.d._extract_text(html)
        self.assertIn("第一段正文内容", text)
        self.assertIn("第二段也是测试", text)
        # script 块应被剔除
        self.assertNotIn("alert(1)", text)

    def test_extract_meta(self):
        html = """
        <meta property="og:title" content="文章标题X">
        <meta name="author" content="作者Y">
        <a id="js_name">公众号Z</a>
        """
        meta = self.d._extract_meta(html)
        self.assertEqual(meta.get("title"), "文章标题X")
        self.assertEqual(meta.get("author"), "作者Y")
        self.assertEqual(meta.get("nickname"), "公众号Z")

    def test_extract_video_url_var(self):
        html = '<script>var video_iframe = "https://mp.video.qq.com/shortvideo/abc.mp4";</script>'
        url = self.d._extract_video_url(html)
        self.assertIn("mp.video.qq.com", url)
        self.assertIn(".mp4", url)

    def test_extract_video_url_source_tag(self):
        html = '<video><source src="https://example.com/movie.mp4"></video>'
        url = self.d._extract_video_url(html)
        self.assertEqual(url, "https://example.com/movie.mp4")

    def test_extract_video_url_protocol_relative(self):
        html = '<script>var video_url = "//v.qq.com/abc.mp4";</script>'
        url = self.d._extract_video_url(html)
        self.assertTrue(url.startswith("https://"))
        self.assertIn("v.qq.com", url)

    def test_no_video_url(self):
        html = "<p>无视频</p>"
        self.assertIsNone(self.d._extract_video_url(html))

    def test_article_mode_skips_asr(self):
        """用 mock 网络下载一篇公众号文章，验证返回 metadata 是文本型。"""
        from mediascribe.config import Settings

        html = """
        <html><head><meta property="og:title" content="文章标题">
        <meta name="author" content="Alice"></head>
        <body>
        <a id="js_name">测试号</a>
        <div id="js_content">
            <p>这是测试正文段落一。</p>
            <p>这是测试正文段落二。</p>
        </div>
        </body></html>
        """
        src = _make_source("wechat_mp", url="https://mp.weixin.qq.com/s?__biz=MzA&mid=1&idx=1")
        settings = Settings()

        with patch.object(self.d, "_fetch_html", return_value=html):
            result = self.d.download(src, settings)

        self.assertEqual(result.metadata["kind"], "wechat_mp_article")
        self.assertTrue(result.metadata["wechat_mp_text"])
        self.assertIn("测试正文段落一", result.metadata["text"])
        # 占位文件存在于 audio_dir
        self.assertTrue(result.video_path.exists())


class TestWechatMpPipelineIntegration(unittest.TestCase):
    """Pipeline._handle_wechat_mp 文本文章直接落盘（不调用 ASR）。"""

    def setUp(self):
        from mediascribe.config import Settings
        from mediascribe.pipeline import Pipeline

        self.settings = Settings()
        # 假装一个 transcriber，调用应该不会发生
        self.transcriber = MagicMock()
        self.transcriber.name = "fake_engine"
        self.pipeline = Pipeline(settings=self.settings, transcriber=self.transcriber)

    def test_text_article_writes_markdown_without_asr(self):
        import tempfile

        from mediascribe.models import DownloadResult, SourceRef

        # 让 workspace_root 指向临时目录
        with tempfile.TemporaryDirectory() as tmp:
            self.pipeline.settings.workspace_root = Path(tmp)
            self.pipeline.settings.transcripts_dir = Path(tmp) / "transcripts"
            self.pipeline.settings.metadata_dir = Path(tmp) / "metadata"
            self.pipeline.settings.audio_dir = Path(tmp) / "audio"
            self.pipeline.settings.downloads_dir = Path(tmp) / "downloads"
            for d in (
                self.pipeline.settings.transcripts_dir,
                self.pipeline.settings.metadata_dir,
                self.pipeline.settings.audio_dir,
                self.pipeline.settings.downloads_dir,
            ):
                d.mkdir(parents=True, exist_ok=True)

            # 构造一个伪造的下载结果
            stub = self.pipeline.settings.audio_dir / "stub.txt"
            stub.write_text("这是测试正文。\n\n第二段。", encoding="utf-8")
            source = SourceRef(
                raw_input="https://mp.weixin.qq.com/s?__biz=MzA&mid=1",
                kind="wechat_mp",
                url="https://mp.weixin.qq.com/s?__biz=MzA&mid=1",
            )
            downloaded = DownloadResult(
                source=source,
                video_path=stub,
                title="测试文章标题",
                webpage_url=source.url,
                metadata={
                    "kind": "wechat_mp_article",
                    "wechat_mp_text": True,
                    "text": "这是测试正文。\n\n第二段。",
                    "title": "测试文章标题",
                    "nickname": "测试号",
                    "author": "Alice",
                    "url": source.url,
                },
            )

            # mock 掉下载器：直接返回预构造的 DownloadResult
            fake_downloader = MagicMock()
            fake_downloader.download.return_value = downloaded
            self.pipeline._get_downloader = MagicMock(return_value=fake_downloader)

            # 跑 _handle_wechat_mp
            result = self.pipeline._handle_wechat_mp(source)

            # transcriber 不应该被调用
            self.transcriber.transcribe.assert_not_called()

            # 文本应直接落到 markdown
            self.assertTrue(result.transcript_path.exists())
            content = result.transcript_path.read_text(encoding="utf-8")
            self.assertIn("测试文章标题", content)
            self.assertIn("这是测试正文", content)
            self.assertIn("Alice", content)
            self.assertEqual(result.engine, "wechat_mp_text")

    def test_text_article_empty_text_raises(self):
        import tempfile

        from mediascribe.models import DownloadResult, SourceRef

        with tempfile.TemporaryDirectory() as tmp:
            self.pipeline.settings.workspace_root = Path(tmp)
            self.pipeline.settings.transcripts_dir = Path(tmp) / "transcripts"
            self.pipeline.settings.metadata_dir = Path(tmp) / "metadata"
            self.pipeline.settings.audio_dir = Path(tmp) / "audio"
            self.pipeline.settings.downloads_dir = Path(tmp) / "downloads"
            for d in (
                self.pipeline.settings.transcripts_dir,
                self.pipeline.settings.metadata_dir,
                self.pipeline.settings.audio_dir,
                self.pipeline.settings.downloads_dir,
            ):
                d.mkdir(parents=True, exist_ok=True)

            source = SourceRef(
                raw_input="x", kind="wechat_mp", url="https://mp.weixin.qq.com/s?x"
            )
            stub = self.pipeline.settings.audio_dir / "stub.txt"
            stub.write_text("", encoding="utf-8")
            downloaded = DownloadResult(
                source=source,
                video_path=stub,
                title="空",
                webpage_url=source.url,
                metadata={"kind": "wechat_mp_article", "wechat_mp_text": True, "text": ""},
            )
            with self.assertRaises(RuntimeError):
                self.pipeline._handle_wechat_mp(source)


if __name__ == "__main__":
    unittest.main()
