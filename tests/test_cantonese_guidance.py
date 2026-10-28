"""粤语（``-l yue``）转写引导的单元测试。

覆盖 2026-10-28 的粤语可用性优化：

1. ``auto_select_model`` 对 yue 恒推荐 large-v3（时长无关），
   zh / cmn / None 的时长分档行为不变
2. ``_AutoModelTranscriber`` 把 ``Settings.language`` 透传给
   ``auto_select_model``；时长探测失败时 yue 也不落 ``small``
3. 弱模型（tiny/base/small/medium）+ yue 打印警告，
   强模型 / auto / 其他语言静默
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mediascribe.config import Settings  # noqa: E402
from mediascribe.pipeline import (  # noqa: E402
    _YUE_WEAK_MODELS,
    Pipeline,
    _warn_weak_cantonese_model,
)
from mediascribe.post_process import auto_select_model  # noqa: E402
from mediascribe.zh_normalize import is_yue_language  # noqa: E402


class TestIsYueLanguage(unittest.TestCase):
    def test_positive(self):
        for lang in ("yue", "YUE", " yue ", "yue-HK"):
            self.assertTrue(is_yue_language(lang), lang)

    def test_negative(self):
        # 前缀匹配与 is_zh_language 的既有惯例一致（whisper 语言码集合
        # 里不存在 yue 前缀的其他码，前缀误报无现实输入）
        for lang in (None, "", "zh", "cmn", "en"):
            self.assertFalse(is_yue_language(lang), lang)


class TestAutoSelectModelYue(unittest.TestCase):
    """yue 恒推荐 large-v3；其他语言的时长分档不受影响。"""

    def test_yue_always_large_v3_regardless_of_duration(self):
        for seconds in (10, 60, 299, 600, 1800, 3600, 7200):
            self.assertEqual(auto_select_model(seconds, language="yue"), "large-v3", seconds)

    def test_yue_beats_prefer_quality_branch(self):
        self.assertEqual(auto_select_model(7200, prefer_quality=True, language="yue"), "large-v3")

    def test_non_yue_duration_tiers_unchanged(self):
        # 回归: 不传 language / 传 zh / cmn 的分档与历史一致
        self.assertEqual(auto_select_model(60), "medium")
        self.assertEqual(auto_select_model(1800), "tiny")
        self.assertEqual(auto_select_model(60, language="zh"), "medium")
        self.assertEqual(auto_select_model(1800, language="zh"), "tiny")
        self.assertEqual(auto_select_model(60, language="cmn"), "medium")
        self.assertEqual(auto_select_model(60, language="ZH"), "medium")


class TestAutoTranscriberLanguagePassThrough(unittest.TestCase):
    """``--model auto`` 时 Settings.language 必须参与模型推荐。"""

    def _make_pipeline(self, language):
        return Pipeline(Settings(model="auto", language=language))

    def test_resolve_passes_language_to_auto_select(self):
        p = self._make_pipeline("yue")
        with (
            mock.patch(
                "mediascribe.post_process.auto_select_model", return_value="large-v3"
            ) as sel,
            mock.patch("mediascribe.pipeline._probe_audio_duration", return_value=120.0),
            mock.patch.object(p, "_create_transcriber") as ct,
        ):
            p.transcriber._duration_seconds = 120.0
            p.transcriber._resolve()
            sel.assert_called_once_with(120, language="yue")
            ct.assert_called_once()

    def test_probe_failure_still_gives_yue_large_v3(self):
        p = self._make_pipeline("yue")
        with mock.patch.object(p, "_create_transcriber") as ct:
            p.transcriber._duration_seconds = 0.0
            p.transcriber._resolve()
            # 时长探测失败: 粤语不落 small, 直接 large-v3
            self.assertEqual(ct.call_args.kwargs.get("model"), "large-v3")

    def test_probe_failure_keeps_small_for_other_languages(self):
        p = self._make_pipeline(None)
        with mock.patch.object(p, "_create_transcriber") as ct:
            p.transcriber._duration_seconds = 0.0
            p.transcriber._resolve()
            self.assertEqual(ct.call_args.kwargs.get("model"), "small")


class TestWeakCantoneseModelWarning(unittest.TestCase):
    def test_weak_model_warns(self):
        for name in sorted(_YUE_WEAK_MODELS):
            with mock.patch("builtins.print") as pr:
                _warn_weak_cantonese_model("yue", name)
            self.assertEqual(pr.call_count, 1, name)
            self.assertIn("large-v3", pr.call_args.args[0])

    def test_strong_or_auto_or_other_language_silent(self):
        for language, model in (
            ("yue", "large-v3"),
            ("yue", "large"),
            ("yue", "auto"),
            ("yue", None),
            ("yue", "distil-large-v3"),
            ("zh", "small"),
            (None, "small"),
        ):
            with mock.patch("builtins.print") as pr:
                _warn_weak_cantonese_model(language, model)
            self.assertEqual(pr.call_count, 0, (language, model))


if __name__ == "__main__":
    unittest.main()
