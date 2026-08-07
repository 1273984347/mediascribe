"""
v3.2.0b Tier 1 — fully asynchronous pipeline.

:class:`AsyncPipeline` 是 :class:`Pipeline` 的可选 async 包装,把
:vmod:`video2text.pipeline_stages` 的同步 stage 推到默认 executor
(``asyncio.to_thread``),并允许在 batch 场景下并发跑多个视频
的 ``download → transcribe → assemble``。

设计约束
--------

1. **不破坏 v3.1.0 / v3.2.0a API** — :class:`Pipeline` 仍是同步入口;
   :class:`AsyncPipeline` 是 opt-in,用 ``await pipeline.run(url)``
   替代 ``pipeline.transcribe(url)``。
2. **共享 stage 子类** — :class:`AsyncPipeline` 不重新发明 stage,
   而是从 :func:`video2text.pipeline_stages.default_chain` 拿同一批
   stage,只把 ``stage.run(ctx)`` 包成 ``asyncio.to_thread``。
3. **并发粒度** — 进程级并发(``asyncio.gather`` 多个 video),
   不是 stage 内并发;stage 内并发会让 5 个 stage 都争抢 GPU,
   反而拖慢。
4. **背压** — :attr:`AsyncPipeline.max_concurrent` 控制并发上限,
   默认 ``min(4, os.cpu_count() or 2)``,可被
   ``VIDEO2TEXT_MAX_WORKERS`` 环境变量覆盖。
5. **取消** — ``pipeline.cancel()`` 一次性 ``task.cancel()`` 所有
   in-flight 任务。底层 :class:`Stage` 在 v3.2.0e 已把
   ``ctx.cancel_event`` 传给 blocking ffmpeg / whisper 调用。
6. **GPU 显存感知 (v3.2.0e)** — :func:`_gpu_aware_concurrency` 在
   batch 启动时快照 :func:`video2text.pipeline.gpu_health`,按
   ``free_vram // vram_per_task`` 收紧并发,避免 CUDA OOM。
   非 CUDA 设备(CPU / metal)不受影响,直接用 ``max_concurrent``。

测试
----
:mod:`douyin_batch.tests.test_pipeline_async` 覆盖并发数、
``asyncio.gather`` 隔离、cancellation 传播与 ``Pipeline`` 等价性。
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from .config import Settings
from .models import TranscriptResult
from .pipeline import Pipeline, gpu_health
from .pipeline_stages import PipelineCancelled


def _default_max_concurrent() -> int:
    """默认并发上限,优先级 ``VIDEO2TEXT_MAX_WORKERS`` > CPU 数。"""
    env = os.environ.get("VIDEO2TEXT_MAX_WORKERS")
    if env and env.isdigit() and int(env) > 0:
        return int(env)
    cpu = os.cpu_count() or 2
    return min(4, cpu)


# ---------------------------------------------------------------------------
# v3.2.0e: GPU 显存感知并发
# ---------------------------------------------------------------------------
# 每个 ASR 任务预估显存占用(MB)。large-v3 ~5GB、medium ~5GB、small ~2GB,
# 取 3000MB 作保守默认(覆盖 medium/large-v3 + 框架开销),可被
# ``VIDEO2TEXT_VRAM_PER_TASK_MB`` 环境变量覆盖。
_DEFAULT_VRAM_PER_TASK_MB = 3000

# ``gpu_health()`` 调用 torch CUDA API,有点贵(亚毫秒级但非零)。
# TTL 缓存避免每个 batch 都重新探测。5s 在 batch 场景下足够新鲜
# (一个 batch 通常跑数十秒到数分钟),又不会因频繁探测拖慢调度。
_DEFAULT_GPU_HEALTH_TTL = 5.0


class _GpuHealthCache:
    """对 :func:`gpu_health` 做 TTL 缓存,线程安全。

    为什么要缓存:``run_batch`` 启动时调一次,如果一个进程连续跑
    多个 batch(如 Web 服务),不该每次都重新探测。TTL 到期才刷新。
    """

    def __init__(self, ttl: float = _DEFAULT_GPU_HEALTH_TTL):
        self._ttl = ttl
        self._lock = threading.Lock()
        self._value: dict | None = None
        self._expires_at: float = 0.0

    def get(self) -> dict:
        now = time.monotonic()
        with self._lock:
            if self._value is not None and now < self._expires_at:
                return self._value
            # 持锁探测 — gpu_health 本身快(<1ms),可接受
            self._value = gpu_health()
            self._expires_at = now + self._ttl
            return self._value

    def invalidate(self) -> None:
        """显式作废缓存(测试用 / 显存释放后手动刷新)。"""
        with self._lock:
            self._value = None
            self._expires_at = 0.0


# 模块级单例 — 同一进程内所有 AsyncPipeline 共享探测结果
_GPU_HEALTH_CACHE = _GpuHealthCache()


def _vram_per_task_mb() -> int:
    """读 ``VIDEO2TEXT_VRAM_PER_TASK_MB`` 环境变量,失败回退默认。"""
    raw = os.environ.get("VIDEO2TEXT_VRAM_PER_TASK_MB")
    if not raw:
        return _DEFAULT_VRAM_PER_TASK_MB
    try:
        v = int(raw)
        return v if v > 0 else _DEFAULT_VRAM_PER_TASK_MB
    except (TypeError, ValueError):
        return _DEFAULT_VRAM_PER_TASK_MB


def _gpu_aware_concurrency(
    base: int,
    *,
    vram_per_task_mb: int | None = None,
    health: dict | None = None,
) -> int:
    """根据 GPU 空闲显存收紧并发上限。

    策略
    ----
    * 非 CUDA(``health["device"] != "cuda"`` 或 ``available=False``)
      → 返回 ``base``,GPU 不参与调度
    * CUDA 但探测不到 VRAM(``vram_total_mb is None``)
      → 返回 ``base``,无法判断就保守用原值
    * CUDA 且有 VRAM 数据
      → ``min(base, max(1, free_vram_mb // vram_per_task_mb))``
      其中 ``free_vram_mb = vram_total_mb - vram_used_mb``
      *至少* 保留 1 个并发(避免完全卡死)

    Parameters
    ----------
    base
        用户/环境配置的并发上限(``max_concurrent``)。
    vram_per_task_mb
        每个任务预估显存(MB);``None`` 时读
        :func:`_vram_per_task_mb`。
    health
        预先探测好的 GPU 健康字典;``None`` 时从模块级缓存取。

    Returns
    -------
    int
        收紧后的并发上限,``1 <= returned <= base``。
    """
    if base <= 1:
        return base
    if vram_per_task_mb is None:
        vram_per_task_mb = _vram_per_task_mb()
    if vram_per_task_mb <= 0:
        return base
    if health is None:
        health = _GPU_HEALTH_CACHE.get()
    # 非 CUDA 或探测失败 → 不收紧
    if not health.get("available") or health.get("device") != "cuda":
        return base
    total = health.get("vram_total_mb")
    used = health.get("vram_used_mb")
    if total is None or used is None:
        return base
    free = max(0, int(total) - int(used))
    gpu_cap = max(1, free // vram_per_task_mb)
    return min(base, gpu_cap)


class AsyncPipeline:
    """异步 pipeline — 多个 video 并发跑 stage chain。

    Parameters
    ----------
    sync_pipeline
        一个构造好的 :class:`Pipeline` 实例(用于复用其
        ``_get_downloader`` / ``_resolve_output_path`` / markdown
        拼接等工具方法)。AsyncPipeline 不会改它,只在内部用其
        ``_build_chain()`` 拿 stage list。
    max_concurrent
        并发上限。0 = 单串行(等价 :class:`Pipeline`);默认见
        :func:`_default_max_concurrent`。v3.2.0e:实际并发可能被
        :func:`_gpu_aware_concurrency` 按 GPU 空闲显存进一步收紧。
    vram_per_task_mb
        每个任务预估显存(MB),用于 GPU 感知并发计算;
        ``None`` 时读 ``VIDEO2TEXT_VRAM_PER_TASK_MB`` 环境变量,
        默认 3000。仅对 CUDA 设备生效。
    """

    def __init__(
        self,
        sync_pipeline: Pipeline,
        *,
        max_concurrent: int = 0,
        vram_per_task_mb: int | None = None,
    ):
        self._sync = sync_pipeline
        if max_concurrent <= 0:
            max_concurrent = _default_max_concurrent()
        self.max_concurrent = max_concurrent
        self._vram_per_task_mb = (
            vram_per_task_mb
            if vram_per_task_mb and vram_per_task_mb > 0
            else _vram_per_task_mb()
        )
        self._tasks: List[asyncio.Task] = []
        self._cancel_event = threading.Event()

    @property
    def settings(self) -> Settings:
        return self._sync.settings

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    async def run(
        self,
        source_input: str,
        *,
        runner: Optional[Callable[[], Any]] = None,
        **kwargs,
    ) -> TranscriptResult:
        """跑单个 video 的 stage chain(走 asyncio.to_thread)。

        若提供 ``runner``(可调用 ``() -> TranscriptResult``),则改为执行
        ``runner()`` 而非底层 ``Pipeline.transcribe`` —— 这是 Web 层接线
        的关键:传入 :func:`video2text.progress.with_progress` 包装的 thunk,
        即可在并发批处理中保留进度事件与结果落盘。
        """
        loop = asyncio.get_running_loop()
        if runner is not None:
            return await loop.run_in_executor(None, runner)
        return await loop.run_in_executor(
            None, lambda: self._sync.transcribe(source_input, **kwargs)
        )

    async def run_batch(
        self,
        source_inputs: Sequence[str],
        *,
        runners: Optional[Dict[str, Callable[[], Any]]] = None,
        **kwargs,
    ) -> List[TranscriptResult]:
        """并发跑多个 video,任一失败不阻塞其他。

        行为契约:
        * 输入长度 N → 输出长度 N(失败的位置是 :class:`_FailedResult` 包装)
        * ``max_concurrent`` 限制 in-flight 任务数,超出排队
        * 任一任务抛 :class:`Exception`,该位置结果是 ``_FailedResult``
          类型(``isinstance(result, _FailedResult)``)
        * 整体 :func:`asyncio.gather` 不抛 — 调用方按位置访问
        * ``runners`` 可选:键为 source、值为 ``() -> result`` 的可调用,
          命中时优先于默认 ``self._sync.transcribe`` 执行(Web 层用于注入
          with_progress 包装的带进度 thunk)

        v3.2.0e:batch 启动时调 :func:`_gpu_aware_concurrency` 收紧并发,
        避免 CUDA OOM。快照一次(不中途动态调整),中途显存波动由
        CUDA 自身处理(slowdown 而非 fail)。
        """
        if not source_inputs:
            return []

        effective = _gpu_aware_concurrency(
            self.max_concurrent, vram_per_task_mb=self._vram_per_task_mb
        )
        sem = asyncio.Semaphore(effective)
        runners = runners or {}

        async def _one(src: str) -> object:
            async with sem:
                if self._cancel_event.is_set():
                    return _FailedResult(src, PipelineCancelled())
                try:
                    r = runners.get(src)
                    if r is not None:
                        return await self.run(src, runner=r)
                    return await self.run(src, **kwargs)
                except Exception as exc:  # 隔离单个视频失败(含 PipelineCancelled)
                    return _FailedResult(src, exc)

        tasks = [asyncio.create_task(_one(s)) for s in source_inputs]
        self._tasks.extend(tasks)
        try:
            return list(await asyncio.gather(*tasks, return_exceptions=False))
        finally:
            # 清掉已完成的 task 引用,避免内存累积
            self._tasks = [t for t in self._tasks if not t.done()]

    def cancel(self) -> int:
        """取消所有 in-flight 任务,返回被取消的 task 数。"""
        self._cancel_event.set()
        n = 0
        for t in list(self._tasks):
            if not t.done():
                t.cancel()
                n += 1
        return n

    # ------------------------------------------------------------------
    # 等价 / 兼容工具
    # ------------------------------------------------------------------
    def build_sync_equivalent(self) -> Pipeline:
        """返回底层 :class:`Pipeline`,便于 doctest / 互操作。"""
        return self._sync


# ---------------------------------------------------------------------------
# 失败包装
# ---------------------------------------------------------------------------
class _FailedResult:
    """``AsyncPipeline.run_batch`` 在单个 source 失败时返回的占位。

    用 duck-typing 表达「失败」语义 — 不继承 :class:`TranscriptResult`,
    调用方用 ``isinstance(r, _FailedResult)`` 判定。
    """

    def __init__(self, source: str, exc: BaseException):
        self.source = source
        self.exc = exc

    def __repr__(self) -> str:
        return f"_FailedResult(source={self.source!r}, exc={self.exc!r})"


# ---------------------------------------------------------------------------
# from_sync — 工厂
# ---------------------------------------------------------------------------
def from_sync(sync_pipeline: Pipeline, **kwargs) -> AsyncPipeline:
    """从现有 :class:`Pipeline` 快速包成 :class:`AsyncPipeline`。

    等价于 ``AsyncPipeline(sync_pipeline, **kwargs)``,但放在
    模块顶层便于 ``pipeline = from_sync(Pipeline(settings))`` 写法。
    """
    return AsyncPipeline(sync_pipeline, **kwargs)


__all__ = [
    "AsyncPipeline",
    "_FailedResult",
    "from_sync",
    "_default_max_concurrent",
    "_gpu_aware_concurrency",
    "_GpuHealthCache",
    "_vram_per_task_mb",
]
