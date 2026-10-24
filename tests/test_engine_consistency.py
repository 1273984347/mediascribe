"""
引擎名单的**跨入口一致性**测试。

背景
----
2026-10-24 新增 ``whisper-cpp`` 引擎时，发现引擎名单在仓库里有**四处**独立维护：

1. ``mediascribe/config.py`` 的 ``_VALID_ENGINES``
2. ``mediascribe/__main__.py`` 的 ``--engine`` choices
3. ``mediascribe/web/app.py`` 的 ``TranscribeRequest.engine`` 正则
4. ``mediascribe/transcribers/factory.py`` 的 ``DEFAULT_FALLBACK_CHAIN``

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
from mediascribe.transcribers.factory import DEFAULT_FALLBACK_CHAIN  # noqa: E402


def _cli_engine_choices() -> set:
    """从 ``mediascribe/__main__.py`` 里解析 ``--engine`` 的 choices。"""
    source = (ROOT / "mediascribe" / "__main__.py").read_text(encoding="utf-8")
    match = re.search(r'"--engine".*?choices=\[(.*?)\]', source, re.S)
    assert match, "未能在 __main__.py 里定位 --engine 的 choices"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _web_engine_pattern() -> set:
    """从 ``mediascribe/web/app.py`` 里解析 engine 的 pydantic 正则。"""
    source = (ROOT / "mediascribe" / "web" / "app.py").read_text(encoding="utf-8")
    match = re.search(r'engine: str = Field\("whisper", pattern="\^?(.*?)\$"\)', source)
    assert match, "未能在 web/app.py 里定位 engine 的 pattern"
    raw = match.group(1).replace("(", "").replace(")", "")
    return {part for part in raw.split("|") if part}


class TestEngineNameConsistency(unittest.TestCase):
    """四处引擎名单必须完全一致。"""

    def test_cli_choices_match_config(self):
        self.assertEqual(_cli_engine_choices(), set(_VALID_ENGINES))

    def test_web_pattern_matches_config(self):
        self.assertEqual(_web_engine_pattern(), set(_VALID_ENGINES))

    def test_fallback_chain_is_subset_of_valid_engines(self):
        for engine in DEFAULT_FALLBACK_CHAIN:
            self.assertIn(
                engine,
                _VALID_ENGINES,
                msg=f"回退链里的 {engine!r} 不在合法引擎名单中",
            )

    def test_whisper_cpp_registered_everywhere(self):
        """新增引擎时最容易漏的就是 Web UI —— 单独锁一条。"""
        self.assertIn("whisper-cpp", _VALID_ENGINES)
        self.assertIn("whisper-cpp", _cli_engine_choices())
        self.assertIn("whisper-cpp", _web_engine_pattern())
        self.assertIn("whisper-cpp", DEFAULT_FALLBACK_CHAIN)

    def test_every_valid_engine_is_reachable(self):
        """每个合法引擎都必须能被 factory 构造。"""
        from mediascribe.transcribers.factory import get_transcriber

        for engine in _VALID_ENGINES:
            transcriber = get_transcriber(engine, model="tiny", device="cpu")
            self.assertTrue(hasattr(transcriber, "transcribe"))


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
