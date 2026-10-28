"""模块级模型缓存的 LRU 上限测试（whisperx / whisper，与 faster_whisper 对齐）。

背景（2026-10-29 审查 P2）：whisperx/whisper 的模块级缓存此前无上限，
长驻 Web 进程里每换一个档位显存就涨一档（8GB 卡第三档即 OOM 风险）；
faster_whisper 一直有 LRU 2 兜底。本轮对齐为 LRU 容量 2。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


class TestWhisperXCacheLRU(unittest.TestCase):
    def test_cache_evicts_beyond_capacity(self):
        from mediascribe.transcribers import whisperx as wx_mod

        wx_mod._MODEL_CACHE.clear()
        loads = []

        def fake_load(name, device, compute_type=None, language=None):
            loads.append((name, language))
            return SimpleNamespace(tag=f"{name}-{language}")

        try:
            for lang in ("zh", "en", "ja"):
                wx_mod._get_cached_model(
                    SimpleNamespace(load_model=fake_load), "small", "cpu", lang
                )
            self.assertLessEqual(len(wx_mod._MODEL_CACHE), wx_mod._MODEL_CACHE_MAX)
            # 最早的 zh 被淘汰; 再取 zh 应触发重新加载
            before = len(loads)
            wx_mod._get_cached_model(SimpleNamespace(load_model=fake_load), "small", "cpu", "zh")
            self.assertEqual(len(loads), before + 1)
        finally:
            wx_mod._MODEL_CACHE.clear()

    def test_cache_hit_refreshes_recency(self):
        from mediascribe.transcribers import whisperx as wx_mod

        wx_mod._MODEL_CACHE.clear()
        fake = SimpleNamespace(load_model=lambda *a, **k: SimpleNamespace())
        try:
            wx_mod._get_cached_model(fake, "tiny", "cpu", "zh")
            wx_mod._get_cached_model(fake, "small", "cpu", "zh")
            # 命中 tiny → 变为最近使用; 再入 small 应淘汰 zh 那条
            wx_mod._get_cached_model(fake, "tiny", "cpu", "zh")
            wx_mod._get_cached_model(fake, "small", "cpu", "zh")
            keys = list(wx_mod._MODEL_CACHE.keys())
            self.assertEqual(len(keys), 2)
            self.assertIn(("tiny", "cpu", "int8", "zh"), keys)
        finally:
            wx_mod._MODEL_CACHE.clear()


class TestWhisperCacheLRU(unittest.TestCase):
    def test_cache_evicts_beyond_capacity(self):
        from mediascribe.transcribers import whisper as w_mod

        w_mod._MODEL_CACHE.clear()
        loads = []
        fake = SimpleNamespace(
            load_model=lambda name, device=None: (
                loads.append((name, device)) or SimpleNamespace(tag=name)
            )
        )
        try:
            for name in ("tiny", "base", "small"):
                w_mod._get_cached_model(fake, name, "cpu")
            self.assertLessEqual(len(w_mod._MODEL_CACHE), w_mod._MODEL_CACHE_MAX)
        finally:
            w_mod._MODEL_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
