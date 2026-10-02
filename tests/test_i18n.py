"""douyin_batch.i18n 的测试 —— 迁入自 tests/test_coverage_gaps_2.py(按被测模块归位)。"""

from __future__ import annotations

import pytest

###########################################################################
# 迁入自 tests/test_coverage_gaps_2.py —— TestI18nExtras(douyin_batch.i18n)
###########################################################################


class TestI18nExtras:
    def test_messages_english(self):
        from douyin_batch.i18n import Messages, set_language, t

        set_language("en")
        # HEADER_TITLE has both en and zh entries.
        assert t("HEADER_TITLE") == Messages.HEADER_TITLE["en"]

    def test_messages_chinese(self):
        from douyin_batch.i18n import set_language, t

        set_language("zh")
        assert t("HEADER_TITLE") == "MediaScribe - 批量转录"

    def test_messages_format_kwargs(self):
        from douyin_batch.i18n import set_language, t

        set_language("en")
        # SUCCESS_VIDEOS_FOUND supports ``{count}`` formatting.
        out = t("SUCCESS_VIDEOS_FOUND", count=5)
        assert "5" in out

    def test_messages_unknown_key_returns_bracketed(self):
        from douyin_batch.i18n import set_language, t

        set_language("en")
        # Unknown keys are wrapped in ``[ ... ]`` so they're easy to
        # spot during translation audits.
        assert t("this_key_does_not_exist") == "[this_key_does_not_exist]"

    def test_detect_language_returns_str(self):
        from douyin_batch.i18n import detect_language, get_language, init_language

        # The auto-detect helper returns one of "en" / "zh".
        lang = detect_language()
        assert lang in ("en", "zh")
        # ``init_language`` doesn't return anything; it just sets the
        # global state.  Verify the side effect via ``get_language()``.
        init_language("en")
        assert get_language() == "en"
        init_language(None)  # auto-detect
        assert get_language() in ("en", "zh")

    def test_get_language_round_trip(self):
        from douyin_batch.i18n import get_language, set_language

        set_language("en")
        assert get_language() == "en"
        set_language("zh")
        assert get_language() == "zh"
        # Unknown lang raises.
        with pytest.raises(ValueError):
            set_language("zz")
