"""进程级显存预算账本 — GPU 并发守恒不变量(P0-1,2026-11-20 架构评审修复)。

为什么闸门放在 ``TranscribeStage`` 而不是 ``AsyncPipeline``:

* CLI / batch / Web(同步 ``/api/transcribe`` 与批量 ``/api/jobs``)/ MCP 四条
  入口最终都走同一条 stage chain,把闸门放在转录 stage 的调用点上,所有路径
  统一过闸;``AsyncPipeline`` 的 ``asyncio.Semaphore`` 退化为纯调度参数,
  不再承担资源守恒职责。
* 用 ``threading`` 原语实现,与 event loop 无关 —— 复审实锤:每个 Web 批次
  一个 ``asyncio.run``,绑定 loop 的 ``asyncio.Semaphore`` 会跨 loop 重建,
  "进程级守恒"失效(两个 loop 各拿一个信号量,GPU 任务数翻倍)。
* 已知边界:Web 与 MCP 同时运行是**两个进程**,任何进程内账本都跨不过进程
  边界 —— 单机产品明确不做跨进程仲裁,``doctor`` / 文档注明。

预算语义:首次使用时对空闲显存拍一次快照(``total - used``,其他进程占用
不计入本进程预算),或用 ``MEDIASCRIBE_VRAM_BUDGET_MB`` 显式覆盖。非 CUDA
设备 / 探测失败 → 闸门空转(fail-open,不阻塞任何路径)。单个任务的预估
占用复用 :func:`mediascribe.runtime_guard._estimate_vram` 的实测表;
超出预算的请求按预算 clamp,永不死锁。
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, Optional

__all__ = [
    "VramLedger",
    "get_vram_ledger",
    "reset_vram_ledger",
    "budget_total_mb",
    "estimate_model_mb",
    "ensure_resident_capacity",
    "register_family_clear",
    "register_resident",
    "unregister_resident",
    "unregister_family",
    "resident_total_mb",
    "reset_resident_registry",
]

_DEFAULT_ESTIMATE_MB = 2000

_LEDGER: Optional["VramLedger"] = None
_LEDGER_LOCK = threading.Lock()


def _probe_budget_mb() -> Optional[int]:
    """返回显存总预算(MB);``None`` = 不设闸(fail-open)。

    优先级:``MEDIASCRIBE_VRAM_BUDGET_MB`` 显式覆盖 > 空闲显存快照。
    测试 seam — 单测直接 patch 本函数。
    """
    raw = os.environ.get("MEDIASCRIBE_VRAM_BUDGET_MB", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    try:
        # 函数内导入:gpu_budget 处于 pipeline→pipeline_stages→gpu_budget 的
        # 依赖链下,顶层导入 pipeline 会成环。
        from .pipeline import gpu_health
    except Exception:  # pragma: no cover - defensive
        return None
    try:
        health = gpu_health()
    except Exception:
        return None
    if not health.get("available") or health.get("device") != "cuda":
        return None
    total = health.get("vram_total_mb")
    used = health.get("vram_used_mb")
    if not total or used is None:
        return None
    free = max(0, int(total) - int(used))
    # free == 0 时设闸只会让所有任务死等 —— 放开闸门交给 CUDA 自身处理
    return free if free > 0 else None


def _estimate_task_vram_mb(transcriber: Any, settings: Any) -> int:
    """按 (engine, model, device) 查实测表估算单任务显存占用(MB)。

    transcriber 属性缺失(如测试桩 / chunked 包装)时逐级回退到
    settings 再回退到保守默认值。
    """
    from .runtime_guard import _estimate_vram

    engine = str(getattr(transcriber, "name", "") or "whisper")
    model = str(getattr(transcriber, "model_name", "") or getattr(settings, "model", "") or "small")
    device = str(getattr(transcriber, "device", "") or getattr(settings, "device", "") or "cpu")
    return _estimate_vram(engine, model, device) or _DEFAULT_ESTIMATE_MB


def estimate_model_mb(engine: str, model: str, device: str) -> int:
    """按 (engine, model, device) 查实测表估算模型显存(MB),未知组合给保守默认。"""
    from .runtime_guard import _estimate_vram

    return _estimate_vram(engine, model, device) or _DEFAULT_ESTIMATE_MB


# ---------------------------------------------------------------------------
# 预算探测缓存 — 账本(P0-1)与驻留注册表(P1-4)共用,进程内只探测一次
# ---------------------------------------------------------------------------
_BUDGET_TOTAL_MB: Optional[int] = None
_BUDGET_PROBED = False
_BUDGET_CACHE_LOCK = threading.Lock()


def budget_total_mb() -> Optional[int]:
    """显存总预算(MB);``None`` = 非 CUDA / 探测失败(fail-open)。

    首次调用探测一次并缓存;``reset_vram_ledger`` 可强制重探。
    """
    global _BUDGET_TOTAL_MB, _BUDGET_PROBED
    with _BUDGET_CACHE_LOCK:
        if not _BUDGET_PROBED:
            _BUDGET_PROBED = True
            _BUDGET_TOTAL_MB = _probe_budget_mb()
        return _BUDGET_TOTAL_MB


# ---------------------------------------------------------------------------
# P1-4(2026-11-20 架构评审): 驻留模型注册表 — 跨引擎缓存族显存预算
# ---------------------------------------------------------------------------
# whisper / faster-whisper / whisperx 三族的模块级模型缓存此前互不感知
# (各族各自 LRU cap 2,whisperx 还有无上限的 align/diarize 缓存):
# 用户在 Web UI 轮换引擎/档位时,三族缓存可同时驻留,8GB 卡交替用
# large + small 即可叠爆显存。本注册表把"各家族当前驻留模型的估计显存"
# 集中记账;装新模型前超预算 → 先清**其它**家族缓存(本族由引擎自己的
# LRU cap 收敛)。
#
# 死锁规避(重要):引擎在自家 ``_MODEL_CACHE_LOCK`` **内**调用
# :func:`ensure_resident_capacity`;若本模块持注册表锁再去调用其它引擎的
# 清缓存钩子(它要拿对方引擎锁),与"其它线程: 对方引擎锁 → 注册表锁"
# 构成环 —— 所以钩子调用必须发生在注册表锁**之外**。
_RESIDENT_LOCK = threading.Lock()
_RESIDENT: Dict[Any, int] = {}
_FAMILY_CLEAR_HOOKS: Dict[str, Callable[[], None]] = {}


def register_family_clear(family: str, clear_fn: Callable[[], None]) -> None:
    """登记某引擎家族的清缓存钩子(幂等;引擎在首次装模型时调用)。"""
    with _RESIDENT_LOCK:
        _FAMILY_CLEAR_HOOKS.setdefault(family, clear_fn)


def register_resident(family: str, key: Any, mb: int) -> None:
    """登记一个已加载的驻留模型(引擎缓存未命中、加载成功后调用)。"""
    with _RESIDENT_LOCK:
        _RESIDENT[(family, key)] = max(0, int(mb))


def unregister_resident(family: str, key: Any) -> None:
    """反注册(引擎 LRU 淘汰时调用)。"""
    with _RESIDENT_LOCK:
        _RESIDENT.pop((family, key), None)


def unregister_family(family: str) -> None:
    """反注册整个家族(引擎 ``clear_model_cache`` 内调用,保持账实一致)。"""
    with _RESIDENT_LOCK:
        for k in [k for k in _RESIDENT if k[0] == family]:
            _RESIDENT.pop(k, None)


def resident_total_mb() -> int:
    """当前所有家族驻留模型的估计显存之和(MB)。"""
    with _RESIDENT_LOCK:
        return sum(_RESIDENT.values())


def clear_other_families(family: str) -> int:
    """清掉除 ``family`` 外所有有钩子的引擎缓存,返回调用的钩子数。

    钩子(引擎 ``clear_model_cache``)自行清缓存并 :func:`unregister_family`,
    调用发生在注册表锁之外(见上方死锁规避说明)。
    """
    with _RESIDENT_LOCK:
        others = [(name, fn) for name, fn in _FAMILY_CLEAR_HOOKS.items() if name != family]
    called = 0
    for _name, fn in others:
        try:
            fn()
            called += 1
        except Exception:  # pragma: no cover - defensive
            continue
    return called


def ensure_resident_capacity(family: str, needed_mb: int) -> bool:
    """跨引擎驻留预算闸:装新模型前确保驻留估计之和 + 新模型 ≤ 预算。

    * 预算不可用(非 CUDA / 探测失败)→ ``True``(fail-open);
    * 超预算 → 先清**其它**家族缓存再复查;
    * 清完仍不够(新模型本身比预算还大)→ ``False``,调用方照常加载
      (fail-open,运行期由任务级 :class:`VramLedger` 兜底)。
    """
    budget = budget_total_mb()
    if not budget:
        return True
    need = min(max(0, int(needed_mb)), budget)
    with _RESIDENT_LOCK:
        if sum(_RESIDENT.values()) + need <= budget:
            return True
    clear_other_families(family)
    with _RESIDENT_LOCK:
        fits = sum(_RESIDENT.values()) + need <= budget
    return bool(fits)


def reset_resident_registry() -> None:
    """清空注册表与家族钩子(测试钩子)。"""
    with _RESIDENT_LOCK:
        _RESIDENT.clear()
        _FAMILY_CLEAR_HOOKS.clear()


class VramLedger:
    """进程级显存账本:acquire/release,``threading`` 原语、loop 无关。

    典型用法(见 :meth:`mediascribe.pipeline_stages.TranscribeStage.run`)::

        mb = ledger.slot_for(transcriber, settings)
        acquired = ledger.acquire(mb, cancel_event=ctx.cancel_event) if mb else True
        if not acquired:
            raise PipelineCancelled()  # 等 GPU 预算期间被取消
        try:
            ...  # 转录
        finally:
            if mb:
                ledger.release(mb)
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._initialized = False
        self._total_mb: Optional[int] = None
        self._available_mb: int = 0

    def _ensure_budget(self) -> Optional[int]:
        """惰性初始化:预算只拍一次快照(进程级缓存),之后由 acquire/release 自行记账。"""
        with self._cond:
            if not self._initialized:
                self._initialized = True
                self._total_mb = budget_total_mb()
                self._available_mb = self._total_mb or 0
            return self._total_mb

    def slot_for(self, transcriber: Any, settings: Any) -> int:
        """单个转录任务的预估显存占用(MB);``0`` = 闸门空转(非 CUDA 等)。"""
        if not self._ensure_budget():
            return 0
        return _estimate_task_vram_mb(transcriber, settings)

    def acquire(
        self,
        mb: int,
        *,
        cancel_event: Optional[threading.Event] = None,
        poll_seconds: float = 0.5,
    ) -> bool:
        """申请显存份额,不足时阻塞等待;等待期间被取消返回 ``False``。

        请求超过总预算时按预算 clamp(永不死锁,代价是超大任务串行)。
        闸门未启用(非 CUDA / 探测失败)时直接放行返回 ``True``。
        """
        total = self._ensure_budget()
        if not total:
            return True
        need = max(1, min(int(mb), total))
        with self._cond:
            while self._available_mb < need:
                if cancel_event is not None and cancel_event.is_set():
                    return False
                self._cond.wait(poll_seconds)
            self._available_mb -= need
            return True

    def release(self, mb: int) -> None:
        """归还显存份额并唤醒所有等待者。未启用时为 no-op。"""
        with self._cond:
            if not self._total_mb:
                return
            self._available_mb = min(self._total_mb, self._available_mb + max(0, int(mb)))
            self._cond.notify_all()

    def snapshot(self) -> dict:
        """账本现状(测试 / doctor 自检用)。"""
        with self._cond:
            return {
                "enabled": self._total_mb is not None,
                "total_mb": self._total_mb,
                "available_mb": self._available_mb,
            }


def get_vram_ledger() -> VramLedger:
    """进程级单例 — 四条入口路径共享同一份预算。"""
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            _LEDGER = VramLedger()
        return _LEDGER


def reset_vram_ledger() -> None:
    """清空单例与预算缓存(测试钩子 — reset 后首次使用会重新探测)。"""
    global _LEDGER, _BUDGET_PROBED, _BUDGET_TOTAL_MB
    with _LEDGER_LOCK:
        _LEDGER = None
    with _BUDGET_CACHE_LOCK:
        _BUDGET_PROBED = False
        _BUDGET_TOTAL_MB = None
