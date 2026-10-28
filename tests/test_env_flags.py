"""布尔 env 词表统一回归（2026-10-29 拍板：1/true/yes/on，大小写不敏感）。

背景：仓库曾有 3 种布尔词表并存 —— ``MEDIASCRIBE_AUDIO_ONLY`` 只认
1/true（用户设 ``=yes`` 被静默当成 false），batch 的 DOUYIN_BATCH_* 缺
on，其余用宽词表。统一为宽词表后本文件把各入口钉死。
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

TRUTHY = ("1", "true", "yes", "on")
FALSY = ("", "0", "false", "no", "off", "random")


class TestAudioOnlyEnv(unittest.TestCase):
    def test_wide_vocabulary_accepted(self):
        from mediascribe.config import Settings

        for value in TRUTHY:
            with self.subTest(value=value):
                with unittest.mock.patch.dict("os.environ", {"MEDIASCRIBE_AUDIO_ONLY": value}):
                    self.assertTrue(Settings().audio_only)

    def test_falsy_and_garbage_rejected(self):
        from mediascribe.config import Settings

        for value in FALSY:
            with self.subTest(value=value):
                with unittest.mock.patch.dict("os.environ", {"MEDIASCRIBE_AUDIO_ONLY": value}):
                    self.assertFalse(Settings().audio_only)

    def test_case_insensitive(self):
        from mediascribe.config import Settings

        with unittest.mock.patch.dict("os.environ", {"MEDIASCRIBE_AUDIO_ONLY": "YES"}):
            self.assertTrue(Settings().audio_only)


class TestBatchBoolEnv(unittest.TestCase):
    def test_wide_vocabulary_accepted(self):
        from mediascribe.batch_config import BatchConfig

        for value in TRUTHY:
            with (
                self.subTest(value=value),
                unittest.mock.patch.dict("os.environ", {"DOUYIN_BATCH_KEEP_AUDIO": value}),
            ):
                config = BatchConfig.from_env()
                self.assertTrue(config.keep_audio)


if __name__ == "__main__":
    unittest.main()
