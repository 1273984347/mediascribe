"""
测试 i18n 平台标签、agent_output 状态/平台/阶段常量、WeChat MP cookies
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


class TestAgentOutputConstants(unittest.TestCase):
    """agent_output 模块暴露的稳定字符串常量"""

    def test_status_constants(self):
        from douyin_batch.agent_output import (
            STATUS_FAILED,
            STATUS_PARTIAL,
            STATUS_SKIPPED,
            STATUS_SUCCESS,
        )

        # 必须是稳定字符串，永远不要本地化
        self.assertEqual(STATUS_SUCCESS, "success")
        self.assertEqual(STATUS_FAILED, "failed")
        self.assertEqual(STATUS_SKIPPED, "skipped")
        self.assertEqual(STATUS_PARTIAL, "partial")

    def test_stage_constants(self):
        from douyin_batch.agent_output import (
            STAGE_DOWNLOAD,
            STAGE_MEDIA_URL,
            STAGE_TEXT_EXTRACT,
            STAGE_TRANSCRIBE,
        )

        self.assertEqual(STAGE_MEDIA_URL, "media_url")
        self.assertEqual(STAGE_DOWNLOAD, "download")
        self.assertEqual(STAGE_TRANSCRIBE, "transcribe")
        self.assertEqual(STAGE_TEXT_EXTRACT, "text_extract")

    def test_platform_constants(self):
        from douyin_batch.agent_output import (
            PLATFORM_BILIBILI,
            PLATFORM_DOUYIN,
            PLATFORM_LOCAL,
            PLATFORM_TIKTOK,
            PLATFORM_UNKNOWN,
            PLATFORM_WECHAT_MP,
            PLATFORM_XIAOHONGSHU,
            PLATFORM_YOUTUBE,
        )

        self.assertEqual(PLATFORM_BILIBILI, "bilibili")
        self.assertEqual(PLATFORM_DOUYIN, "douyin")
        self.assertEqual(PLATFORM_YOUTUBE, "youtube")
        self.assertEqual(PLATFORM_XIAOHONGSHU, "xiaohongshu")
        self.assertEqual(PLATFORM_WECHAT_MP, "wechat_mp")
        self.assertEqual(PLATFORM_TIKTOK, "tiktok")
        self.assertEqual(PLATFORM_LOCAL, "local")
        self.assertEqual(PLATFORM_UNKNOWN, "unknown")


class TestPlatformLabel(unittest.TestCase):
    """platform_label() 国际化标签"""

    def setUp(self):
        from douyin_batch import i18n

        self._prev_lang = i18n.get_language()
        i18n.set_language("en")

    def tearDown(self):
        from douyin_batch import i18n

        i18n.set_language(self._prev_lang)

    def test_youtube_en(self):
        from douyin_batch.agent_output import platform_label

        self.assertEqual(platform_label("youtube"), "YouTube")

    def test_xiaohongshu_en(self):
        from douyin_batch.agent_output import platform_label

        self.assertEqual(platform_label("xiaohongshu"), "Xiaohongshu")

    def test_unknown_kind(self):
        from douyin_batch.agent_output import platform_label

        self.assertEqual(platform_label("nonsense"), "Unknown")

    def test_zh_labels(self):
        from douyin_batch import i18n
        from douyin_batch.agent_output import platform_label

        i18n.set_language("zh")
        self.assertEqual(platform_label("bilibili"), "B站")
        self.assertEqual(platform_label("douyin"), "抖音")
        self.assertEqual(platform_label("wechat_mp"), "微信公众号")
        self.assertEqual(platform_label("video"), "本地文件")


class TestStatusAndStageLabels(unittest.TestCase):
    """status_label() / stage_label()"""

    def setUp(self):
        from douyin_batch import i18n

        self._prev_lang = i18n.get_language()
        i18n.set_language("en")

    def tearDown(self):
        from douyin_batch import i18n

        i18n.set_language(self._prev_lang)

    def test_status_labels_en(self):
        from douyin_batch.agent_output import (
            STATUS_FAILED,
            STATUS_SUCCESS,
            status_label,
        )

        self.assertEqual(status_label(STATUS_SUCCESS), "success")
        self.assertEqual(status_label(STATUS_FAILED), "failed")
        self.assertEqual(status_label("unknown"), "unknown")

    def test_status_labels_zh(self):
        from douyin_batch import i18n
        from douyin_batch.agent_output import status_label

        i18n.set_language("zh")
        self.assertEqual(status_label("success"), "成功")
        self.assertEqual(status_label("failed"), "失败")

    def test_stage_label_none(self):
        from douyin_batch.agent_output import stage_label

        self.assertIsNone(stage_label(None))

    def test_stage_labels_en(self):
        from douyin_batch.agent_output import STAGE_TEXT_EXTRACT, stage_label

        self.assertEqual(stage_label(STAGE_TEXT_EXTRACT), "extracting text")


class TestAgentOutputStatsPartial(unittest.TestCase):
    """stats 字段必须包含 partial 计数"""

    def test_partial_counted(self):
        from douyin_batch.agent_output import STATUS_PARTIAL, STATUS_SUCCESS, AgentOutput

        out = AgentOutput(command="test")
        out.add_video({"video_id": "a", "status": STATUS_SUCCESS})
        out.add_video({"video_id": "b", "status": STATUS_PARTIAL})
        out.add_video({"video_id": "c", "status": STATUS_PARTIAL})
        out.add_video({"video_id": "d", "status": "failed"})
        s = out._stats()
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["success"], 1)
        self.assertEqual(s["partial"], 2)
        self.assertEqual(s["failed"], 1)


class TestAddVideoForPlatform(unittest.TestCase):
    """add_video_for_platform 便捷方法"""

    def test_writes_full_record(self):
        from douyin_batch.agent_output import (
            PLATFORM_WECHAT_MP,
            STAGE_TEXT_EXTRACT,
            STATUS_SUCCESS,
            AgentOutput,
        )

        out = AgentOutput(command="test")
        out.add_video_for_platform(
            video_id="w1",
            url="https://mp.weixin.qq.com/s?x",
            platform=PLATFORM_WECHAT_MP,
            status=STATUS_SUCCESS,
            stage=STAGE_TEXT_EXTRACT,
            transcript="output/transcripts/w1.md",
            audio="output/audio/w1.txt",
        )
        self.assertEqual(len(out._videos), 1)
        v = out._videos[0]
        self.assertEqual(v["platform"], "wechat_mp")
        self.assertEqual(v["status"], "success")
        self.assertEqual(v["stage"], "text_extract")
        self.assertEqual(v["transcript"], "output/transcripts/w1.md")


class TestCookieParsing(unittest.TestCase):
    """config._parse_cookie_string / Settings 注入"""

    def test_netscape_format(self):
        from mediascribe.config import _parse_cookie_string

        text = (
            "# Netscape HTTP Cookie File\n"
            ".qq.com\tTRUE\t/\tFALSE\t0\tskey\t@abc123\n"
            ".qq.com\tTRUE\t/\tFALSE\t0\tuin\t12345\n"
            "single_line=value1\n"
        )
        result = _parse_cookie_string(text)
        self.assertEqual(result["skey"], "@abc123")
        self.assertEqual(result["uin"], "12345")
        self.assertEqual(result["single_line"], "value1")

    def test_json_format(self):
        from mediascribe.config import _parse_cookie_string

        result = _parse_cookie_string('{"a": "1", "b": "2"}')
        self.assertEqual(result, {"a": "1", "b": "2"})

    def test_comments_skipped(self):
        from mediascribe.config import _parse_cookie_string

        text = "// comment\n# another comment\nname=value\n"
        result = _parse_cookie_string(text)
        self.assertEqual(result, {"name": "value"})

    def test_empty(self):
        from mediascribe.config import _parse_cookie_string

        self.assertEqual(_parse_cookie_string(""), {})

    def test_settings_dict_injection(self):
        from mediascribe.config import Settings

        s = Settings(wechat_cookies={"skey": "x", "uin": "1"})
        self.assertEqual(s.wechat_cookies["skey"], "x")

    def test_settings_file_injection(self):
        from mediascribe.config import Settings

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("skey=fromfile\n")
            f.write("uin=42\n")
            tmp_path = f.name
        try:
            s = Settings(wechat_cookies_file=Path(tmp_path))
            self.assertEqual(s.wechat_cookies["skey"], "fromfile")
            self.assertEqual(s.wechat_cookies["uin"], "42")
        finally:
            os.unlink(tmp_path)

    def test_settings_dict_overrides_file(self):
        from mediascribe.config import Settings

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("skey=fromfile\n")
            tmp_path = f.name
        try:
            s = Settings(
                wechat_cookies={"skey": "fromdict"},
                wechat_cookies_file=Path(tmp_path),
            )
            self.assertEqual(s.wechat_cookies["skey"], "fromdict")
        finally:
            os.unlink(tmp_path)

    def test_env_var_injection(self):
        from mediascribe.config import Settings

        with patch.dict(os.environ, {"MEDIASCRIBE_WECHAT_COOKIE": "env_k=env_v"}):
            s = Settings()
            self.assertEqual(s.wechat_cookies.get("env_k"), "env_v")


class TestWechatMpDownloaderCookies(unittest.TestCase):
    """WechatMpDownloader 接受 cookies 注入"""

    def setUp(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        self.d = WechatMpDownloader()

    def test_default_no_cookies(self):
        self.assertEqual(self.d._active_cookies, {})

    def test_attach_dict(self):
        self.d.attach_cookies(cookies={"skey": "x"})
        self.assertEqual(self.d._active_cookies, {"skey": "x"})
        self.assertEqual(self.d._cookies_source, "dict")

    def test_attach_file(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("fromfile=1\n")
            tmp = f.name
        try:
            d = WechatMpDownloader()
            d.attach_cookies(cookies_file=Path(tmp))
            self.assertEqual(d._active_cookies.get("fromfile"), "1")
            self.assertTrue(d._cookies_source.startswith("file:"))
        finally:
            os.unlink(tmp)

    def test_dict_overrides_file(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("k=fromfile\n")
            tmp = f.name
        try:
            d = WechatMpDownloader()
            d.attach_cookies(cookies={"k": "fromdict"}, cookies_file=Path(tmp))
            self.assertEqual(d._active_cookies["k"], "fromdict")
        finally:
            os.unlink(tmp)

    def test_settings_cookies_inherited_on_download(self):
        """download() 内部自动注入 settings.wechat_cookies。"""
        from mediascribe.config import Settings

        with patch.object(self.d, "_fetch_html", return_value="<html></html>") as m:
            with patch.object(self.d, "_extract_text", return_value="hello"), patch.object(
                self.d, "_extract_meta", return_value={"title": "t"}
            ), patch.object(self.d, "_extract_video_url", return_value=None):
                s = Settings(wechat_cookies={"skey": "x"})
                src_path = s.audio_dir / "stub.txt"
                src_path.parent.mkdir(parents=True, exist_ok=True)
                # 替换 write_text_stub 简化：mock 一下
                with patch.object(self.d, "_write_text_stub", return_value=src_path):
                    from mediascribe.models import SourceRef

                    self.d.download(
                        SourceRef(
                            raw_input="x",
                            kind="wechat_mp",
                            url="https://mp.weixin.qq.com/s?abc",
                        ),
                        s,
                    )
            # 验证 _fetch_html 收到带 cookie 的请求
            m.assert_called_once()
            self.assertEqual(self.d._active_cookies.get("skey"), "x")
            self.assertEqual(self.d._cookies_source, "settings")


class TestMcpWechatMpTool(unittest.TestCase):
    """mcp_server._tool_transcribe_wechat_mp"""

    def test_listed_in_tool_list(self):
        from mediascribe import mcp_server

        names = [t["name"] for t in mcp_server.TOOL_LIST]
        self.assertIn("transcribe_wechat_mp", names)

    def test_handler_registered(self):
        from mediascribe import mcp_server

        self.assertIn("transcribe_wechat_mp", mcp_server.TOOL_HANDLERS)

    def test_url_required(self):
        from mediascribe.mcp_server import _tool_transcribe_wechat_mp

        result = _tool_transcribe_wechat_mp({})
        self.assertFalse(result["ok"])
        self.assertIn("url is required", result["error"])

    def test_non_wechat_url_rejected(self):
        from mediascribe.mcp_server import _tool_transcribe_wechat_mp

        result = _tool_transcribe_wechat_mp({"url": "https://www.bilibili.com/video/BV1xx"})
        self.assertFalse(result["ok"])
        self.assertIn("not a WeChat MP article", result["error"])

    def test_text_article_mode(self):
        """文本文章直接返回 ok=True + mode=text。"""
        import tempfile
        from pathlib import Path

        from mediascribe.mcp_server import _tool_transcribe_wechat_mp

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            # mock pipeline.transcribe 返回一个 TranscriptResult
            mock_result = MagicMock()
            mock_result.engine = "wechat_mp_text"
            mock_result.transcript_path = Path(tmp) / "transcripts" / "x.md"
            mock_result.audio_path = Path(tmp) / "audio" / "x.txt"
            mock_result.language = "zh"

            with patch("mediascribe.pipeline.Pipeline") as MockPipeline:
                MockPipeline.return_value.transcribe.return_value = mock_result
                result = _tool_transcribe_wechat_mp(
                    {
                        "url": "https://mp.weixin.qq.com/s?__biz=MzA&mid=1&idx=1",
                        "output_dir": str(out_dir),
                    }
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "text")
            self.assertEqual(result["engine"], "wechat_mp_text")
            # Settings 接收了正确的 cookies
            settings_call = MockPipeline.call_args.kwargs["settings"]
            self.assertEqual(settings_call.model, "small")  # default

    def test_video_article_mode(self):
        """视频消息返回 mode=video。"""
        from mediascribe.mcp_server import _tool_transcribe_wechat_mp

        mock_result = MagicMock()
        mock_result.engine = "whisper"
        mock_result.transcript_path = "t.md"
        mock_result.audio_path = "a.wav"
        mock_result.language = "zh"

        with patch("mediascribe.pipeline.Pipeline") as MockPipeline:
            MockPipeline.return_value.transcribe.return_value = mock_result
            result = _tool_transcribe_wechat_mp(
                {"url": "https://mp.weixin.qq.com/s?__biz=MzA&mid=1&idx=1"}
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "video")

    def test_cookies_passed_to_settings(self):
        from mediascribe.mcp_server import _tool_transcribe_wechat_mp

        mock_result = MagicMock()
        mock_result.engine = "wechat_mp_text"
        mock_result.transcript_path = "t.md"
        mock_result.audio_path = "a.txt"
        mock_result.language = "zh"

        with patch("mediascribe.pipeline.Pipeline") as MockPipeline:
            MockPipeline.return_value.transcribe.return_value = mock_result
            _tool_transcribe_wechat_mp(
                {
                    "url": "https://mp.weixin.qq.com/s?__biz=MzA&mid=1&idx=1",
                    "cookies": {"skey": "abc"},
                }
            )
        settings_call = MockPipeline.call_args.kwargs["settings"]
        self.assertEqual(settings_call.wechat_cookies, {"skey": "abc"})


if __name__ == "__main__":
    unittest.main()
