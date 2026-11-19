"""
引擎名单的**跨入口一致性**测试。

背景
----
2026-10-24 新增 ``whisper-cpp`` 引擎时，发现引擎名单在仓库里有**四处**独立维护：

1. ``mediascribe/config.py`` 的 ``_VALID_ENGINES``
2. ``mediascribe/__main__.py`` 的 ``--engine`` choices
3. ``mediascribe/web/models.py`` 的 ``TranscribeRequest.engine`` 正则
4. ~~factory 的回退链~~（2026-10-29 已删：产品零调用，引擎决策单点收敛到 runtime_guard）

只改了 1、2、4 ⇒ **Web UI 选 whisper-cpp 会被 422 拒绝**（第 3 处漏改）。

P2-9(2026-11-20 评审)后的钉法升级:三处**接线**全部收敛到
``config._VALID_ENGINES`` 单一真源(CLI ``choices=list(_VALID_ENGINES)``、
Web pattern 由名单动态构造),本文件随之从「正则解析源码抠名单」改为
**行为断言** —— Web 模型逐引擎接受 + 未知拒绝 + factory 可构造 +
CLI 源码接线检查(只锁接线,不锁名单写法)。
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mediascribe.config import _VALID_ENGINES  # noqa: E402


def _fastapi_missing() -> bool:
    from importlib import util

    return util.find_spec("fastapi") is None


class TestEngineNameConsistency(unittest.TestCase):
    """名单单一真源 + 三处接线。"""

    def test_cli_choices_wired_to_config(self):
        """CLI --engine choices 引用 config._VALID_ENGINES(锁接线,不锁写法)。"""
        source = (ROOT / "mediascribe" / "__main__.py").read_text(encoding="utf-8")
        match = re.search(r'"--engine".*?choices=(.*?),\n', source, re.S)
        self.assertIsNotNone(match, "未能在 __main__.py 里定位 --engine 的 choices")
        self.assertIn("_VALID_ENGINES", match.group(1), "CLI choices 必须接 config 真源")

    def test_web_pattern_wired_to_config(self):
        """Web pattern 由 _VALID_ENGINES 动态构造(锁接线)。"""
        source = (ROOT / "mediascribe" / "web" / "models.py").read_text(encoding="utf-8")
        self.assertIn("_VALID_ENGINES", source)
        self.assertNotIn(
            'pattern="^(whisper|',
            source,
            "Web pattern 不得手抄引擎名单 —— 必须由 _VALID_ENGINES 构造",
        )

    def test_web_model_accepts_every_valid_engine(self):
        """行为断言:Web 模型逐引擎接受 —— 名单改了这里必须跟着过。"""
        if _fastapi_missing():
            self.skipTest("fastapi 未安装")
        from mediascribe.web.app import TranscribeRequest

        for engine in _VALID_ENGINES:
            req = TranscribeRequest(urls=["https://example.com/a.mp4"], engine=engine)
            self.assertEqual(req.engine, engine, engine)

    def test_web_model_rejects_unknown_engine(self):
        if _fastapi_missing():
            self.skipTest("fastapi 未安装")
        from pydantic import ValidationError

        from mediascribe.web.app import TranscribeRequest

        with self.assertRaises(ValidationError):
            TranscribeRequest(urls=["https://example.com/a.mp4"], engine="nope")

    def test_whisper_cpp_registered_everywhere(self):
        """新增引擎时最容易漏的就是 Web UI —— 单独锁一条。"""
        self.assertIn("whisper-cpp", _VALID_ENGINES)
        if not _fastapi_missing():
            from mediascribe.web.app import TranscribeRequest

            self.assertEqual(
                TranscribeRequest(urls=["https://example.com/a.mp4"], engine="whisper-cpp").engine,
                "whisper-cpp",
            )

    def test_every_valid_engine_is_reachable(self):
        """每个合法引擎都必须能被 factory 构造。"""
        from mediascribe.transcribers.factory import get_transcriber

        for engine in _VALID_ENGINES:
            transcriber = get_transcriber(engine, model="tiny", device="cpu")
            self.assertTrue(hasattr(transcriber, "transcribe"))

    def test_transcribers_package_exports_every_engine(self):
        """``mediascribe.transcribers`` 必须导出每个引擎的实现类。

        实战踩坑：新增 whisper-cpp 时忘了加进 ``__init__.py`` ⇒
        ``from .transcribers import WhisperCppTranscriber`` 直接 ImportError，
        整个包都起不来（而引擎本身是好的）。
        """
        import mediascribe.transcribers as pkg

        expected = {
            "whisper": "WhisperTranscriber",
            "faster-whisper": "FasterWhisperTranscriber",
            "whisperx": "WhisperXTranscriber",
            "whisper-cpp": "WhisperCppTranscriber",
        }
        for engine, cls_name in expected.items():
            self.assertTrue(
                hasattr(pkg, cls_name),
                msg=f"mediascribe.transcribers 缺少 {cls_name}（引擎 {engine}）",
            )
            self.assertIn(cls_name, pkg.__all__)


class TestWebAcceptsWhisperCpp(unittest.TestCase):
    """Web 层必须真的接受 whisper-cpp（不是只在校验名单里）。"""

    def test_request_model_accepts_whisper_cpp(self):
        if _fastapi_missing():
            self.skipTest("fastapi 未安装")
        from mediascribe.web.app import TranscribeRequest

        req = TranscribeRequest(urls=["https://example.com/a.mp4"], engine="whisper-cpp")
        self.assertEqual(req.engine, "whisper-cpp")

    def test_request_model_rejects_unknown_engine(self):
        if _fastapi_missing():
            self.skipTest("fastapi 未安装")
        from pydantic import ValidationError

        from mediascribe.web.app import TranscribeRequest

        with self.assertRaises(ValidationError):
            TranscribeRequest(urls=["https://example.com/a.mp4"], engine="nope")


if __name__ == "__main__":
    unittest.main()
