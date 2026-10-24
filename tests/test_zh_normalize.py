"""
中文简繁统一（P2）的单元测试。

覆盖三层防线：
1. 引擎侧 —— 三个引擎在中文场景注入简体 ``initial_prompt``（跨平台有效）
2. 输出侧 —— ``build_markdown`` 的兜底转换（Windows 有效）
3. 配置侧 —— ``Settings.zh_simplify`` 的优先级（显式参数 > env > 默认开）
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mediascribe.config import Settings  # noqa: E402
from mediascribe.markdown_builder import build_markdown  # noqa: E402
from mediascribe.zh_normalize import (  # noqa: E402
    SIMPLIFIED_ZH_PROMPT,
    conversion_available,
    is_zh_language,
    normalize_text,
    simplified_initial_prompt,
    to_simplified,
)

TRADITIONAL = "各位觀眾大家好，歡迎收看本期的科技觀察節目。"
SIMPLIFIED = "各位观众大家好，欢迎收看本期的科技观察节目。"


class TestSimplifiedInitialPrompt(unittest.TestCase):
    """引擎侧的简体引导（跨平台有效，是主力防线）。"""

    def test_zh_injects_prompt(self):
        self.assertEqual(simplified_initial_prompt(None, "zh"), SIMPLIFIED_ZH_PROMPT)

    def test_zh_variants_inject(self):
        for lang in ("zh", "ZH", "yue", "cmn"):
            self.assertEqual(
                simplified_initial_prompt(None, lang),
                SIMPLIFIED_ZH_PROMPT,
                msg=f"语言 {lang} 应注入简体 prompt",
            )

    def test_explicit_prompt_wins(self):
        """用户自定义 prompt 优先（常含术语表）。"""
        custom = "术语表：叁月聚粮、莫德尔分法"
        self.assertEqual(simplified_initial_prompt(custom, "zh"), custom)

    def test_non_zh_no_prompt(self):
        for lang in ("en", "ja", None, ""):
            self.assertIsNone(simplified_initial_prompt(None, lang))

    def test_is_zh_language(self):
        self.assertTrue(is_zh_language("zh"))
        self.assertTrue(is_zh_language("zh-TW"))
        self.assertFalse(is_zh_language("en"))
        self.assertFalse(is_zh_language(None))


class TestToSimplified(unittest.TestCase):
    """输出侧兜底转换。"""

    def test_conversion_never_raises(self):
        for text in ("", "plain ascii", TRADITIONAL, "混合 mixed 文字", "🎉"):
            out = to_simplified(text)
            self.assertIsInstance(out, str)
            if not text:
                self.assertEqual(out, "")

    def test_windows_converts(self):
        if not conversion_available():
            self.skipTest("本平台无零依赖繁简转换能力")
        self.assertEqual(to_simplified(TRADITIONAL), SIMPLIFIED)

    def test_simplified_unchanged(self):
        if not conversion_available():
            self.skipTest("本平台无零依赖繁简转换能力")
        self.assertEqual(to_simplified(SIMPLIFIED), SIMPLIFIED)


class TestNormalizeText(unittest.TestCase):
    def test_disabled_returns_original(self):
        self.assertEqual(normalize_text(TRADITIONAL, "zh", enabled=False), TRADITIONAL)

    def test_non_zh_language_returns_original(self):
        self.assertEqual(normalize_text(TRADITIONAL, "en"), TRADITIONAL)

    def test_zh_normalized(self):
        if not conversion_available():
            self.skipTest("本平台无零依赖繁简转换能力")
        self.assertEqual(normalize_text(TRADITIONAL, "zh"), SIMPLIFIED)


class TestBuildMarkdownSimplify(unittest.TestCase):
    """``build_markdown`` 的输出层兜底。"""

    def _transcription(self, language="zh"):
        return {"segments": [], "model": "openai-whisper", "language": language}

    def test_default_normalizes(self):
        if not conversion_available():
            self.skipTest("本平台无零依赖繁简转换能力")
        md = build_markdown("标题", TRADITIONAL, self._transcription(), None)
        self.assertIn(SIMPLIFIED, md)
        self.assertNotIn(TRADITIONAL, md)

    def test_disabled_keeps_original(self):
        md = build_markdown("标题", TRADITIONAL, self._transcription(), None, zh_simplify=False)
        self.assertIn(TRADITIONAL, md)

    def test_segments_branch_normalizes(self):
        """``--timestamps`` 分支走 segments 文本，同样要归一化。"""
        if not conversion_available():
            self.skipTest("本平台无零依赖繁简转换能力")
        tr = {
            "segments": [{"text": TRADITIONAL, "start": 0.0, "end": 5.0}],
            "model": "openai-whisper",
            "language": "zh",
        }
        md = build_markdown("标题", TRADITIONAL, tr, None, timestamps=True)
        self.assertIn(SIMPLIFIED, md)
        self.assertNotIn(TRADITIONAL, md)


class TestSettingsZhSimplify(unittest.TestCase):
    """``Settings.zh_simplify`` 的优先级：显式参数 > env > 默认开。"""

    def test_default_true(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEDIASCRIBE_ZH_SIMPLIFY", None)
            self.assertTrue(Settings().zh_simplify)

    def test_explicit_false_wins(self):
        with mock.patch.dict(os.environ, {"MEDIASCRIBE_ZH_SIMPLIFY": "1"}):
            self.assertFalse(Settings(zh_simplify=False).zh_simplify)

    def test_env_false(self):
        with mock.patch.dict(os.environ, {"MEDIASCRIBE_ZH_SIMPLIFY": "0"}):
            self.assertFalse(Settings().zh_simplify)

    def test_env_true(self):
        with mock.patch.dict(os.environ, {"MEDIASCRIBE_ZH_SIMPLIFY": "true"}):
            self.assertTrue(Settings().zh_simplify)


class TestEnginesInjectSimplifiedPrompt(unittest.TestCase):
    """三个引擎都必须在中文场景注入简体 prompt。

    这是**跨平台**防线（输出层转换只有 Windows 有），因此不能只测一个引擎。
    """

    def _assert_injects(self, module_name, cls_name, monkeypatched_model):
        import importlib

        mod = importlib.import_module(module_name)
        cls = getattr(mod, cls_name)
        inst = cls(model="small", device="cpu")
        inst._model = monkeypatched_model
        with mock.patch.object(mod, "_get_cached_model", return_value=monkeypatched_model):
            with mock.patch.object(cls, "_ensure_model", return_value=monkeypatched_model):
                inst.transcribe(Path("dummy.wav"), language="zh")
        return monkeypatched_model

    def test_faster_whisper_injects(self):
        captured = {}

        class FakeSegments:
            def __iter__(self):
                return iter([])

        class FakeModel:
            def transcribe(self, path, **kwargs):
                captured.update(kwargs)
                return FakeSegments(), type("Info", (), {"language": "zh"})()

        self._assert_injects(
            "mediascribe.transcribers.faster_whisper", "FasterWhisperTranscriber", FakeModel()
        )
        self.assertEqual(captured.get("initial_prompt"), SIMPLIFIED_ZH_PROMPT)

    def test_openai_whisper_injects(self):
        captured = {}

        class FakeModel:
            def transcribe(self, path, **kwargs):
                captured.update(kwargs)
                return {"text": "", "segments": [], "language": "zh"}

        self._assert_injects("mediascribe.transcribers.whisper", "WhisperTranscriber", FakeModel())
        self.assertEqual(captured.get("initial_prompt"), SIMPLIFIED_ZH_PROMPT)

    def test_whisperx_injects(self):
        captured = {}

        class FakeWhisperXModel:
            def transcribe(self, audio, **kwargs):
                captured.update(kwargs)
                return {"segments": [], "language": "zh"}

        fake_module = mock.MagicMock()
        fake_module.load_audio.return_value = b"audio"
        fake_module.load_model.return_value = FakeWhisperXModel()

        import importlib

        mod = importlib.import_module("mediascribe.transcribers.whisperx")
        inst = mod.WhisperXTranscriber(model="small", device="cpu")
        with mock.patch.dict(sys.modules, {"whisperx": fake_module}):
            with mock.patch.object(mod, "_get_cached_model", return_value=FakeWhisperXModel()):
                inst.transcribe(Path("dummy.wav"), language="zh")
        self.assertEqual(captured.get("initial_prompt"), SIMPLIFIED_ZH_PROMPT)


if __name__ == "__main__":
    unittest.main()
