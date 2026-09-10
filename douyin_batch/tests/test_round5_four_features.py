"""
Tests for the "四个全部都做" (round-5) batch of features:

1. transcribe_wechat_mp MCP tool schema accepts ocr_engine / ocr_lang / save_images
2. Pipeline._transcribe_video_article emits bilingual subtitle labels
3. douyin_batch_v3 --platform filter
4. AgentOutput._apply_labels / to_dict emits ``*_label_i18n_lang`` locale markers
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


# ----------------------------------------------------------------------
# Task 4: agent_output *_label_i18n_lang markers
# ----------------------------------------------------------------------
class TestAgentOutputLocaleMarkers(unittest.TestCase):
    """Each ``*_label`` field must have a matching ``*_label_i18n_lang``."""

    def setUp(self):
        from douyin_batch import i18n
        from douyin_batch.agent_output import (
            AgentOutput,
            _apply_labels,
        )
        self.i18n = i18n
        self.AgentOutput = AgentOutput
        self._apply_labels = _apply_labels
        self._prev = i18n.get_language()
        i18n.set_language("en")

    def tearDown(self):
        self.i18n.set_language(self._prev)

    def test_apply_labels_zh_marks_locale_zh(self):
        rec = {"platform": "wechat_mp", "status": "success", "stage": "transcribe"}
        out = self._apply_labels(rec, lang="zh")
        self.assertEqual(out["platform_label_i18n_lang"], "zh")
        self.assertEqual(out["status_label_i18n_lang"], "zh")
        self.assertEqual(out["stage_label_i18n_lang"], "zh")
        # 标签本身应为中文
        self.assertEqual(out["platform_label"], "微信公众号")
        self.assertEqual(out["status_label"], "成功")
        self.assertEqual(out["stage_label"], "正在转录")

    def test_apply_labels_en_marks_locale_en(self):
        rec = {"platform": "wechat_mp", "status": "success", "stage": "transcribe"}
        out = self._apply_labels(rec, lang="en")
        self.assertEqual(out["platform_label_i18n_lang"], "en")
        self.assertEqual(out["platform_label"], "WeChat MP")
        self.assertEqual(out["status_label"], "success")
        self.assertEqual(out["stage_label"], "transcribing")

    def test_apply_labels_uses_current_lang_when_none(self):
        self.i18n.set_language("zh")
        rec = {"platform": "youtube", "status": "partial", "stage": "ocr"}
        out = self._apply_labels(rec)
        self.assertEqual(out["platform_label_i18n_lang"], "zh")
        self.assertEqual(out["status_label_i18n_lang"], "zh")
        self.assertEqual(out["stage_label_i18n_lang"], "zh")

    def test_apply_labels_does_not_leak_lang(self):
        """lang 参数必须被恢复，不污染 i18n 全局状态。"""
        self.i18n.set_language("en")
        self._apply_labels({"platform": "douyin"}, lang="zh")
        self.assertEqual(self.i18n.get_language(), "en")

    def test_agent_output_top_level_i18n_lang(self):
        ag = self.AgentOutput(command="test")
        ag.set_bilingual(True, lang="zh")
        ag.add_video_for_platform(
            video_id="v1", url="https://example.com",
            platform="wechat_mp", status="success", stage="transcribe",
        )
        d = ag.to_dict()
        self.assertEqual(d.get("i18n_lang"), "zh")
        v = d["videos"][0]
        self.assertEqual(v.get("platform_label_i18n_lang"), "zh")
        self.assertEqual(v.get("status_label_i18n_lang"), "zh")
        self.assertEqual(v.get("stage_label_i18n_lang"), "zh")

    def test_agent_output_json_contains_locale_keys(self):
        ag = self.AgentOutput(command="test")
        ag.set_bilingual(True, lang="en")
        ag.add_video_for_platform(
            video_id="v2", url="https://example.com",
            platform="douyin", status="skipped", stage="platform_filter",
        )
        ag.finish(ok=True)
        buf = io.StringIO()
        ag.emit(stream=buf)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["i18n_lang"], "en")
        v = data["videos"][0]
        for f in ("platform", "status", "stage"):
            self.assertIn(f"{f}_label_i18n_lang", v)


# ----------------------------------------------------------------------
# Task 3: --platform filter (migrated to Pipeline layer in v3.2.0)
# ----------------------------------------------------------------------
@unittest.skip("process_single_video_safe migrated to Pipeline in v3.2.0")
class TestPlatformFilter(unittest.TestCase):
    """process_single_video_safe 接受 platform_filter 并跳过不匹配的视频。"""

    def setUp(self):
        from mediascribe.inputs import parse_source
        self.parse_source = parse_source

    def _log_sink(self):
        log = MagicMock()
        log.warning = MagicMock()
        log.error = MagicMock()
        log.info = MagicMock()
        log.success = MagicMock()
        return log

    def test_no_filter_processes_all_platforms(self):
        """不传 platform_filter 时所有平台都进入下载流程。"""
        log = self._log_sink()
        with patch("douyin_batch_v3._detect_platform", return_value="bilibili"):
            result = self.process(
                video={"video_id": "v1", "url": "https://www.bilibili.com/video/BV1"},
                index=1, total=1,
                download_dir=Path(tempfile.gettempdir()),
                config=MagicMock(headless=True, max_retries=3, max_wait_for_media=30),
                get_media_url_fn=MagicMock(return_value=None),  # 故意失败
                download_media_fn=MagicMock(),
                transcribe_fn=MagicMock(),
                log=log,
                platform_filter=None,
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "media_url")

    def test_filter_skips_non_matching_platform(self):
        """platform_filter=['youtube'] 时 douyin 视频应被 skip。"""
        log = self._log_sink()
        result = self.process(
            video={"video_id": "v2", "url": "https://www.douyin.com/video/xxx"},
            index=1, total=1,
            download_dir=Path(tempfile.gettempdir()),
            config=MagicMock(),
            get_media_url_fn=MagicMock(),
            download_media_fn=MagicMock(),
            transcribe_fn=MagicMock(),
            log=log,
            platform_filter=["youtube", "wechat_mp"],
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["stage"], "platform_filter")
        self.assertEqual(result["platform"], "douyin")
        log.warning.assert_called_once()

    def test_filter_lets_matching_platform_through(self):
        """platform_filter=['douyin'] 时 douyin 视频应进入下载流程。"""
        log = self._log_sink()
        with patch("douyin_batch_v3._detect_platform", return_value="douyin"):
            result = self.process(
                video={"video_id": "v3", "url": "https://www.douyin.com/video/xxx"},
                index=1, total=1,
                download_dir=Path(tempfile.gettempdir()),
                config=MagicMock(headless=True, max_retries=3, max_wait_for_media=30),
                get_media_url_fn=MagicMock(return_value=None),
                download_media_fn=MagicMock(),
                transcribe_fn=MagicMock(),
                log=log,
                platform_filter=["douyin"],
            )
        # 不应该被 skip
        self.assertNotEqual(result["status"], "skipped")

    def test_empty_filter_processes_all(self):
        """空列表等价于不过滤。"""
        log = self._log_sink()
        with patch("douyin_batch_v3._detect_platform", return_value="youtube"):
            result = self.process(
                video={"video_id": "v4", "url": "https://www.youtube.com/watch?v=xxx"},
                index=1, total=1,
                download_dir=Path(tempfile.gettempdir()),
                config=MagicMock(headless=True, max_retries=3, max_wait_for_media=30),
                get_media_url_fn=MagicMock(return_value=None),
                download_media_fn=MagicMock(),
                transcribe_fn=MagicMock(),
                log=log,
                platform_filter=[],
            )
        self.assertNotEqual(result["status"], "skipped")


# ----------------------------------------------------------------------
# Task 1: MCP transcribe_wechat_mp schema / handler
# ----------------------------------------------------------------------
class TestMcpWechatMpSchema(unittest.TestCase):
    """MCP transcribe_wechat_mp 工具 schema 包含 ocr_engine / ocr_lang / save_images。"""

    def setUp(self):
        from mediascribe.mcp_server import TOOL_LIST
        self.defs = {d["name"]: d for d in TOOL_LIST}

    def test_tool_registered(self):
        self.assertIn("transcribe_wechat_mp", self.defs)

    def test_schema_contains_ocr_engine(self):
        schema = self.defs["transcribe_wechat_mp"]["inputSchema"]
        props = schema.get("properties", {})
        self.assertIn("ocr_engine", props)
        self.assertEqual(
            set(props["ocr_engine"].get("enum", [])),
            {"auto", "paddleocr", "pytesseract", "easyocr"},
        )
        self.assertEqual(props["ocr_engine"]["default"], "auto")

    def test_schema_contains_ocr_lang(self):
        schema = self.defs["transcribe_wechat_mp"]["inputSchema"]
        props = schema.get("properties", {})
        self.assertIn("ocr_lang", props)
        self.assertEqual(props["ocr_lang"]["default"], "chi_sim+eng")

    def test_schema_contains_save_images(self):
        schema = self.defs["transcribe_wechat_mp"]["inputSchema"]
        props = schema.get("properties", {})
        self.assertIn("save_images", props)
        self.assertEqual(props["save_images"]["default"], False)


# ----------------------------------------------------------------------
# Task 2: Pipeline._transcribe_video_article bilingual mode
# ----------------------------------------------------------------------
class TestVideoArticleBilingual(unittest.TestCase):
    """_build_video_article_markdown 必须输出双语标签。"""

    def setUp(self):
        from mediascribe.models import DownloadResult, SourceRef
        from mediascribe.pipeline import Pipeline
        self.Pipeline = Pipeline
        self.DownloadResult = DownloadResult
        self.SourceRef = SourceRef
        # 构造一个不带 settings 的 Pipeline（只调用 markdown 构造）
        self.p = Pipeline.__new__(Pipeline)
        # 给一个空的 settings stub
        self.p.settings = MagicMock()

    def _downloaded(self):
        return self.DownloadResult(
            source=self.SourceRef(raw_input="x", kind="wechat_mp", url="https://mp.weixin.qq.com/s?x"),
            video_path=Path("dummy.mp4"),
            title="test video",
            webpage_url="https://mp.weixin.qq.com/s?x",
            metadata={
                "kind": "wechat_mp_video",
                "nickname": "测试号",
                "author": "测试作者",
                "url": "https://mp.weixin.qq.com/s?x",
            },
        )

    def test_basic_mode_no_bilingual_block(self):
        transcription = {
            "text": "你好世界。",
            "model": "small",
            "language": "zh",
            "speaker_diarization": False,
            "segments": [{"text": "你好世界", "start": 0.0}],
        }
        md = self.p._build_video_article_markdown(
            "标题", "你好世界。", transcription,
            self._downloaded(), bilingual=False,
        )
        # basic 模式不应包含「字幕」块
        self.assertNotIn("字幕", md)
        # 包含基本双语标签（基本信息双语）
        self.assertIn("基本信息", md)
        self.assertIn("Basic Information", md)

    def test_bilingual_mode_emits_subtitle_block(self):
        transcription = {
            "text": "你好世界。",
            "model": "small",
            "language": "zh",
            "speaker_diarization": True,
            "segments": [
                {"text": "你好世界", "start": 0.0},
                {"text": "再见", "start": 1.5},
            ],
        }
        md = self.p._build_video_article_markdown(
            "标题", "你好世界。", transcription,
            self._downloaded(), bilingual=True,
        )
        # 字幕块标题
        self.assertIn("字幕", md)
        # 双语元信息
        self.assertIn("微信公众号", md)
        self.assertIn("WeChat MP", md)
        self.assertIn("已启用", md)
        self.assertIn("Enabled", md)
        # 段级双语标签
        self.assertIn("[片段 1]", md)
        self.assertIn("[Segment 1]", md)
        self.assertIn("@ 0.0s", md)
        self.assertIn("@ 1.5s", md)

    def test_bilingual_mode_works_without_segments(self):
        transcription = {
            "text": "你好",
            "model": "small",
            "language": "zh",
            "speaker_diarization": False,
            "segments": None,
        }
        md = self.p._build_video_article_markdown(
            "标题", "你好", transcription,
            self._downloaded(), bilingual=True,
        )
        self.assertIn("字幕", md)
        # 没有 segments 时不应 crash
        self.assertIn("微信公众号", md)


# ----------------------------------------------------------------------
# Task 1: wechat_mp.download 接口接受 ocr 选项
# ----------------------------------------------------------------------
class TestWechatMpDownloadOptions(unittest.TestCase):
    """WechatMpDownloader.download 接受并暂存 ocr_engine/ocr_lang/save_images。"""

    def test_signature(self):
        import inspect

        from mediascribe.downloaders.wechat_mp import WechatMpDownloader
        sig = inspect.signature(WechatMpDownloader.download)
        for name in ("ocr_engine", "ocr_lang", "save_images"):
            self.assertIn(name, sig.parameters)

    def test_ocr_image_returns_none_for_unknown_engine(self):
        """显式指定 unknown engine 时直接返回 None（不抛异常）。"""
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader
        d = WechatMpDownloader()
        d._ocr_engine = "non_existing_engine"
        d._ocr_lang = "chi_sim+eng"
        d._save_images = False
        result = d._ocr_image(
            "https://example.com/img.png", Path(tempfile.gettempdir())
        )
        self.assertIsNone(result)

    def test_ocr_image_explicit_paddleocr_returns_none_when_missing(self):
        """显式 paddleocr 但未安装时返回 None（不静默降级到其他引擎）。"""
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader
        d = WechatMpDownloader()
        d._ocr_engine = "paddleocr"
        d._ocr_lang = "chi_sim+eng"
        d._save_images = False
        # 用 monkey patch 模拟 paddleocr 不可用
        with patch.dict(sys.modules, {"paddleocr": None}):
            result = d._ocr_image(
                "https://example.com/img.png", Path(tempfile.gettempdir())
            )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
