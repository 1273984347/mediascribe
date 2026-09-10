"""
Unit tests for the performance utilities.
"""

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


class TestProfileStep(unittest.TestCase):
    def test_records_duration(self):
        from mediascribe.performance import STEP_TIMES, clear_step_times, profile_step

        clear_step_times()

        @profile_step("test_sleep")
        def slow():
            time.sleep(0.01)
            return "ok"

        result = slow()
        self.assertEqual(result, "ok")
        self.assertIn("test_sleep", STEP_TIMES)
        self.assertGreater(STEP_TIMES["test_sleep"][-1], 0.005)

    def test_records_on_exception(self):
        from mediascribe.performance import STEP_TIMES, clear_step_times, profile_step

        clear_step_times()

        @profile_step("test_boom")
        def bad():
            raise RuntimeError("kaboom")

        with self.assertRaises(RuntimeError):
            bad()
        self.assertEqual(len(STEP_TIMES["test_boom"]), 1)


class TestPerformanceReport(unittest.TestCase):
    def test_from_registry(self):
        from mediascribe.performance import (
            STEP_TIMES,
            PerformanceReport,
            clear_step_times,
            profile_step,
        )

        clear_step_times()

        @profile_step("alpha")
        def a():
            time.sleep(0.001)

        @profile_step("beta")
        def b():
            time.sleep(0.002)

        a()
        a()
        b()
        report = PerformanceReport.from_registry()
        self.assertIn("alpha", report.steps)
        self.assertIn("beta", report.steps)
        self.assertEqual(report.steps["alpha"]["count"], 2)
        self.assertEqual(report.steps["beta"]["count"], 1)
        self.assertGreater(report.steps["beta"]["total_sec"], report.steps["alpha"]["mean_sec"])

    def test_markdown_output(self):
        from mediascribe.performance import PerformanceReport, clear_step_times, profile_step

        clear_step_times()

        @profile_step("x")
        def x():
            time.sleep(0.001)

        x()
        md = PerformanceReport.from_registry().to_markdown()
        self.assertIn("| Step |", md)
        self.assertIn("x", md)
        self.assertIn("Total:", md)

    def test_write_json(self):
        import json
        import tempfile

        from mediascribe.performance import PerformanceReport, clear_step_times, profile_step

        clear_step_times()

        @profile_step("y")
        def y():
            time.sleep(0.001)

        y()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "perf.json"
            PerformanceReport.from_registry().write(p)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertIn("y", data["steps"])


class TestParallelMap(unittest.TestCase):
    def test_empty_input(self):
        from mediascribe.performance import parallel_map

        self.assertEqual(parallel_map(lambda x: x * 2, []), [])

    def test_preserves_order(self):
        from mediascribe.performance import parallel_map

        out = parallel_map(lambda x: x * 2, [1, 2, 3, 4])
        self.assertEqual(out, [2, 4, 6, 8])

    def test_thread_pool_used(self):
        from mediascribe.performance import parallel_map

        # Multiple workers should make 4 sleeps of 0.05s finish
        # faster than the serial 0.20s would.
        t0 = time.perf_counter()
        parallel_map(lambda x: time.sleep(0.05), range(4), max_workers=4)
        dur = time.perf_counter() - t0
        self.assertLess(dur, 0.18, f"parallel map should be faster than serial: {dur}")


class TestRunScopedTimings(unittest.TestCase):
    """P2-7: per-run 计时注册表 — 并发 run 经由 contextvars 隔离,
    全局 ``STEP_TIMES`` 与无 run 上下文的调用方行为保持不变。"""

    def setUp(self):
        from mediascribe.performance import clear_step_times

        clear_step_times()

    def tearDown(self):
        from mediascribe.performance import clear_step_times

        clear_step_times()

    def test_run_registry_isolated_from_global(self):
        from mediascribe.performance import (
            STEP_TIMES,
            begin_run_registry,
            end_run_registry,
            get_step_times,
            profile_step,
        )

        @profile_step("iso")
        def f():
            return "ok"

        reg = begin_run_registry()
        self.assertEqual(f(), "ok")
        # 记录进 run 注册表,不污染全局
        self.assertIn("iso", reg)
        self.assertNotIn("iso", STEP_TIMES)
        self.assertIn("iso", get_step_times())
        end_run_registry()
        # 脱离 run 上下文后回落全局,run 计时不再可见
        self.assertNotIn("iso", get_step_times())
        self.assertEqual(STEP_TIMES, {})

    def test_second_run_gets_fresh_registry(self):
        """连续两次 profile run,第二次的计数不叠加第一次。"""
        from mediascribe.performance import (
            begin_run_registry,
            end_run_registry,
            get_step_times,
            profile_step,
        )

        @profile_step("iso2")
        def f():
            time.sleep(0.001)

        begin_run_registry()
        f()
        f()
        first = get_step_times()
        self.assertEqual(len(first["iso2"]), 2)

        begin_run_registry()  # 第二个 run
        second = get_step_times()
        self.assertEqual(second, {})
        end_run_registry()
        end_run_registry()

    def test_clear_step_times_detaches_run_registry(self):
        from mediascribe.performance import (
            STEP_TIMES,
            begin_run_registry,
            clear_step_times,
            get_step_times,
            profile_step,
        )

        @profile_step("iso3")
        def f():
            pass

        begin_run_registry()
        clear_step_times()  # 回到全局模式
        f()
        self.assertIn("iso3", STEP_TIMES)
        self.assertIn("iso3", get_step_times())


class TestDownloadCache(unittest.TestCase):
    def test_put_and_get(self):
        from mediascribe.performance import DownloadCache

        with __import__("tempfile").TemporaryDirectory() as td:
            src = Path(td) / "source.txt"
            src.write_text("hi", encoding="utf-8")
            cache = DownloadCache()
            try:
                # First call: miss
                self.assertIsNone(cache.get("https://example.com/a"))
                cached = cache.put("https://example.com/a", src)
                self.assertTrue(cached.exists())
                # Second put on same URL returns the same cached path
                cached2 = cache.put("https://example.com/a", src)
                self.assertEqual(cached, cached2)
                # Subsequent get: hit
                self.assertEqual(cache.get("https://example.com/a"), cached)
                self.assertEqual(cache.stats()["hits"], 1)
                self.assertEqual(cache.stats()["misses"], 1)
            finally:
                cache.clear()


if __name__ == "__main__":
    unittest.main()
