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
    budget_total_mb,
    get_vram_ledger,
    reprobe_budget_if_failed,
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
        patcher = mock.patch("mediascribe.gpu_budget.budget_total_mb", return_value=total)
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
        with mock.patch("mediascribe.gpu_budget.budget_total_mb", return_value=None):
            self.assertEqual(led.slot_for(_FakeTranscriber(), None), 0)

    def test_slot_uses_estimate_table(self):
        led = VramLedger()
        with mock.patch("mediascribe.gpu_budget.budget_total_mb", return_value=8192):
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


# ---------------------------------------------------------------------------
# P1-4(2026-11-20 架构评审): 驻留模型注册表 — 跨引擎缓存族显存预算
# ---------------------------------------------------------------------------
from mediascribe.gpu_budget import (  # noqa: E402
    clear_other_families,
    ensure_resident_capacity,
    register_family_clear,
    register_resident,
    reset_resident_registry,
    resident_total_mb,
    unregister_family,
    unregister_resident,
)


class TestResidentRegistry(unittest.TestCase):
    def setUp(self):
        reset_resident_registry()
        patcher = mock.patch("mediascribe.gpu_budget.budget_total_mb", return_value=2000)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(reset_resident_registry)

    def test_register_unregister_total(self):
        register_resident("faster-whisper", ("small", "cuda", "int8_float16"), 614)
        register_resident("whisper", ("small", "cuda"), 1965)
        self.assertEqual(resident_total_mb(), 2579)
        unregister_resident("faster-whisper", ("small", "cuda", "int8_float16"))
        self.assertEqual(resident_total_mb(), 1965)
        unregister_family("whisper")
        self.assertEqual(resident_total_mb(), 0)

    def test_ensure_fits_without_clearing(self):
        cleared = []
        register_resident("faster-whisper", ("k1",), 614)
        register_family_clear("faster-whisper", lambda: cleared.append("fw"))
        # 614 + 1000 ≤ 2000 → 不清任何家族
        self.assertTrue(ensure_resident_capacity("faster-whisper", 1000))
        self.assertEqual(cleared, [])

    def test_ensure_clears_other_families(self):
        cleared = []

        def whisper_clear():
            unregister_family("whisper")
            cleared.append("whisper")

        register_resident("whisper", ("small", "cuda"), 1965)
        register_family_clear("whisper", whisper_clear)
        # 1965 + 1000 > 2000 → 清 whisper 家族(fw 自族不动)
        self.assertTrue(ensure_resident_capacity("faster-whisper", 1000))
        self.assertEqual(cleared, ["whisper"])
        self.assertEqual(resident_total_mb(), 0)

    def test_ensure_never_clears_own_family(self):
        cleared = []
        register_resident("faster-whisper", ("k",), 1500)
        register_family_clear("faster-whisper", lambda: cleared.append("fw"))
        # 1500 + 1000 > 2000,无其它家族可清 → 返回 False(fail-open 由任务账本兜底),
        # 本族钩子不得被调(引擎在本族缓存锁内调用,清本族会死锁)
        self.assertFalse(ensure_resident_capacity("faster-whisper", 1000))
        self.assertEqual(cleared, [])
        self.assertEqual(resident_total_mb(), 1500)

    def test_clear_other_families_swallows_hook_errors(self):
        def broken():
            raise RuntimeError("boom")

        register_resident("whisper", ("k",), 100)
        register_family_clear("whisper", broken)
        self.assertEqual(clear_other_families("faster-whisper"), 0)

    def test_fail_open_when_no_budget(self):
        register_resident("faster-whisper", ("k",), 1500)
        with mock.patch("mediascribe.gpu_budget.budget_total_mb", return_value=None):
            self.assertTrue(ensure_resident_capacity("faster-whisper", 999999))


class TestReprobeBudget(unittest.TestCase):
    """R-3(2026-11-21 架构评审): 首探失败不终身失闸 —— 空闲时有限重探。"""

    def tearDown(self):
        reset_vram_ledger()

    def test_reprobe_updates_cache_on_success(self):
        reset_vram_ledger()
        with mock.patch("mediascribe.gpu_budget._probe_budget_mb", return_value=None):
            self.assertIsNone(budget_total_mb())  # 首探失败
            self.assertIsNone(reprobe_budget_if_failed(min_interval_s=0))
        with mock.patch("mediascribe.gpu_budget._probe_budget_mb", return_value=4096):
            self.assertEqual(reprobe_budget_if_failed(min_interval_s=0), 4096)
        self.assertEqual(budget_total_mb(), 4096)

    def test_reprobe_respects_min_interval(self):
        reset_vram_ledger()
        with mock.patch("mediascribe.gpu_budget._probe_budget_mb", return_value=None):
            self.assertIsNone(budget_total_mb())  # 首探失败,记时间戳
            reprobe_budget_if_failed(min_interval_s=0)
        with mock.patch("mediascribe.gpu_budget._probe_budget_mb", return_value=4096) as p:
            self.assertIsNone(reprobe_budget_if_failed(min_interval_s=9999))
            p.assert_not_called()

    def test_slot_for_arms_gate_after_reprobe_when_idle(self):
        reset_vram_ledger()
        ledger = get_vram_ledger()
        with mock.patch("mediascribe.gpu_budget._probe_budget_mb", return_value=None):
            self.assertEqual(ledger.slot_for(_FakeTranscriber(), None), 0)  # 首探失败
        import mediascribe.gpu_budget as gb

        gb._LAST_PROBE_TS = 0.0  # 老化时间戳,绕过重探限频
        with mock.patch("mediascribe.gpu_budget._probe_budget_mb", return_value=4096):
            self.assertGreater(ledger.slot_for(_FakeTranscriber(), None), 0)
        snap = ledger.snapshot()
        self.assertTrue(snap["enabled"])
        self.assertEqual(snap["total_mb"], 4096)

    def test_no_reprobe_while_tasks_in_flight(self):
        """有 in-flight slot 时禁止重探 —— 账实一致。"""
        reset_vram_ledger()
        ledger = get_vram_ledger()
        with mock.patch("mediascribe.gpu_budget._probe_budget_mb", return_value=None):
            self.assertEqual(ledger.slot_for(_FakeTranscriber(), None), 0)  # 首探失败
        import mediascribe.gpu_budget as gb

        ledger._active = 2
        try:
            gb._LAST_PROBE_TS = 0.0
            with mock.patch("mediascribe.gpu_budget._probe_budget_mb", return_value=4096) as p:
                self.assertEqual(ledger.slot_for(_FakeTranscriber(), None), 0)
                p.assert_not_called()
        finally:
            ledger._active = 0
