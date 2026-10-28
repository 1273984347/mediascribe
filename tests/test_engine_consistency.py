"""
引擎名单的**跨入口一致性**测试。

背景
----
2026-10-24 新增 ``whisper-cpp`` 引擎时，发现引擎名单在仓库里有**四处**独立维护：

1. ``mediascribe/config.py`` 的 ``_VALID_ENGINES``
2. ``mediascribe/__main__.py`` 的 ``--engine`` choices
3. ``mediascribe/web/models.py`` 的 ``TranscribeRequest.engine`` 正则
   （2026-10-25 职责拆分前在 ``mediascribe/web/app.py``）
4. ~~factory 的回退链~~（2026-10-29 已删：产品零调用，引擎决策单点收敛到 runtime_guard）

只改了 1、2、4 ⇒ **Web UI 选 whisper-cpp 会被 422 拒绝**（第 3 处漏改）。
这类「改了核心枚举忘了其它入口」的缺口，靠人记是记不住的 —— 本文件把它变成
**可回归的断言**：任何一处漏改，CI 立刻红。
"""

from __future__ import annotations

import argparse
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mediascribe.config import _VALID_ENGINES  # noqa: E402


def _cli_engine_choices() -> set:
    """从 ``mediascribe/__main__.py`` 里解析 ``--engine`` 的 choices。"""
    source = (ROOT / "mediascribe" / "__main__.py").read_text(encoding="utf-8")
    match = re.search(r'"--engine".*?choices=\[(.*?)\]', source, re.S)
    assert match, "未能在 __main__.py 里定位 --engine 的 choices"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _web_engine_pattern() -> set:
    """从 ``mediascribe/web/models.py`` 里解析 engine 的 pydantic 正则。

    2026-10-25 职责拆分后, pydantic 模型搬到了 ``mediascribe/web/models.py``
    (``app.py`` 只是 re-export) ⇒ 正则的真正所在也换了地方。
    """
    source = (ROOT / "mediascribe" / "web" / "models.py").read_text(encoding="utf-8")
    match = re.search(r'engine: str = Field\("whisper", pattern="\^?(.*?)\$"\)', source)
    assert match, "未能在 web/models.py 里定位 engine 的 pattern"
    raw = match.group(1).replace("(", "").replace(")", "")
    return {part for part in raw.split("|") if part}


class TestEngineNameConsistency(unittest.TestCase):
    """四处引擎名单必须完全一致。"""

    def test_cli_choices_match_config(self):
        self.assertEqual(_cli_engine_choices(), set(_VALID_ENGINES))

    def test_web_pattern_matches_config(self):
        self.assertEqual(_web_engine_pattern(), set(_VALID_ENGINES))

    def test_whisper_cpp_registered_everywhere(self):
        """新增引擎时最容易漏的就是 Web UI —— 单独锁一条。"""
        self.assertIn("whisper-cpp", _VALID_ENGINES)
        self.assertIn("whisper-cpp", _cli_engine_choices())
        self.assertIn("whisper-cpp", _web_engine_pattern())

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
        pytest_fastapi = __import__("importlib").util.find_spec("fastapi")
        if pytest_fastapi is None:
            self.skipTest("fastapi 未安装")
        from mediascribe.web.app import TranscribeRequest

        req = TranscribeRequest(urls=["https://example.com/a.mp4"], engine="whisper-cpp")
        self.assertEqual(req.engine, "whisper-cpp")

    def test_request_model_rejects_unknown_engine(self):
        from importlib import util

        if util.find_spec("fastapi") is None:
            self.skipTest("fastapi 未安装")
        from pydantic import ValidationError

        from mediascribe.web.app import TranscribeRequest

        with self.assertRaises(ValidationError):
            TranscribeRequest(urls=["https://example.com/a.mp4"], engine="nope")


if __name__ == "__main__":
    unittest.main()
