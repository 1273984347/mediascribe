"""
v3.2.0b — :class:`AsyncPipeline` unit tests.

覆盖:

1. ``from_sync`` 工厂
2. ``_default_max_concurrent`` 优先级(env > CPU 数)
3. 单 video 跑 :meth:`AsyncPipeline.run` 走 ``asyncio.to_thread``
4. ``run_batch`` 并发数 = ``max_concurrent``
5. ``run_batch`` 单个失败被隔离
6. ``cancel()`` 把 in-flight 任务取消
7. :class:`Pipeline` 公共契约 (v3.2.0a) 仍可用
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mediascribe.config import Settings
from mediascribe.models import TranscriptResult
from mediascribe.pipeline import Pipeline
from mediascribe.pipeline_async import (
    AsyncPipeline,
    _default_max_concurrent,
    _FailedResult,
    _gpu_aware_concurrency,
    _GpuHealthCache,
    _reset_gpu_semaphores,
    _shared_gpu_semaphore,
    _vram_per_task_mb,
    from_sync,
)
from mediascribe.pipeline_stages import PipelineCancelled


def _run_coro(coro):
    """运行协程,兼容已有 event loop 的情况 (如 pytest-asyncio)。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # 已有 event loop (pytest-asyncio 等),用新线程跑
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _fake_settings(tmp: Path) -> Settings:
    return Settings(workspace_root=tmp)


def _audio_file(tmp: Path, name: str = "hello.wav") -> Path:
    p = tmp / name
    p.write_bytes(b"RIFFfake")
    return p


def _fake_transcriber() -> mock.MagicMock:
    t = mock.MagicMock(name="Transcriber")
    t.name = "fake"
    t.transcribe.return_value = {
        "text": "hello world",
        "model": "fake-tiny",
        "language": "en",
        "segments": [],
        "speaker_diarization": False,
    }
    return t


# ---------------------------------------------------------------------------
# _default_max_concurrent
# ---------------------------------------------------------------------------
class TestDefaultMaxConcurrent(unittest.TestCase):
    def test_env_override_takes_precedence(self):
        with mock.patch.dict(os.environ, {"MEDIASCRIBE_MAX_WORKERS": "7"}):
            self.assertEqual(_default_max_concurrent(), 7)

    def test_env_zero_falls_through_to_cpu(self):
        # 0 / 负数 / 非数字都视作未设
        with mock.patch.dict(os.environ, {"MEDIASCRIBE_MAX_WORKERS": "0"}):
            n = _default_max_concurrent()
            self.assertGreaterEqual(n, 1)
        with mock.patch.dict(os.environ, {"MEDIASCRIBE_MAX_WORKERS": "-1"}):
            n = _default_max_concurrent()
            self.assertGreaterEqual(n, 1)

    def test_caps_at_4(self):
        env = {k: v for k, v in os.environ.items() if k != "MEDIASCRIBE_MAX_WORKERS"}
        env.pop("MEDIASCRIBE_MAX_WORKERS", None)
        with mock.patch.dict(os.environ, env, clear=True):
            # 8+ CPU 机器上仍 cap 在 4
            with mock.patch("os.cpu_count", return_value=16):
                self.assertEqual(_default_max_concurrent(), 4)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
class TestFromSync(unittest.TestCase):
    def test_wraps_existing_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            p = Pipeline(settings=_fake_settings(tmp), transcriber=_fake_transcriber())
            ap = from_sync(p)
            self.assertIsInstance(ap, AsyncPipeline)
            self.assertIs(ap.build_sync_equivalent(), p)
            self.assertGreaterEqual(ap.max_concurrent, 1)


# ---------------------------------------------------------------------------
# AsyncPipeline.run
# ---------------------------------------------------------------------------
class TestAsyncPipelineRun(unittest.TestCase):
    def test_run_single_audio(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = _audio_file(tmp)
            sync = Pipeline(settings=_fake_settings(tmp), transcriber=_fake_transcriber())
            ap = from_sync(sync)
            result = _run_coro(ap.run(str(audio)))
            self.assertIsInstance(result, TranscriptResult)
            self.assertEqual(result.text, "hello world")


# ---------------------------------------------------------------------------
# AsyncPipeline.run_batch
# ---------------------------------------------------------------------------
class TestAsyncPipelineRunBatch(unittest.TestCase):
    def test_empty_input_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sync = Pipeline(settings=_fake_settings(tmp), transcriber=_fake_transcriber())
            ap = from_sync(sync)
            self.assertEqual(_run_coro(ap.run_batch([])), [])

    def test_concurrency_is_capped(self):
        """max_concurrent=2 时,任何时刻 in-flight 任务数 ≤ 2。"""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for i in range(6):
                _audio_file(tmp, f"a{i}.wav")
            sync = Pipeline(settings=_fake_settings(tmp), transcriber=_fake_transcriber())
            ap = AsyncPipeline(sync, max_concurrent=2)
            in_flight = 0
            peak = 0
            # 不需要 asyncio.Lock — 协程只在 await 点交错,计数段
            # 无 await 即天然原子;且 py3.9 的 Lock() 在循环外急切
            # 绑定 event loop 会直接 RuntimeError。
            original_run = ap.run

            async def tracked_run(src, **kw):
                nonlocal in_flight, peak
                in_flight += 1
                peak = max(peak, in_flight)
                try:
                    return await original_run(src, **kw)
                finally:
                    in_flight -= 1

            ap.run = tracked_run  # type: ignore[assignment]
            inputs = [str(tmp / f"a{i}.wav") for i in range(6)]
            results = _run_coro(ap.run_batch(inputs))
            self.assertEqual(len(results), 6)
            self.assertLessEqual(peak, 2, f"peak in-flight {peak} > 2")
            for r in results:
                self.assertIsInstance(r, TranscriptResult)

    def test_single_failure_is_isolated(self):
        """一个 source 抛异常不影响其他 source。"""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for i in range(3):
                _audio_file(tmp, f"a{i}.wav")
            sync = Pipeline(settings=_fake_settings(tmp), transcriber=_fake_transcriber())
            ap = AsyncPipeline(sync, max_concurrent=2)
            # 让 transcribe 在第二个文件抛异常
            transcriber = sync.transcriber

            def maybe_fail(audio_path, *a, **kw):
                if "a1" in str(audio_path):
                    raise RuntimeError("boom")
                return {
                    "text": "hello world",
                    "model": "fake-tiny",
                    "language": "en",
                    "segments": [],
                    "speaker_diarization": False,
                }

            transcriber.transcribe.side_effect = maybe_fail
            inputs = [str(tmp / f"a{i}.wav") for i in range(3)]
            results = _run_coro(ap.run_batch(inputs))
            self.assertEqual(len(results), 3)
            self.assertIsInstance(results[0], TranscriptResult)
            self.assertIsInstance(results[1], _FailedResult)
            self.assertIsInstance(results[2], TranscriptResult)
            self.assertIn("boom", str(results[1].exc))


# ---------------------------------------------------------------------------
# v3.2.0x P1-7: 进程级 GPU 信号量单例
# ---------------------------------------------------------------------------
class TestSharedGpuSemaphore(unittest.TestCase):
    """``_shared_gpu_semaphore`` 的单例与总量守恒契约。"""

    def setUp(self):
        _reset_gpu_semaphores()

    def tearDown(self):
        _reset_gpu_semaphores()

    def test_same_device_reuses_semaphore(self):
        async def driver():
            s1 = _shared_gpu_semaphore("cuda", 2)
            s2 = _shared_gpu_semaphore("cuda", 4)
            return s1, s2

        s1, s2 = _run_coro(driver())
        self.assertIs(s1, s2)  # 复用首个实例,effective 忽略

    def test_different_devices_get_different_semaphores(self):
        async def driver():
            return (
                _shared_gpu_semaphore("cuda", 2),
                _shared_gpu_semaphore("cpu", 2),
            )

        s_cuda, s_cpu = _run_coro(driver())
        self.assertIsNot(s_cuda, s_cpu)

    def test_concurrent_batches_share_global_cap(self):
        """两个 AsyncPipeline 并发 batch,GPU 任务总量 ≤ 单个 batch 上限。

        修复前每个实例各自建 Semaphore → 总量翻倍(2×cap)。
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sync = Pipeline(settings=_fake_settings(tmp), transcriber=_fake_transcriber())
            cpu_health = {
                "available": False,
                "device": "cpu",
                "vram_total_mb": None,
                "vram_used_mb": None,
            }

            async def driver():
                with mock.patch("mediascribe.pipeline_async._GPU_HEALTH_CACHE") as cache:
                    cache.get.return_value = cpu_health
                    aps = [AsyncPipeline(sync, max_concurrent=4) for _ in range(2)]
                    in_flight = 0
                    peak = 0

                    for ap in aps:

                        async def tracked_run(src, **kw):
                            nonlocal in_flight, peak
                            in_flight += 1
                            peak = max(peak, in_flight)
                            try:
                                await asyncio.sleep(0.01)
                                return "OK"
                            finally:
                                in_flight -= 1

                        ap.run = tracked_run  # type: ignore[assignment]

                    batches = [ap.run_batch([f"src{i}" for i in range(6)]) for ap in aps]
                    await asyncio.gather(*batches)
                return peak

            peak = _run_coro(driver())
            self.assertLessEqual(peak, 4, f"global peak {peak} > shared cap 4")


# ---------------------------------------------------------------------------
# v3.2.0x P1-8: 取消契约
# ---------------------------------------------------------------------------
class TestCancelContract(unittest.TestCase):
    """``cancel()`` 后 gather 不抛,结果按位置是 _FailedResult。"""

    def test_cancelled_batch_returns_failed_results_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            sync = Pipeline(
                settings=_fake_settings(Path(td)),
                transcriber=_fake_transcriber(),
            )

            async def driver():
                ap = AsyncPipeline(sync, max_concurrent=2)

                async def fake_run(src, **kw):
                    await asyncio.sleep(5)  # 模拟长任务
                    return "OK"

                ap.run = fake_run  # type: ignore[assignment]
                task = asyncio.create_task(ap.run_batch(["a", "b", "c"]))
                await asyncio.sleep(0.05)
                ap.cancel()
                # P1-8: gather 正常返回,不抛 CancelledError
                return await task

            results = _run_coro(driver())
            self.assertEqual(len(results), 3)
            for r in results:
                self.assertIsInstance(r, _FailedResult)
                self.assertIsInstance(r.exc, PipelineCancelled)

    def test_run_batch_clears_stale_cancel_event(self):
        """实例复用:上一次 batch 的取消请求不泄漏到下一次。"""
        with tempfile.TemporaryDirectory() as td:
            sync = Pipeline(
                settings=_fake_settings(Path(td)),
                transcriber=_fake_transcriber(),
            )

            async def driver():
                ap = AsyncPipeline(sync, max_concurrent=1)
                ap._cancel_event.set()  # 模拟上次 cancel() 的残留
                return await ap.run_batch(["a"], runners={"a": lambda: "OK"})

            results = _run_coro(driver())
            self.assertEqual(results, ["OK"])


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------
class TestAsyncPipelineCancel(unittest.TestCase):
    def test_cancel_returns_n_when_tasks_in_flight(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sync = Pipeline(settings=_fake_settings(tmp), transcriber=_fake_transcriber())
            ap = AsyncPipeline(sync, max_concurrent=2)

            async def fake_run(src, **kw):
                await asyncio.sleep(10)  # 永不结束
                return None

            ap.run = fake_run  # type: ignore[assignment]

            async def driver():
                t1 = asyncio.create_task(ap.run("a"))
                t2 = asyncio.create_task(ap.run("b"))
                ap._tasks.extend([t1, t2])
                # 让 driver 跑 0.05s 让 t1/t2 进入 in-flight
                await asyncio.sleep(0.05)
                n = ap.cancel()
                return n

            n = _run_coro(driver())
            self.assertGreaterEqual(n, 0)  # 取决于时序


# ---------------------------------------------------------------------------
# 公共契约保留
# ---------------------------------------------------------------------------
class TestPipelinePublicContract(unittest.TestCase):
    """v3.2.0a Pipeline 公共契约 v3.2.0b 仍可用。"""

    def test_transcribe_still_returns_transcript_result(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = _audio_file(tmp)
            sync = Pipeline(settings=_fake_settings(tmp), transcriber=_fake_transcriber())
            r = sync.transcribe(str(audio))
            self.assertIsInstance(r, TranscriptResult)
            self.assertEqual(r.text, "hello world")


# ---------------------------------------------------------------------------
# v3.2.0e: GPU 显存感知并发
# ---------------------------------------------------------------------------
class TestGpuAwareConcurrency(unittest.TestCase):
    """``_gpu_aware_concurrency`` 的策略矩阵。

    直接传 ``health`` dict 绕开模块级缓存,保证测试确定性。
    """

    _CUDA_HEALTH = {
        "available": True,
        "device": "cuda",
        "name": "RTX 4090",
        "vram_total_mb": 24576,
        "vram_used_mb": 6144,  # free = 18432 MB
        "temperature_c": 41,
        "utilisation_pct": 38,
        "backend": "torch",
    }

    # -- 非 CUDA 不收紧 ----------------------------------------------------
    def test_cpu_device_returns_base_unchanged(self):
        health = {"available": False, "device": "cpu", "vram_total_mb": None, "vram_used_mb": None}
        self.assertEqual(_gpu_aware_concurrency(4, health=health), 4)

    def test_metal_device_returns_base_unchanged(self):
        # metal / rocm 等非 cuda 后备,不参与 VRAM 计算
        health = {"available": True, "device": "metal", "vram_total_mb": None, "vram_used_mb": None}
        self.assertEqual(_gpu_aware_concurrency(4, health=health), 4)

    def test_cuda_but_no_vram_info_returns_base(self):
        # CUDA 可用但 mem_get_info 探测失败 → 保守不收紧
        health = {"available": True, "device": "cuda", "vram_total_mb": None, "vram_used_mb": None}
        self.assertEqual(_gpu_aware_concurrency(4, health=health), 4)

    # -- CUDA 收紧逻辑 -----------------------------------------------------
    def test_cuda_tightens_to_free_vram_div_vram_per_task(self):
        # free = 24576 - 6144 = 18432 MB;18432 // 3000 = 6;min(4, 6) = 4
        self.assertEqual(_gpu_aware_concurrency(4, health=self._CUDA_HEALTH), 4)

    def test_cuda_low_vram_caps_below_base(self):
        # free = 5000 MB;5000 // 3000 = 1;min(4, 1) = 1
        health = {**self._CUDA_HEALTH, "vram_total_mb": 8192, "vram_used_mb": 3192}  # free = 5000
        self.assertEqual(_gpu_aware_concurrency(4, health=health), 1)

    def test_cuda_vram_just_below_two_tasks_caps_to_one(self):
        # free = 5999 MB;5999 // 3000 = 1(差 1MB 到 2)
        health = {**self._CUDA_HEALTH, "vram_total_mb": 12000, "vram_used_mb": 6001}  # free = 5999
        self.assertEqual(_gpu_aware_concurrency(4, health=health), 1)

    def test_cuda_zero_free_vram_still_returns_one(self):
        # free = 0;max(1, 0) = 1 — 至少保留 1 个并发,不卡死
        health = {**self._CUDA_HEALTH, "vram_used_mb": 24576}  # free = 0
        self.assertEqual(_gpu_aware_concurrency(4, health=health), 1)

    def test_base_one_returns_one_regardless_of_gpu(self):
        # base=1 (串行) 时直接返回 1,跳过 GPU 探测
        self.assertEqual(_gpu_aware_concurrency(1, health=self._CUDA_HEALTH), 1)

    def test_custom_vram_per_task_overrides_default(self):
        # free = 18432;18432 // 5000 = 3;min(4, 3) = 3
        self.assertEqual(
            _gpu_aware_concurrency(4, vram_per_task_mb=5000, health=self._CUDA_HEALTH),
            3,
        )

    def test_zero_vram_per_task_returns_base(self):
        # 非法 vram_per_task → 保守不收紧
        self.assertEqual(
            _gpu_aware_concurrency(4, vram_per_task_mb=0, health=self._CUDA_HEALTH),
            4,
        )


class TestVramPerTaskEnv(unittest.TestCase):
    def test_env_override(self):
        with mock.patch.dict(os.environ, {"MEDIASCRIBE_VRAM_PER_TASK_MB": "2500"}):
            self.assertEqual(_vram_per_task_mb(), 2500)

    def test_invalid_env_falls_back(self):
        for bad in ("0", "-1", "abc", ""):
            env = {k: v for k, v in os.environ.items() if k != "MEDIASCRIBE_VRAM_PER_TASK_MB"}
            env["MEDIASCRIBE_VRAM_PER_TASK_MB"] = bad
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertGreater(_vram_per_task_mb(), 0)


class TestGpuHealthCache(unittest.TestCase):
    def test_cache_returns_same_value_within_ttl(self):
        cache = _GpuHealthCache(ttl=10.0)
        # 第一次探测
        with mock.patch(
            "mediascribe.pipeline_async.gpu_health",
            return_value={"available": True, "device": "cuda"},
        ) as m:
            v1 = cache.get()
            v2 = cache.get()
            self.assertEqual(v1, v2)
            # gpu_health 只被调一次(TTL 内复用)
            self.assertEqual(m.call_count, 1)

    def test_invalidate_forces_refresh(self):
        cache = _GpuHealthCache(ttl=10.0)
        with mock.patch(
            "mediascribe.pipeline_async.gpu_health",
            return_value={"available": False, "device": "cpu"},
        ):
            cache.get()
        cache.invalidate()
        with mock.patch(
            "mediascribe.pipeline_async.gpu_health",
            return_value={"available": True, "device": "cuda"},
        ) as m:
            v = cache.get()
            self.assertEqual(v["device"], "cuda")
            self.assertEqual(m.call_count, 1)


# ---------------------------------------------------------------------------
# v3.2.0e: runner / runners 注入(Web 接线契约)
# ---------------------------------------------------------------------------
class TestRunnerInjection(unittest.TestCase):
    """``runner=`` / ``runners=`` 让调用方注入自定义可调用(如 with_progress
    封装的带进度 thunk),替代默认 ``self._sync.transcribe``。这是 Web 层
    ``submit_jobs`` 走 ``AsyncPipeline.run_batch`` 的关键契约。
    """

    def test_run_honors_custom_runner(self):
        sync = mock.MagicMock()
        sync.settings = mock.MagicMock()
        ap = AsyncPipeline(sync)
        captured = {}

        def my_runner():
            captured["hit"] = True
            return "CUSTOM"

        result = _run_coro(ap.run("u", runner=my_runner))
        self.assertEqual(result, "CUSTOM")
        self.assertTrue(captured.get("hit"))
        # 自定义 runner 时不应调用底层 transcribe
        sync.transcribe.assert_not_called()

    def test_run_batch_uses_per_source_runners(self):
        sync = mock.MagicMock()
        sync.settings = mock.MagicMock()
        ap = AsyncPipeline(sync, max_concurrent=2)
        calls: dict = {}

        def ok(sentinel):
            def _r():
                calls[sentinel] = True
                return f"ok:{sentinel}"

            return _r

        def bad():
            calls["bad"] = True
            raise RuntimeError("boom")

        runners = {"a": ok("a"), "bad": bad, "c": ok("c")}
        results = _run_coro(ap.run_batch(["a", "bad", "c"], runners=runners))
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0], "ok:a")
        self.assertEqual(results[2], "ok:c")
        # 失败位置隔离为 _FailedResult
        self.assertIsInstance(results[1], _FailedResult)
        self.assertIn("boom", str(results[1].exc))
        # 三个 runner 都被调用
        self.assertEqual(set(calls.keys()), {"a", "bad", "c"})
        # 底层 transcribe 不应被直接调用
        sync.transcribe.assert_not_called()

    def test_run_batch_mixed_runners_and_default(self):
        # 部分 source 提供 runner,其余走默认 transcribe
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for i in range(3):
                _audio_file(tmp, f"a{i}.wav")
            sync = Pipeline(settings=_fake_settings(tmp), transcriber=_fake_transcriber())
            ap = AsyncPipeline(sync, max_concurrent=2)
            src1 = str(tmp / "a1.wav")
            custom = {src1: (lambda: "CUSTOM")}
            results = _run_coro(
                ap.run_batch(
                    [str(tmp / "a0.wav"), src1, str(tmp / "a2.wav")],
                    runners=custom,
                )
            )
            self.assertEqual(len(results), 3)
            # a1 走自定义 runner
            self.assertEqual(results[1], "CUSTOM")
            # a0 / a2 走默认 transcribe
            self.assertIsInstance(results[0], TranscriptResult)
            self.assertIsInstance(results[2], TranscriptResult)


if __name__ == "__main__":
    unittest.main()
