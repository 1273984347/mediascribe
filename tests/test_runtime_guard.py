"""
运行时资源守卫（``mediascribe.runtime_guard``）的单元测试。

覆盖：
1. **转录前降档** —— 显存不足 / 内存不足 / Apple Silicon / 内存压力换引擎
2. **降档阶梯不循环** —— 同族模型只降一次（实测踩过 large-v3→large-v2→large-v2…）
3. **通知必须可操作** —— 降档要告诉用户「降了什么、为什么」
4. **转录中监测** —— 紧急线触发通知 + 请求取消；过载后不交付结果
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mediascribe.runtime_guard import (  # noqa: E402
    MEMORY_PRESSURE_TOTAL_MB,
    MODEL_LADDER,
    DegradeDecision,
    HardwareSnapshot,
    ResourcePressureError,
    RuntimeMonitor,
    _lower_model,
    plan,
    snapshot,
)


def _snap(**kw) -> HardwareSnapshot:
    base = {
        "cuda_available": True,
        "gpu_name": "RTX 4060",
        "vram_total_mb": 8188,
        "vram_used_mb": 1000,
        "ram_total_mb": 16000,
        "ram_available_mb": 9000,
        "cpu_cores": 14,
    }
    base.update(kw)
    return HardwareSnapshot(**base)


class TestModelLadder(unittest.TestCase):
    """降档阶梯：按族定位，同族只降一次。"""

    def test_ladder_has_no_duplicate_families(self):
        from mediascribe.runtime_guard import _MODEL_FAMILY

        families = [_MODEL_FAMILY.get(m, m) for m in MODEL_LADDER]
        self.assertEqual(
            len(families),
            len(set(families)),
            msg=f"阶梯里有同族重复项会致死循环: {MODEL_LADDER}",
        )

    def test_lower_model_walks_down(self):
        self.assertEqual(_lower_model("large-v3"), "medium")
        self.assertEqual(_lower_model("medium"), "small")
        self.assertEqual(_lower_model("small"), "base")
        self.assertEqual(_lower_model("base"), "tiny")
        self.assertIsNone(_lower_model("tiny"))

    def test_lower_model_treats_large_variants_as_same_family(self):
        """large / large-v2 / large-v3 是同一族 ⇒ 必须降成同一档。"""
        for m in ("large", "large-v2", "large-v3", "distil-large-v3"):
            self.assertEqual(_lower_model(m), "medium", msg=f"{m} 应降档到 medium")

    def test_lower_model_terminates(self):
        """从任意档位连续降档，必须在有限步内到 tiny。"""
        model = "large-v3"
        seen = set()
        steps = 0
        while True:
            nxt = _lower_model(model)
            if nxt is None:
                break
            self.assertNotIn(nxt, seen, msg=f"降档出现环: {nxt}")
            seen.add(nxt)
            model = nxt
            steps += 1
            self.assertLess(steps, 10, "降档步数异常")
        self.assertEqual(model, "tiny")


class TestPlanDegrade(unittest.TestCase):
    """转录前的降档决策。"""

    def test_no_change_when_resources_sufficient(self):
        d = plan("faster-whisper", "large-v3", "cuda", _snap())
        self.assertFalse(d.changed)
        self.assertEqual(d.engine, "faster-whisper")
        self.assertEqual(d.model, "large-v3")
        self.assertEqual(d.notices, [])

    def test_vram_shortfall_degrades_model(self):
        snap = _snap(vram_total_mb=4096, vram_used_mb=1000)
        d = plan("faster-whisper", "large-v3", "cuda", snap)
        self.assertTrue(d.changed)
        self.assertNotEqual(d.model, "large-v3")
        # locale 无关断言：en/zh 文案都含 "MiB"，稳定信号看 reasons
        self.assertTrue(any("MiB" in n for n in d.notices), msg=d.notices)
        self.assertIn("model-degraded", d.reasons)

    def test_memory_shortfall_degrades_model(self):
        snap = _snap(cuda_available=False, ram_available_mb=700)
        d = plan("faster-whisper", "large-v3", "cpu", snap)
        self.assertTrue(d.changed)
        self.assertNotEqual(d.model, "large-v3")

    def test_apple_silicon_switches_engine(self):
        """faster-whisper 无 Metal 后端（实测 ctranslate2 抛错）⇒ 必须换引擎。"""
        snap = _snap(cuda_available=False, is_apple_silicon=True, ram_available_mb=9000)
        with mock.patch("mediascribe.runtime_guard._whisper_cpp_available", return_value=True):
            d = plan("faster-whisper", "large-v3", "cpu", snap)
        self.assertTrue(d.changed)
        self.assertNotEqual(d.engine, "faster-whisper")
        self.assertTrue(any("Apple Silicon" in n for n in d.notices), msg=d.notices)

    def test_memory_pressure_switches_to_whisper_cpp(self):
        """CPU + 内存紧张 ⇒ 换 whisper.cpp（实测最省内存）。"""
        # 用**总内存**判定（硬件能力），不是瞬时可用内存
        snap = _snap(cuda_available=False, ram_total_mb=MEMORY_PRESSURE_TOTAL_MB - 1024)
        with mock.patch("mediascribe.runtime_guard._whisper_cpp_available", return_value=True):
            d = plan("faster-whisper", "small", "cpu", snap)
        self.assertEqual(d.engine, "whisper-cpp")

    def test_engine_switch_requires_whisper_cpp_binary(self):
        """P0-2：whisper.cpp 二进制不可用时**不得**切引擎 —— 换到没有
        二进制的引擎会把原本能跑的配置变成转录期硬失败。"""
        snap = _snap(cuda_available=False, ram_total_mb=MEMORY_PRESSURE_TOTAL_MB - 1024)
        with mock.patch("mediascribe.runtime_guard._whisper_cpp_available", return_value=False):
            d = plan("faster-whisper", "small", "cpu", snap)
        self.assertEqual(d.engine, "faster-whisper")
        self.assertTrue(any("whisper.cpp" in n for n in d.notices), msg=d.notices)
        self.assertIn("memory-pressure-no-cpp-binary", d.reasons)

    def test_apple_silicon_without_cpp_keeps_engine_and_notifies(self):
        """Apple Silicon + faster-whisper + 无 whisper.cpp ⇒ 保留原引擎，
        但通知必须说清后果与预置方式。"""
        snap = _snap(cuda_available=False, is_apple_silicon=True, ram_available_mb=9000)
        with mock.patch("mediascribe.runtime_guard._whisper_cpp_available", return_value=False):
            d = plan("faster-whisper", "large-v3", "cpu", snap)
        self.assertEqual(d.engine, "faster-whisper")
        joined = " ".join(d.notices)
        self.assertIn("whisper.cpp", joined)
        self.assertIn("docs/engines.md", joined)
        self.assertIn("apple-silicon-no-metal-no-cpp-binary", d.reasons)

    def test_notices_are_actionable(self):
        """降档通知必须说清「降了什么、为什么」—— 用户要能据此行动。"""
        snap = _snap(vram_total_mb=4096, vram_used_mb=1000)
        d = plan("faster-whisper", "large-v3", "cuda", snap)
        self.assertTrue(d.notices)
        joined = " ".join(d.notices)
        self.assertIn("MiB", joined)
        self.assertTrue("⇒" in joined or "->" in joined, msg=joined)

    def test_decision_serialisable(self):
        import json

        d = plan("faster-whisper", "large-v3", "cuda", _snap())
        json.dumps(d.to_dict(), ensure_ascii=False)


class TestSnapshot(unittest.TestCase):
    def test_snapshot_never_raises(self):
        snap = snapshot()
        self.assertIsInstance(snap, HardwareSnapshot)
        self.assertGreaterEqual(snap.ram_total_mb, 0)
        snap.to_dict()

    def test_vram_free_is_non_negative(self):
        snap = _snap(vram_total_mb=100, vram_used_mb=500)
        self.assertEqual(snap.vram_free_mb, 0)


class TestRuntimeMonitor(unittest.TestCase):
    """转录期间的过载监测。"""

    def test_notifies_and_requests_cancel_on_pressure(self):
        cancel = threading.Event()
        notices = []
        mon = RuntimeMonitor(cancel_event=cancel, on_notice=notices.append, interval_s=0.05)
        with mock.patch.object(mon, "_sample", return_value="sampled abort reason"):
            mon.start()
            for _ in range(50):
                if mon.pressured:
                    break
                threading.Event().wait(0.02)
            mon.stop()
        self.assertTrue(mon.pressured)
        self.assertTrue(cancel.is_set(), msg="紧急过载应请求取消")
        self.assertTrue(notices, msg="必须通知用户")
        self.assertIn("abort reason", mon.pressure_reason)

    def test_no_pressure_keeps_cancel_untouched(self):
        cancel = threading.Event()
        mon = RuntimeMonitor(cancel_event=cancel, interval_s=0.05)
        with mock.patch.object(mon, "_sample", return_value=None):
            mon.start()
            threading.Event().wait(0.2)
            mon.stop()
        self.assertFalse(mon.pressured)
        self.assertFalse(cancel.is_set())

    def test_on_notice_exception_does_not_break(self):
        def boom(_msg):
            raise RuntimeError("通知通道炸了")

        mon = RuntimeMonitor(on_notice=boom, interval_s=0.05)
        with mock.patch.object(mon, "_sample", return_value="x"):
            mon.start()
            threading.Event().wait(0.15)
            mon.stop()
        self.assertTrue(mon.pressured)

    def test_pressure_error_is_runtime_error(self):
        """过载异常必须是 RuntimeError 的子类（上层按 RuntimeError 捕获）。"""
        self.assertTrue(issubclass(ResourcePressureError, RuntimeError))


class TestPipelineIntegration(unittest.TestCase):
    """与 pipeline 的接线：降档要落到 settings，通知要能进 metadata。"""

    def test_pipeline_uses_guard_plan(self):
        from mediascribe.config import Settings
        from mediascribe.pipeline import Pipeline

        snap = _snap(vram_total_mb=4096, vram_used_mb=1000)
        settings = Settings(model="large-v3", engine="faster-whisper", device="cuda")
        with mock.patch(
            "mediascribe.pipeline.plan_resources",
            return_value=DegradeDecision(
                engine="faster-whisper",
                model="medium",
                device="cuda",
                changed=True,
                notices=["测试降档说明"],
            ),
        ):
            p = Pipeline(settings)
        self.assertEqual(settings.model, "medium")
        self.assertIn("测试降档说明", p._resource_notices)

    def test_context_carries_resource_notices(self):
        from mediascribe.config import Settings
        from mediascribe.pipeline_stages import PipelineContext

        ctx = PipelineContext(settings=Settings(), source_input="x", resource_notices=["一条说明"])
        self.assertEqual(ctx.resource_notices, ["一条说明"])


if __name__ == "__main__":
    unittest.main()
