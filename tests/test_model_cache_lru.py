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


def setUpModule():
    """P1-4: 引擎缓存现在入跨引擎驻留账 —— 每个文件重置注册表保证测试独立。"""
    from mediascribe.gpu_budget import reset_resident_registry

    reset_resident_registry()


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


from mediascribe.transcribers import faster_whisper as fw_mod  # noqa: E402
from mediascribe.transcribers import whisperx as wx_mod  # noqa: E402


class TestFasterWhisperCacheResidency(unittest.TestCase):
    """P1-4: faster-whisper 缓存装载入跨引擎驻留账,淘汰/清缓存反注册。"""

    def setUp(self):
        from mediascribe.gpu_budget import reset_resident_registry

        reset_resident_registry()
        fw_mod._MODEL_CACHE.clear()
        # CI 无 faster-whisper 包 → mock 掉导入探测与加载;预算钉 2000MB
        self._mods = mock.patch.dict(
            "sys.modules", {"faster_whisper": mock.MagicMock(name="fw_lib")}
        )
        self._mods.start()
        self.addCleanup(self._mods.stop)
        self._budget = mock.patch("mediascribe.gpu_budget.budget_total_mb", return_value=2000)
        self._budget.start()
        self.addCleanup(self._budget.stop)
        self._loader = mock.patch(
            "mediascribe.transcribers.faster_whisper._load_whisper_model",
            side_effect=lambda name, device, ct: SimpleNamespace(model=name),
        )
        self._loader.start()
        self.addCleanup(self._loader.stop)
        self.addCleanup(fw_mod.clear_model_cache)
        self.addCleanup(reset_resident_registry)

    def test_load_registers_and_eviction_unregisters(self):
        from mediascribe.gpu_budget import resident_total_mb

        fw_mod._get_cached_model("small", "cuda", "int8_float16")
        fw_mod._get_cached_model("large-v3", "cuda", "int8_float16")
        fw_mod._get_cached_model("medium", "cuda", "int8_float16")  # 淘汰 small
        self.assertEqual(len(fw_mod._MODEL_CACHE), 2)
        keys = {k for (_fam, k) in [(f, k) for (f, k) in _resident_keys()]}
        self.assertEqual(
            keys, {("large-v3", "cuda", "int8_float16"), ("medium", "cuda", "int8_float16")}
        )
        # 实测表: fw large-v3 cuda=2816 + medium cuda=1600
        self.assertEqual(resident_total_mb(), 2816 + 1600)

    def test_clear_model_cache_unregisters_family(self):
        from mediascribe.gpu_budget import resident_total_mb

        fw_mod._get_cached_model("small", "cuda", "int8_float16")
        self.assertGreater(resident_total_mb(), 0)
        fw_mod.clear_model_cache()
        self.assertEqual(resident_total_mb(), 0)
        self.assertEqual(len(fw_mod._MODEL_CACHE), 0)


def _resident_keys():
    from mediascribe import gpu_budget

    with gpu_budget._RESIDENT_LOCK:
        return [(f, k) for (f, k) in gpu_budget._RESIDENT]


class TestWhisperXResidencyAccounting(unittest.TestCase):
    """P1-4: whisperx 主模型/对齐模型/分离管线全部入跨引擎驻留账。"""

    def setUp(self):
        from mediascribe.gpu_budget import reset_resident_registry

        reset_resident_registry()
        wx_mod._MODEL_CACHE.clear()
        wx_mod._ALIGN_MODEL_CACHE.clear()
        wx_mod._DIARIZE_CACHE.clear()
        self._budget = mock.patch("mediascribe.gpu_budget.budget_total_mb", return_value=2000)
        self._budget.start()
        self.addCleanup(self._budget.stop)
        self.addCleanup(wx_mod.clear_model_cache)
        self.addCleanup(reset_resident_registry)

    def test_main_and_align_registered(self):
        from mediascribe.gpu_budget import resident_total_mb

        fake = SimpleNamespace(
            load_model=lambda name, device, compute_type=None, language=None: SimpleNamespace(
                tag=f"{name}"
            ),
            load_align_model=lambda language_code, device: (
                SimpleNamespace(tag=f"align-{language_code}"),
                {"detreator": None},
            ),
        )
        wx_mod._get_cached_model(fake, "small", "cuda", "zh")
        wx_mod._get_cached_align_model(fake, "zh", "cuda")
        # 主模型 small cuda=900(实测表) + align 记 small 档=900
        self.assertEqual(resident_total_mb(), 900 + 900)
        wx_mod.clear_model_cache()
        self.assertEqual(resident_total_mb(), 0)
