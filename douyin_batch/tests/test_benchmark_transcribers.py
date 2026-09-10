"""
Unit tests for the transcriber benchmark script.

The benchmark is mostly an I/O / timing wrapper around the
``mediascribe.transcribers.factory`` module.  These tests pin
the parts that are easy to break: cost profile integrity, table
renderer, and the JSON report shape.
"""
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import benchmark_transcribers  # noqa: E402


class TestCostProfile(unittest.TestCase):
    """All three engines must have a valid cost profile entry."""

    def test_all_engines_have_profile(self):
        for engine in ("whisper", "faster-whisper", "whisperx"):
            self.assertIn(engine, benchmark_transcribers.COST_PROFILE)

    def test_profile_shape(self):
        for engine, profile in benchmark_transcribers.COST_PROFILE.items():
            self.assertIn("cold_start_ms", profile, engine)
            self.assertIn("ms_per_audio_sec", profile, engine)
            self.assertGreater(profile["cold_start_ms"], 0, engine)
            self.assertGreater(profile["ms_per_audio_sec"], 0, engine)

    def test_faster_whisper_cheaper_than_whisper(self):
        # Sanity: the documented ordering is whisperx ~ faster-whisper << whisper
        w = benchmark_transcribers.COST_PROFILE["whisper"]["ms_per_audio_sec"]
        fw = benchmark_transcribers.COST_PROFILE["faster-whisper"]["ms_per_audio_sec"]
        wx = benchmark_transcribers.COST_PROFILE["whisperx"]["ms_per_audio_sec"]
        self.assertLess(fw, w, "faster-whisper should be cheaper than whisper")
        self.assertLess(wx, w, "whisperx should be cheaper than whisper")


class TestRenderTable(unittest.TestCase):
    """The ASCII table must include every result row."""

    def test_renders_all_engines(self):
        results = [
            benchmark_transcribers.BenchResult(
                engine="whisper", available=True,
                cold_start_ms=10, per_call_ms=[100, 110], peak_mem_mb=1.0,
            ),
            benchmark_transcribers.BenchResult(
                engine="faster-whisper", available=True,
                cold_start_ms=5, per_call_ms=[20, 25], peak_mem_mb=0.5,
            ),
        ]
        text = benchmark_transcribers.render_table(results)
        self.assertIn("whisper", text)
        self.assertIn("faster-whisper", text)
        self.assertIn("Engine", text)
        self.assertIn("---", text)


class TestWriteReports(unittest.TestCase):
    """JSON + markdown reports must be written and parseable."""

    def test_writes_three_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            results = [
                benchmark_transcribers.BenchResult(
                    engine="whisper", available=True,
                    per_call_ms=[100.0], peak_mem_mb=1.0,
                )
            ]
            benchmark_transcribers.write_reports(results, out_dir)
            self.assertTrue((out_dir / "benchmark.json").exists())
            self.assertTrue((out_dir / "benchmark.txt").exists())
            self.assertTrue((out_dir / "benchmark.md").exists())
            data = json.loads((out_dir / "benchmark.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["results"]), 1)
            self.assertEqual(data["results"][0]["engine"], "whisper")


# ---------------------------------------------------------------------------
# Engine-level regression tests: model caches / torch.load patch
# (P1-3 / P1-4 / P2-10 — no GPU or optional deps required)
# ---------------------------------------------------------------------------
class TestFasterWhisperLRUCache(unittest.TestCase):
    """faster_whisper._MODEL_CACHE 必须是容量 2 的 LRU。"""

    def setUp(self):
        from mediascribe.transcribers import faster_whisper as fw
        self.fw = fw
        fw.clear_model_cache()
        self.addCleanup(fw.clear_model_cache)

    def _install_fake_faster_whisper(self):
        fake_mod = types.ModuleType("faster_whisper")
        created = []

        class FakeWhisperModel:
            def __init__(self, name, device=None, compute_type=None):
                created.append(name)

        fake_mod.WhisperModel = FakeWhisperModel
        return fake_mod, created

    def test_lru_evicts_oldest_beyond_capacity(self):
        fw = self.fw
        fake_mod, created = self._install_fake_faster_whisper()
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_mod}):
            fw._get_cached_model("tiny", "cpu", "int8")
            fw._get_cached_model("small", "cpu", "int8")
            # 命中 tiny → tiny 变为最近使用，small 成为最久未用
            fw._get_cached_model("tiny", "cpu", "int8")
            fw._get_cached_model("base", "cpu", "int8")  # 挤出 small
            self.assertEqual(
                set(fw._MODEL_CACHE),
                {("tiny", "cpu", "int8"), ("base", "cpu", "int8")},
            )
            self.assertEqual(created, ["tiny", "small", "base"])
            # 命中不重复加载
            fw._get_cached_model("tiny", "cpu", "int8")
            self.assertEqual(created, ["tiny", "small", "base"])
        self.assertEqual(len(fw._MODEL_CACHE), 2)

    def test_clear_model_cache(self):
        fw = self.fw
        fake_mod, _created = self._install_fake_faster_whisper()
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_mod}):
            fw._get_cached_model("tiny", "cpu", "int8")
        fw.clear_model_cache()
        self.assertEqual(len(fw._MODEL_CACHE), 0)


class TestWhisperXModelCache(unittest.TestCase):
    """whisperx 主模型/对齐模型必须跨转录调用复用，不再每次重载。"""

    def test_models_reused_across_calls(self):
        from mediascribe.transcribers import whisperx as wx
        wx.clear_model_cache()
        self.addCleanup(wx.clear_model_cache)

        fake_whisperx = mock.MagicMock()
        fake_model = mock.MagicMock()
        fake_model.transcribe.return_value = {
            "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}],
            "language": "zh",
        }
        fake_whisperx.load_model.return_value = fake_model
        fake_whisperx.load_audio.return_value = "fake-audio"
        fake_whisperx.load_align_model.return_value = ("align-model", {"d": 1})
        fake_whisperx.align.side_effect = (
            lambda segments, align_model, meta, audio, device: {
                "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}],
                "language": "zh",
            }
        )

        tx = wx.WhisperXTranscriber(model="small", device="cpu")
        with mock.patch.dict(sys.modules, {"whisperx": fake_whisperx}):
            r1 = tx.transcribe(Path("a.wav"), language="zh")
            r2 = tx.transcribe(Path("b.wav"), language="zh")

        # 同一实例连转两个音频：主模型与对齐模型各只加载一次
        self.assertEqual(fake_whisperx.load_model.call_count, 1)
        self.assertEqual(fake_whisperx.load_align_model.call_count, 1)
        self.assertEqual(r1["text"], "hi")
        self.assertEqual(r2["text"], "hi")
        self.assertEqual(len(wx._MODEL_CACHE), 1)
        self.assertEqual(len(wx._ALIGN_MODEL_CACHE), 1)
        wx.clear_model_cache()
        self.assertEqual(len(wx._MODEL_CACHE), 0)
        self.assertEqual(len(wx._ALIGN_MODEL_CACHE), 0)


class TestWhisperTorchLoadPatch(unittest.TestCase):
    """P2-10: torch.load 只在模型加载窗口内被临时替换，不污染全局。"""

    def test_patch_is_scoped_and_restored(self):
        import torch

        from mediascribe.transcribers import whisper as wmod

        original = torch.load
        try:
            with wmod._whisper_load_patch():
                self.assertIsNot(torch.load, original)
            self.assertIs(torch.load, original)  # 退出后恢复
        finally:
            torch.load = original

    def test_weights_only_true_falls_back_to_false(self):
        import torch

        from mediascribe.transcribers import whisper as wmod

        calls = []

        def fake_load(*args, **kwargs):
            calls.append(kwargs.get("weights_only"))
            if kwargs.get("weights_only", False):
                raise RuntimeError("weights_only pickle failed")
            return "ok"

        original = torch.load
        torch.load = fake_load
        try:
            with wmod._whisper_load_patch():
                self.assertEqual(torch.load(b"f", weights_only=True), "ok")
            # 先 weights_only=True 尝试，失败回退 False（仅限窗口内）
            self.assertEqual(calls, [True, False])
        finally:
            torch.load = original


if __name__ == "__main__":
    unittest.main()
