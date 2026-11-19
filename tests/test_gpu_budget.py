"""P0-1(2026-11-20 架构评审)— :mod:`mediascribe.gpu_budget` 显存账本单位测试。

覆盖:
1. 预算探测:env 覆盖优先 / 非 CUDA fail-open / free==0 放开
2. acquire/release 记账:不足阻塞、释放唤醒、超额 clamp 永不死锁
3. 等待期间取消(cancel_event 置位)→ 返回 False
4. slot_for:闸门未启用返 0 / 实测表按 (engine, model, device) 取值
5. 单例与 reset
"""

from __future__ import annotations

import threading
import unittest
from unittest import mock

from mediascribe.gpu_budget import (
    VramLedger,
    _estimate_task_vram_mb,
    _probe_budget_mb,
    get_vram_ledger,
    reset_vram_ledger,
)


class _FakeTranscriber:
    """带 name/model_name/device 属性的转录器桩(模拟 faster-whisper 实例)。"""

    def __init__(self, name="faster-whisper", model_name="small", device="cuda"):
        self.name = name
        self.model_name = model_name
        self.device = device

    def transcribe(self, audio_path, **kwargs):  # pragma: no cover - 桩
        return {}


class TestProbeBudget(unittest.TestCase):
    def test_env_override_wins(self):
        with mock.patch.dict("os.environ", {"MEDIASCRIBE_VRAM_BUDGET_MB": "4096"}):
            self.assertEqual(_probe_budget_mb(), 4096)

    def test_invalid_env_ignored(self):
        with mock.patch.dict("os.environ", {"MEDIASCRIBE_VRAM_BUDGET_MB": "abc"}):
            with mock.patch(
                "mediascribe.pipeline.gpu_health",
                return_value={"available": False, "device": "cpu"},
            ):
                self.assertIsNone(_probe_budget_mb())

    def test_non_cuda_fails_open(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os as _os

            _os.environ.pop("MEDIASCRIBE_VRAM_BUDGET_MB", None)
            with mock.patch(
                "mediascribe.pipeline.gpu_health",
                return_value={"available": True, "device": "cpu"},
            ):
                self.assertIsNone(_probe_budget_mb())

    def test_free_vram_snapshot(self):
        import os as _os

        _os.environ.pop("MEDIASCRIBE_VRAM_BUDGET_MB", None)
        with mock.patch(
            "mediascribe.pipeline.gpu_health",
            return_value={
                "available": True,
                "device": "cuda",
                "vram_total_mb": 8192,
                "vram_used_mb": 2048,
            },
        ):
            self.assertEqual(_probe_budget_mb(), 6144)

    def test_zero_free_fails_open(self):
        import os as _os

        _os.environ.pop("MEDIASCRIBE_VRAM_BUDGET_MB", None)
        with mock.patch(
            "mediascribe.pipeline.gpu_health",
            return_value={
                "available": True,
                "device": "cuda",
                "vram_total_mb": 8192,
                "vram_used_mb": 8192,
            },
        ):
            self.assertIsNone(_probe_budget_mb())


class TestAcquireRelease(unittest.TestCase):
    def _ledger_with_budget(self, total):
        led = VramLedger()
        patcher = mock.patch("mediascribe.gpu_budget._probe_budget_mb", return_value=total)
        patcher.start()
        self.addCleanup(patcher.stop)
        return led

    def test_accounting(self):
        led = self._ledger_with_budget(1000)
        self.assertTrue(led.acquire(600))
        self.assertEqual(led.snapshot()["available_mb"], 400)
        led.release(600)
        self.assertEqual(led.snapshot()["available_mb"], 1000)

    def test_acquire_blocks_until_release(self):
        led = self._ledger_with_budget(1000)
        self.assertTrue(led.acquire(800))
        done = threading.Event()

        def worker():
            led.acquire(600)
            done.set()

        t = threading.Thread(target=worker)
        t.start()
        try:
            # 预算不足,worker 仍在等
            self.assertFalse(done.wait(0.3))
            led.release(800)
            self.assertTrue(done.wait(2.0))
        finally:
            t.join(timeout=3.0)

    def test_acquire_returns_false_when_cancelled(self):
        led = self._ledger_with_budget(1000)
        self.assertTrue(led.acquire(900))
        evt = threading.Event()
        evt.set()
        self.assertFalse(led.acquire(500, cancel_event=evt))

    def test_oversized_request_clamped_never_deadlocks(self):
        led = self._ledger_with_budget(500)
        self.assertTrue(led.acquire(99999))
        self.assertEqual(led.snapshot()["available_mb"], 0)
        led.release(99999)
        self.assertEqual(led.snapshot()["available_mb"], 500)

    def test_release_before_init_is_noop(self):
        led = VramLedger()
        led.release(100)  # 不得抛错
        self.assertFalse(led.snapshot()["enabled"])


class TestSlotFor(unittest.TestCase):
    def test_slot_zero_when_no_budget(self):
        led = VramLedger()
        with mock.patch("mediascribe.gpu_budget._probe_budget_mb", return_value=None):
            self.assertEqual(led.slot_for(_FakeTranscriber(), None), 0)

    def test_slot_uses_estimate_table(self):
        led = VramLedger()
        with mock.patch("mediascribe.gpu_budget._probe_budget_mb", return_value=8192):
            # runtime_guard 实测表:faster-whisper large-v3 cuda = 2816
            mb = led.slot_for(_FakeTranscriber(model_name="large-v3", device="cuda"), None)
            self.assertEqual(mb, 2816)

    def test_estimate_falls_back_for_unknown_keys(self):
        # 未知 (engine, model, device) 组合 → 保守默认(不得抛 KeyError)
        mb = _estimate_task_vram_mb(_FakeTranscriber(name="chunked"), None)
        self.assertGreater(mb, 0)


class TestSingleton(unittest.TestCase):
    def test_same_instance_until_reset(self):
        reset_vram_ledger()
        a = get_vram_ledger()
        b = get_vram_ledger()
        self.assertIs(a, b)
        reset_vram_ledger()
        self.assertIsNot(get_vram_ledger(), a)


if __name__ == "__main__":
    unittest.main()
