"""Transcriber factory 构造测试。

历史说明：本文件原名 ``test_whisperx_fallback.py``，测的是 factory 的
回退链；该回退链（含内存压力换序）2026-10-29 删除 —— 产品零调用，且
「可用内存 <4096」口径与 runtime_guard 的「总内存 <8192」互相矛盾，
引擎切换决策单点收敛到 :mod:`mediascribe.runtime_guard`（行为由
``tests/test_runtime_guard.py`` 钉死）。仅存 factory 的构造契约测试。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


class TestTranscriberFactory(unittest.TestCase):
    def test_get_transcriber_whisper(self):
        from mediascribe.transcribers import (
            WhisperTranscriber,
            get_transcriber,
        )

        t = get_transcriber("whisper", model="tiny")
        self.assertIsInstance(t, WhisperTranscriber)
        self.assertEqual(t.model_name, "tiny")

    def test_get_transcriber_unknown_raises(self):
        from mediascribe.transcribers import get_transcriber

        with self.assertRaises(ValueError):
            get_transcriber("does-not-exist")

    def test_package_no_longer_exports_fallback_chain(self):
        """删除的回退链 API 不得再从包里导出（防半删除状态）。"""
        import mediascribe.transcribers as pkg

        for gone in (
            "DEFAULT_FALLBACK_CHAIN",
            "get_transcriber_with_fallback",
            "list_available_engines",
        ):
            self.assertFalse(
                hasattr(pkg, gone),
                msg=f"{gone} 已删除，不应再被导出",
            )


if __name__ == "__main__":
    unittest.main()
