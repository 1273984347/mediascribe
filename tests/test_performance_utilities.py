"""
Unit tests for the performance utilities.
"""

import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
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
        # 0.01s 真睡在任何平台只会睡更久(计时器粒度), 记录值必为正;
        # 阈值断言交给合成数据测试(见 test_from_registry), 这里只锁
        # 「装饰器真的记录了时长」这个行为, 免受 runner 速度影响。
        self.assertGreater(STEP_TIMES["test_sleep"][-1], 0.0)

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
        """聚合逻辑用**合成数据**直测(2026-10-29 修复)。

        原实现靠 ``sleep(0.001) vs sleep(0.002)`` 的真实时长比较比值,
        macOS matrix(计时器合并/调度抖动)上两次实测翻转(v3.4.0a1
        发布验证 py3.12:0.00463 not greater than 0.00505)。时长记录
        行为由 test_records_duration 覆盖, 这里只测 from_registry 的
        统计口径, 注入确定值即无 runner 差异。
        """
        from mediascribe.performance import (
            STEP_TIMES,
            PerformanceReport,
            clear_step_times,
        )

        clear_step_times()
        STEP_TIMES["alpha"] = [0.001, 0.003]  # mean = 0.002
        STEP_TIMES["beta"] = [0.01]  # total = 0.01
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

        # Multiple workers should make 4 sleeps of 0.05s finish faster
        # than the serial 0.20s would.
        #
        # v3.4.0a1 发布验证实测:macOS matrix runner 上 executor 首建 +
        # 调度抖动让绝对阈值 0.18s 误报(实测 0.1997s)。改为
        # ① 先空跑一次吸收建池成本 ② 同环境内相对比较 —— 真串行实现
        # ≈ 串行基线必挂, 并发实现在任何 runner 速度下都有大裕量。
        parallel_map(lambda x: None, range(4), max_workers=4)  # warm-up

        t0 = time.perf_counter()
        parallel_map(lambda x: time.sleep(0.05), range(4), max_workers=4)
        parallel_dur = time.perf_counter() - t0

        t0 = time.perf_counter()
        for _ in range(4):
            time.sleep(0.05)
        serial_dur = time.perf_counter() - t0

        self.assertLess(
            parallel_dur,
            serial_dur * 0.8,
            f"parallel map should beat serial: parallel={parallel_dur:.3f}s "
            f"serial={serial_dur:.3f}s",
        )


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


###########################################################################
# 迁入自 tests/test_coverage_gaps.py —— TestPerformanceExtras(mediascribe.performance)
###########################################################################


class TestPerformanceExtras:
    """Hit to_markdown, write, parallel_map edge cases."""

    def test_to_markdown_empty(self):
        from mediascribe.performance import PerformanceReport

        rep = PerformanceReport()
        assert "_No steps recorded._" in rep.to_markdown()

    def test_to_markdown_with_steps(self):
        from mediascribe.performance import (
            STEP_TIMES,
            PerformanceReport,
            clear_step_times,
        )

        clear_step_times()
        STEP_TIMES["download"] = [0.1, 0.2, 0.05]
        STEP_TIMES["transcribe"] = [1.5]
        try:
            rep = PerformanceReport.from_registry()
            md = rep.to_markdown()
            assert "| Step | Count |" in md
            assert "download" in md
            assert "transcribe" in md
            assert "Total:" in md
            # Round-trip via dict
            d = rep.to_dict()
            assert d["steps"]["download"]["count"] == 3
        finally:
            clear_step_times()

    def test_performance_report_write(self, tmp_path):
        from mediascribe.performance import (
            STEP_TIMES,
            PerformanceReport,
            clear_step_times,
        )

        clear_step_times()
        STEP_TIMES["x"] = [0.01]
        try:
            rep = PerformanceReport.from_registry()
            out = tmp_path / "perf.json"
            rep.write(out)
            data = json.loads(out.read_text(encoding="utf-8"))
            assert data["steps"]["x"]["count"] == 1
        finally:
            clear_step_times()

    def test_parallel_map_empty(self):
        from mediascribe.performance import parallel_map

        assert parallel_map(lambda x: x * 2, []) == []

    def test_parallel_map_order_preserved(self):
        from mediascribe.performance import parallel_map

        out = parallel_map(lambda x: x * 2, [1, 2, 3, 4], max_workers=2)
        assert out == [2, 4, 6, 8]

    def test_parallel_map_exception_captured(self):
        from mediascribe.performance import parallel_map

        def boom(x):
            if x == 2:
                raise ValueError("nope")
            return x

        out = parallel_map(boom, [1, 2, 3], max_workers=2)
        assert out[0] == 1
        assert isinstance(out[1], ValueError)
        assert out[2] == 3

    def test_download_cache_round_trip(self, tmp_path):
        from mediascribe.performance import DownloadCache

        src = tmp_path / "src.txt"
        src.write_text("hi", encoding="utf-8")
        # Pre-create the cache dir so ``shutil.copy2`` has a destination.
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache = DownloadCache(base=cache_dir)
        assert cache.get("http://x/a") is None  # miss
        dst = cache.put("http://x/a", src)
        assert dst.exists()
        assert cache.get("http://x/a") == dst  # hit
        stats = cache.stats()
        assert stats["hits"] >= 1 and stats["misses"] >= 1
        cache.clear()


if __name__ == "__main__":
    unittest.main()
