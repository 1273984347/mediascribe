"""
测试：
- agent_output bilingual 模式（双语标签）
- wechat_mp 图片提取 + OCR（mock 引擎）
- video2text.__main__ 的 --wechat-cookies / --wechat-cookie-file CLI flag
- douyin_batch_v3._detect_platform / process_single_video_safe.platform
"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


class TestAgentOutputBilingual(unittest.TestCase):
    """AgentOutput bilingual 模式"""

    def setUp(self):
        from douyin_batch import i18n
        from douyin_batch.agent_output import AgentOutput

        self._prev = i18n.get_language()
        i18n.set_language("en")
        self.out = AgentOutput(command="test")

    def tearDown(self):
        from douyin_batch import i18n

        i18n.set_language(self._prev)

    def test_bilingual_off_by_default(self):
        self.out.add_video_for_platform(
            video_id="v1",
            url="https://x",
            platform="youtube",
            status="success",
            stage="download",
        )
        d = self.out.to_dict()
        v = d["videos"][0]
        self.assertNotIn("platform_label", v)
        self.assertNotIn("status_label", v)
        self.assertNotIn("stage_label", v)

    def test_bilingual_en(self):
        self.out.set_bilingual(True, lang="en")
        self.out.add_video_for_platform(
            video_id="v1",
            url="https://x",
            platform="youtube",
            status="success",
            stage="download",
        )
        d = self.out.to_dict()
        v = d["videos"][0]
        # 保留英文字段
        self.assertEqual(v["platform"], "youtube")
        self.assertEqual(v["status"], "success")
        # 追加人类可读标签
        self.assertEqual(v["platform_label"], "YouTube")
        self.assertEqual(v["status_label"], "success")
        self.assertEqual(v["stage_label"], "downloading")

    def test_bilingual_zh(self):
        self.out.set_bilingual(True, lang="zh")
        self.out.add_video_for_platform(
            video_id="v2",
            url="https://mp.weixin.qq.com/s?x",
            platform="wechat_mp",
            status="success",
            stage="text_extract",
        )
        d = self.out.to_dict()
        v = d["videos"][0]
        self.assertEqual(v["platform_label"], "微信公众号")
        self.assertEqual(v["status_label"], "成功")
        self.assertEqual(v["stage_label"], "正在提取文本")

    def test_bilingual_partial_status(self):
        self.out.set_bilingual(True, lang="zh")
        self.out.add_video_for_platform(
            video_id="v3",
            url="https://x",
            platform="wechat_mp",
            status="partial",
        )
        d = self.out.to_dict()
        self.assertEqual(d["videos"][0]["status_label"], "部分完成")

    def test_bilingual_stage_ocr(self):
        from douyin_batch.agent_output import PLATFORM_WECHAT_MP, STAGE_OCR

        self.out.set_bilingual(True, lang="en")
        self.out.add_video_for_platform(
            video_id="v4",
            url="https://x",
            platform=PLATFORM_WECHAT_MP,
            status="success",
            stage=STAGE_OCR,
        )
        d = self.out.to_dict()
        self.assertEqual(d["videos"][0]["stage_label"], "OCR-ing images")

    def test_bilingual_emit_json(self):
        """emit() 应该直接输出含 *_label 字段的合法 JSON。"""
        self.out.set_bilingual(True, lang="zh")
        self.out.add_video_for_platform(
            video_id="v5",
            url="https://x",
            platform="xiaohongshu",
            status="success",
            stage="media_url",
        )
        buf = io.StringIO()
        self.out.emit(stream=buf)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(
            parsed["videos"][0]["platform_label"],
            "小红书",
        )

    def test_bilingual_preserves_i18n_state(self):
        """bilingual 切换不应污染 i18n 全局状态。"""
        from douyin_batch import i18n

        i18n.set_language("en")
        self.out.set_bilingual(True, lang="zh")
        self.out.add_video_for_platform(
            video_id="v6", url="x", platform="youtube", status="success"
        )
        # 触发 to_dict() 切换到 zh
        self.out.to_dict()
        # 外部 i18n 仍应是 en
        self.assertEqual(i18n.get_language(), "en")


class TestDetectPlatformInV3(unittest.TestCase):
    """video2text.inputs.parse_source — platform detection."""

    def test_douyin(self):
        from video2text.inputs import parse_source
        src = parse_source("https://www.douyin.com/video/abc")
        self.assertEqual(src.kind, "douyin")

    def test_bilibili(self):
        from video2text.inputs import parse_source
        src = parse_source("https://www.bilibili.com/video/BV1xx411c7mD")
        self.assertEqual(src.kind, "bilibili")

    def test_youtube(self):
        from video2text.inputs import parse_source
        src = parse_source("https://youtu.be/abc")
        self.assertEqual(src.kind, "youtube")

    def test_xiaohongshu(self):
        from video2text.inputs import parse_source
        src = parse_source("https://www.xiaohongshu.com/explore/abc")
        self.assertEqual(src.kind, "xiaohongshu")

    def test_wechat_mp(self):
        from video2text.inputs import parse_source
        src = parse_source("https://mp.weixin.qq.com/s?__biz=MzA&mid=1")
        self.assertEqual(src.kind, "wechat_mp")

    def test_tiktok(self):
        from video2text.inputs import parse_source
        src = parse_source("https://www.tiktok.com/@x/video/1")
        self.assertEqual(src.kind, "tiktok")

    def test_unknown(self):
        from video2text.inputs import parse_source
        src = parse_source("https://example.com/x")
        self.assertEqual(src.kind, "video")  # generic URL → video

    def test_local_path(self):
        from video2text.inputs import parse_source
        # 不存在的路径 → kind="video" (fallback)
        src = parse_source("Z:/path/video.mp4")
        self.assertIn(src.kind, ("video", "audio"))
        # 不存在的相对路径也走 fallback
        src2 = parse_source("./relative.mp4")
        self.assertIn(src2.kind, ("video", "audio"))


class TestWechatMpImageExtract(unittest.TestCase):
    """WechatMpDownloader 图片提取（无网络）"""

    def setUp(self):
        from video2text.downloaders.wechat_mp import WechatMpDownloader

        self.d = WechatMpDownloader()

    def test_extract_no_images(self):
        self.assertEqual(
            self.d._extract_image_urls(
                "<html><body><div id='js_content'>纯文本文章</div></body></html>"
            ),
            [],
        )

    def test_extract_data_src_priority(self):
        html = """
        <div id="js_content">
            <img data-src="https://mmbiz.qpic.cn/mmbiz_png/abc/0.png">
            <img src="https://example.com/img1.jpg">
        </div>
        """
        urls = self.d._extract_image_urls(html)
        self.assertEqual(len(urls), 2)
        self.assertIn("mmbiz.qpic.cn", urls[0])
        self.assertIn("example.com", urls[1])

    def test_dedup(self):
        html = """
        <div id="js_content">
            <img data-src="https://x.com/a.jpg">
            <img src="https://x.com/a.jpg">
        </div>
        """
        urls = self.d._extract_image_urls(html)
        self.assertEqual(len(urls), 1)

    def test_protocol_relative_normalized(self):
        html = """
        <div id="js_content">
            <img data-src="//mmbiz.qpic.cn/abc.png">
        </div>
        """
        urls = self.d._extract_image_urls(html)
        self.assertEqual(urls[0], "https://mmbiz.qpic.cn/abc.png")

    def test_data_uri_skipped(self):
        html = """
        <div id="js_content">
            <img src="data:image/png;base64,abc">
            <img data-src="https://x.com/real.png">
        </div>
        """
        urls = self.d._extract_image_urls(html)
        self.assertEqual(len(urls), 1)
        self.assertIn("real.png", urls[0])


class TestWechatMpOcrGracefulDegradation(unittest.TestCase):
    """_ocr_image / _ocr_images 在没有 OCR 引擎时安全降级"""

    def setUp(self):
        from video2text.downloaders.wechat_mp import WechatMpDownloader

        self.d = WechatMpDownloader()

    def test_ocr_image_no_engine_returns_none(self):
        """所有 OCR 引擎缺失 → 返回 None，不抛异常。"""
        with patch.dict(sys.modules, {"paddleocr": None, "pytesseract": None, "easyocr": None}):
            # 让 requests.get 返回一个伪 PNG
            fake_resp = MagicMock()
            fake_resp.content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
            fake_resp.raise_for_status = MagicMock()
            with patch.object(
                self.d, "_ocr_image",
                wraps=self.d._ocr_image,
            ) as m:
                # requests.get 直接 mock 掉
                with patch("requests.get", return_value=fake_resp):
                    result = m("https://x.com/img.png", Path("."))
            # 所有引擎缺失 → 返回 None
            self.assertIsNone(result)

    def test_ocr_images_summary(self):
        """_ocr_images 返回 (texts, success, total)。"""
        with patch.object(self.d, "_ocr_image", return_value="hello"):
            texts, success, total = self.d._ocr_images(
                ["https://x.com/1.png", "https://x.com/2.png"], Path(".")
            )
            self.assertEqual(success, 2)
            self.assertEqual(total, 2)
            self.assertEqual(texts, ["hello", "hello"])

    def test_ocr_images_partial(self):
        """部分图片识别成功。"""
        with patch.object(
            self.d,
            "_ocr_image",
            side_effect=["first", None, "third"],
        ):
            texts, success, total = self.d._ocr_images(
                ["a", "b", "c"], Path(".")
            )
            self.assertEqual(success, 2)
            self.assertEqual(total, 3)
            # The internal executor is multi-threaded; order is not
            # guaranteed, so assert on the multiset of results.
            self.assertCountEqual(texts, ["first", "third"])


class TestWechatMpDownloadWithOcr(unittest.TestCase):
    """download() 在图片文章上的端到端流程（mock OCR）"""

    def test_image_article_no_ocr_engines(self):
        """所有 OCR 引擎缺失 → status=partial, ocr_success=0。"""
        from video2text.config import Settings
        from video2text.downloaders.wechat_mp import WechatMpDownloader
        from video2text.models import SourceRef

        d = WechatMpDownloader()
        s = Settings()

        html = """
        <html><head><meta property="og:title" content="图片文章"></head><body>
        <a id="js_name">测试号</a>
        <div id="js_content">
            <p>图文说明</p>
            <img data-src="https://mmbiz.qpic.cn/a.png">
        </div>
        </body></html>
        """
        with patch.object(d, "_fetch_html", return_value=html), \
             patch.dict(sys.modules, {"paddleocr": None, "pytesseract": None, "easyocr": None}), \
             patch.object(d, "_ocr_images", return_value=([], 0, 1)), \
             patch.object(
                 d, "_write_text_stub",
                 return_value=s.audio_dir / "stub.txt",
             ):
            (s.audio_dir / "stub.txt").parent.mkdir(parents=True, exist_ok=True)
            result = d.download(
                SourceRef(
                    raw_input="x",
                    kind="wechat_mp",
                    url="https://mp.weixin.qq.com/s?abc",
                ),
                s,
            )

        meta = result.metadata
        self.assertEqual(meta["wechat_mp_status"], "partial")
        self.assertEqual(meta["ocr_total"], 1)
        self.assertEqual(meta["ocr_success"], 0)
        self.assertEqual(len(meta["image_urls"]), 1)

    def test_image_article_all_ocr_success(self):
        """OCR 全部成功 → status=success。"""
        from video2text.config import Settings
        from video2text.downloaders.wechat_mp import WechatMpDownloader
        from video2text.models import SourceRef

        d = WechatMpDownloader()
        s = Settings()

        html = """
        <html><head><meta property="og:title" content="图片文章"></head><body>
        <div id="js_content">
            <img data-src="https://mmbiz.qpic.cn/a.png">
            <img data-src="https://mmbiz.qpic.cn/b.png">
        </div>
        </body></html>
        """
        with patch.object(d, "_fetch_html", return_value=html), \
             patch.object(
                 d, "_ocr_images",
                 return_value=(["text1", "text2"], 2, 2),
             ), \
             patch.object(
                 d, "_write_text_stub",
                 return_value=s.audio_dir / "stub.txt",
             ):
            (s.audio_dir / "stub.txt").parent.mkdir(parents=True, exist_ok=True)
            result = d.download(
                SourceRef(
                    raw_input="x",
                    kind="wechat_mp",
                    url="https://mp.weixin.qq.com/s?abc",
                ),
                s,
            )

        meta = result.metadata
        self.assertEqual(meta["wechat_mp_status"], "success")
        self.assertEqual(meta["ocr_success"], 2)
        self.assertEqual(meta["ocr_total"], 2)
        self.assertEqual(meta["ocr_texts"], ["text1", "text2"])

    def test_text_only_article_unchanged(self):
        """纯文本文章保持 success，不触发 OCR。"""
        from video2text.config import Settings
        from video2text.downloaders.wechat_mp import WechatMpDownloader
        from video2text.models import SourceRef

        d = WechatMpDownloader()
        s = Settings()

        html = """
        <html><head><meta property="og:title" content="纯文本"></head><body>
        <div id="js_content"><p>纯文本文章</p></div>
        </body></html>
        """
        with patch.object(d, "_fetch_html", return_value=html), \
             patch.object(
                 d, "_write_text_stub",
                 return_value=s.audio_dir / "stub.txt",
             ):
            (s.audio_dir / "stub.txt").parent.mkdir(parents=True, exist_ok=True)
            result = d.download(
                SourceRef(
                    raw_input="x",
                    kind="wechat_mp",
                    url="https://mp.weixin.qq.com/s?abc",
                ),
                s,
            )
        self.assertEqual(result.metadata["wechat_mp_status"], "success")
        self.assertEqual(result.metadata["ocr_total"], 0)
        # OCR 未调用
        # （_ocr_images 不应被触发）


class TestVideo2TextCliWechatFlags(unittest.TestCase):
    """video2text.__main__ 解析 --wechat-cookies / --wechat-cookie-file"""

    def test_argparser_accepts_wechat_cookies(self):
        from video2text.__main__ import main

        # 避免 main 真实跑：mock 掉 Pipeline
        with patch("video2text.__main__.Pipeline") as MockPipeline, patch("sys.argv", [
            "video2text", "transcribe",
            "https://mp.weixin.qq.com/s?__biz=MzA&mid=1",
            "--wechat-cookies", "skey=abc,uin=123",
            "--wechat-cookie-file", "Z:/cookies.txt",
        ]):
            try:
                main()
            except SystemExit:
                pass

        call = MockPipeline.call_args
        settings = call.kwargs.get("settings") or call.args[0]
        self.assertEqual(settings.wechat_cookies, {"skey": "abc", "uin": "123"})
        # Path 在 Windows 上会被 normalize（"Z:/cookies.txt" → "Z:\\cookies.txt"）
        self.assertEqual(
            str(settings.wechat_cookies_file).replace("\\", "/").lower(),
            "z:/cookies.txt",
        )

    def test_argparser_no_cookies(self):
        from video2text.__main__ import main

        with patch("video2text.__main__.Pipeline") as MockPipeline, patch("sys.argv", [
            "video2text", "transcribe", "video.mp4",
        ]):
            try:
                main()
            except SystemExit:
                pass
        call = MockPipeline.call_args
        settings = call.kwargs.get("settings") or call.args[0]
        self.assertEqual(settings.wechat_cookies, {})
        self.assertIsNone(settings.wechat_cookies_file)


class TestPipelineWechatOcrMarkdown(unittest.TestCase):
    """Pipeline._handle_wechat_mp 把 OCR 内容追加到 markdown"""

    def test_ocr_section_in_markdown(self):

        from video2text.config import Settings
        from video2text.models import DownloadResult, SourceRef
        from video2text.pipeline import Pipeline

        with tempfile.TemporaryDirectory() as tmp:
            s = Settings()
            s.workspace_root = Path(tmp)
            s.transcripts_dir = Path(tmp) / "transcripts"
            s.metadata_dir = Path(tmp) / "metadata"
            s.audio_dir = Path(tmp) / "audio"
            s.downloads_dir = Path(tmp) / "downloads"
            for d in (
                s.transcripts_dir, s.metadata_dir, s.audio_dir, s.downloads_dir
            ):
                d.mkdir(parents=True, exist_ok=True)

            stub = s.audio_dir / "stub.txt"
            stub.write_text("正文段落一。", encoding="utf-8")
            source = SourceRef(
                raw_input="x",
                kind="wechat_mp",
                url="https://mp.weixin.qq.com/s?x",
            )
            downloaded = DownloadResult(
                source=source,
                video_path=stub,
                title="图片文章",
                webpage_url=source.url,
                metadata={
                    "kind": "wechat_mp_article",
                    "wechat_mp_text": True,
                    "wechat_mp_status": "partial",
                    "text": "正文段落一。",
                    "title": "图片文章",
                    "image_urls": ["https://x.com/a.png"],
                    "ocr_texts": ["图片中识别到的文字一", "图片中识别到的文字二"],
                    "ocr_success": 2,
                    "ocr_total": 2,
                },
            )
            p = Pipeline(settings=s, transcriber=MagicMock())
            p._get_downloader = MagicMock(
                return_value=MagicMock(download=MagicMock(return_value=downloaded))
            )
            result = p._handle_wechat_mp(source)

            content = result.transcript_path.read_text(encoding="utf-8")
            self.assertIn("图片 OCR 文本", content)
            self.assertIn("图片中识别到的文字一", content)
            self.assertIn("图片中识别到的文字二", content)
            # OCR 状态写入 markdown
            self.assertIn("**图片 OCR**: 2/2", content)
            self.assertIn("- **状态**: partial", content)


if __name__ == "__main__":
    unittest.main()
