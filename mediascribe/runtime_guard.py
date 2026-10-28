"""
运行时资源守卫 —— 硬件自适应（自动降档）+ 过载保护（监测与安全中止）。

为什么需要
----------
MediaScribe 跑在**用户的机器**上，而这些机器的规格跨度极大：8 GB 内存的老笔记本
（无独显）、16 GB + 8 GB 显存的游戏本、32 GB + 16 GB 显存的创作机、Mac。
同一份配置在不同机器上的结果可能从「秒级完成」到「OOM 崩掉」。

2026-10-23 的本机实测给出了决策依据（CPU 档峰值内存 / CUDA 档峰值显存）：

======================  ==============  ==============  ==============
引擎 / 档位              峰值显存        峰值内存        备注
======================  ==============  ==============  ==============
faster-whisper small    614 MiB        1167 MiB        CUDA int8_float16
faster-whisper large-v3 2816 MiB       3757 MiB        CUDA int8_float16
faster-whisper small    —              952 MiB         CPU int8
faster-whisper large-v3 —              3613 MiB        CPU int8
openai-whisper small    1965 MiB       1963 MiB        CUDA fp16
openai-whisper medium   4774 MiB       4074 MiB        CUDA fp16
**whisper.cpp tiny**     —              **291 MiB**     CPU ggml
======================  ==============  ==============  ==============

⇒ whisper.cpp 是**最省内存**的（291 MiB），在 4 GB 机器上它是唯一可行的选择。

两层防线
--------
1. **转录前（:func:`plan`）** —— 按当前资源**自动降档**（模型档位）或
   **换引擎**（Apple Silicon / 内存紧张），并产出**给人看的通知**。
2. **转录中（:class:`RuntimeMonitor`）** —— 后台线程持续采样内存/显存/温度；
   越过紧急线时**通知 + 请求取消**（尽力而为），转录结束后若确实发生过
   紧急过载 ⇒ 抛 :class:`ResourcePressureError`，**不交付可能不完整的结果**。

估算表是**单一来源**：:mod:`scripts.bench.asr.bench_asr` 导入本模块的同一份
数据 ⇒ 基准与产品不会出现两套口径。
"""

from __future__ import annotations

import logging
import os
import platform
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 模型档位阶梯（从重到轻）
# ---------------------------------------------------------------------------

#: 降档时按此顺序往下降。
#: ⚠️ 这里**只列族名**、每族一次 —— ``large`` / ``large-v2`` / ``large-v3`` /
#: ``distil-large-*`` 都归为 ``large-v3`` 族。若把同族的多个名字都列进阶梯，
#: 归一化后降档会落回同一族 ⇒ 死循环（实测踩到：large-v3 → large-v2 → large-v2…）。
MODEL_LADDER: Tuple[str, ...] = ("large-v3", "medium", "small", "base", "tiny")

#: 归一化：把 ``large`` / ``large-v2`` / ``large-v3`` 视作同一档
_MODEL_FAMILY = {
    "large": "large-v3",
    "large-v2": "large-v3",
    "large-v3": "large-v3",
    "medium": "medium",
    "small": "small",
    "base": "base",
    "tiny": "tiny",
    "distil-large-v2": "large-v3",
    "distil-large-v3": "large-v3",
}

#: 各 (engine, model 族, device) 的**估算**常驻显存（MiB）
#: 数据来源见模块 docstring 的实测表；未实测的按同族插值。
VRAM_ESTIMATE_MB: Dict[Tuple[str, str, str], int] = {
    ("faster-whisper", "tiny", "cuda"): 400,
    ("faster-whisper", "base", "cuda"): 500,
    ("faster-whisper", "small", "cuda"): 614,
    ("faster-whisper", "medium", "cuda"): 1600,
    ("faster-whisper", "large-v3", "cuda"): 2816,
    ("whisper", "tiny", "cuda"): 900,
    ("whisper", "base", "cuda"): 1400,
    ("whisper", "small", "cuda"): 1965,
    ("whisper", "medium", "cuda"): 4774,
    ("whisper", "large-v3", "cuda"): 3600,
    ("whisperx", "tiny", "cuda"): 700,
    ("whisperx", "base", "cuda"): 800,
    ("whisperx", "small", "cuda"): 900,
    ("whisperx", "medium", "cuda"): 2200,
    ("whisperx", "large-v3", "cuda"): 3600,
}

#: 各 (engine, model 族, device=cpu) 的**估算**常驻内存（MiB）
RAM_ESTIMATE_MB: Dict[Tuple[str, str, str], int] = {
    ("faster-whisper", "tiny", "cpu"): 600,
    ("faster-whisper", "base", "cpu"): 800,
    ("faster-whisper", "small", "cpu"): 952,
    ("faster-whisper", "medium", "cpu"): 2200,
    ("faster-whisper", "large-v3", "cpu"): 3613,
    ("whisper", "tiny", "cpu"): 1000,
    ("whisper", "base", "cpu"): 1400,
    ("whisper", "small", "cpu"): 1908,
    ("whisper", "medium", "cpu"): 3200,
    ("whisper", "large-v3", "cpu"): 6800,
    ("whisperx", "tiny", "cpu"): 1600,
    ("whisperx", "base", "cpu"): 1800,
    ("whisperx", "small", "cpu"): 1900,
    ("whisperx", "medium", "cpu"): 3400,
    ("whisperx", "large-v3", "cpu"): 7000,
    ("whisper-cpp", "tiny", "cpu"): 291,
    ("whisper-cpp", "base", "cpu"): 348,
    ("whisper-cpp", "small", "cpu"): 500,
    ("whisper-cpp", "medium", "cpu"): 1100,
    ("whisper-cpp", "large-v3", "cpu"): 2600,
}

#: 显存估算的安全系数（留 1/4 余量给 CUDA context 与激活）
VRAM_HEADROOM = 0.75

#: **机器总内存**低于该值时，CPU 路径优先选 whisper.cpp（最省内存）。
#:
#: ⚠️ 这里刻意用「总内存」而不是「可用内存」：
#: 「4 GB 机器跑不动 faster-whisper」是**硬件能力**问题（稳定、可复现），
#: 而「此刻可用内存偏低」是**瞬时**问题 —— 若用它决定引擎，会让同一份配置
#: 在不同机器/不同时刻跑出**不同结果**，测试与复现都会失去意义。
#: 瞬时压力由 :class:`RuntimeMonitor` 在转录**期间**负责（监测 + 安全中止）。
MEMORY_PRESSURE_TOTAL_MB = 8192

#: 降档的**滞回系数**：估算需求超过「可用内存 × 该系数」才降档。
#: 避免可用内存轻微波动就来回降档（抖动）。
DEGRADE_HYSTERESIS = 2.0

#: 内存紧急线（跌破即请求取消）
RAM_ABORT_MB = 512

#: 显存占用紧急线（占总量百分比）
VRAM_ABORT_PCT = 97.0

#: GPU 温度紧急线（℃）
GPU_TEMP_ABORT_C = 92.0


# ---------------------------------------------------------------------------
# 硬件探测
# ---------------------------------------------------------------------------


@dataclass
class HardwareSnapshot:
    """某一时刻的硬件与资源水位。"""

    cuda_available: bool = False
    gpu_name: str = ""
    vram_total_mb: int = 0
    vram_used_mb: int = 0
    ram_total_mb: int = 0
    ram_available_mb: int = 0
    cpu_cores: int = 0
    is_apple_silicon: bool = False
    platform: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def vram_free_mb(self) -> int:
        return max(0, self.vram_total_mb - self.vram_used_mb)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cuda_available": self.cuda_available,
            "gpu_name": self.gpu_name,
            "vram_total_mb": self.vram_total_mb,
            "vram_free_mb": self.vram_free_mb,
            "ram_total_mb": self.ram_total_mb,
            "ram_available_mb": self.ram_available_mb,
            "cpu_cores": self.cpu_cores,
            "is_apple_silicon": self.is_apple_silicon,
            "platform": self.platform,
            "notes": list(self.notes),
        }


def _nvidia_smi(query: str) -> List[str]:
    import shutil
    import subprocess

    if not shutil.which("nvidia-smi"):
        return []
    try:
        proc = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    except Exception:
        return []


def snapshot() -> HardwareSnapshot:
    """采集当前硬件与资源水位（任何探测失败都只记录、不抛异常）。"""
    snap = HardwareSnapshot(platform=platform.platform())
    snap.is_apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"

    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        snap.ram_total_mb = int(vm.total / 1024 / 1024)
        snap.ram_available_mb = int(vm.available / 1024 / 1024)
        snap.cpu_cores = psutil.cpu_count(logical=False) or (os.cpu_count() or 2)
    except Exception as exc:
        snap.notes.append(f"psutil 探测失败: {exc}")

    try:
        import torch  # type: ignore

        snap.cuda_available = bool(torch.cuda.is_available())
        if snap.cuda_available:
            props = torch.cuda.get_device_properties(0)
            snap.gpu_name = props.name
            snap.vram_total_mb = int(props.total_memory / 1024 / 1024)
    except Exception as exc:
        snap.notes.append(f"torch 探测失败: {exc}")

    if not snap.vram_total_mb:
        rows = _nvidia_smi("memory.total,memory.used")
        if rows:
            try:
                total, used = (int(float(x)) for x in rows[0].split(","))
                snap.vram_total_mb = total
                snap.vram_used_mb = used
            except Exception:
                pass
    return snap


# ---------------------------------------------------------------------------
# 转录前：自动降档 / 换引擎
# ---------------------------------------------------------------------------


@dataclass
class DegradeDecision:
    """降档决策结果。"""

    engine: str
    model: str
    device: str
    changed: bool = False
    reasons: List[str] = field(default_factory=list)
    notices: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine": self.engine,
            "model": self.model,
            "device": self.device,
            "changed": self.changed,
            "reasons": list(self.reasons),
            "notices": list(self.notices),
        }


def _family(model: str) -> str:
    return _MODEL_FAMILY.get(model, model)


def _estimate_vram(engine: str, model: str, device: str) -> int:
    return VRAM_ESTIMATE_MB.get((engine, _family(model), device), 2000)


def _estimate_ram(engine: str, model: str, device: str) -> int:
    return RAM_ESTIMATE_MB.get((engine, _family(model), device), 3000)


def _lower_model(model: str) -> Optional[str]:
    """返回下一个更轻的档位；已是最低档返回 ``None``。

    按**族**定位（``large-v2`` 归为 ``large-v3`` 族）⇒ 同一族只会被降一次，
    不会在阶梯上原地打转。
    """
    fam = _family(model)
    if fam not in MODEL_LADDER:
        return None
    idx = MODEL_LADDER.index(fam)
    if idx + 1 >= len(MODEL_LADDER):
        return None
    return MODEL_LADDER[idx + 1]


def _engine_uses_metal(engine: str) -> bool:
    """该引擎在 Apple Silicon 上是否走 Metal（MPS）后端。

    实测（2026-10-23）：``ctranslate2.get_supported_compute_types("metal")`` 直接抛
    ``ValueError: unsupported device metal`` ⇒ **faster-whisper 在 Mac 上完全不可用**
    （不是慢，是没有该后端）。
    """
    return engine not in ("faster-whisper",)


def _whisper_cpp_available() -> bool:
    """whisper.cpp 二进制是否就绪（惰性导入，探测失败按不可用处理）。

    plan() 在把引擎切换成 whisper-cpp **之前**必须调用本函数 —— 切到
    一个没有二进制的引擎会让原本能跑的配置在转录期硬失败
    （2026-10-29 审查 P0-2：5018e97 回归）。测试可 patch 本函数。
    """
    try:
        from mediascribe.transcribers.whisper_cpp import is_available
    except Exception:  # pragma: no cover - 导入失败视同不可用
        return False
    return is_available()


def plan(
    engine: str,
    model: str,
    device: str = "auto",
    snap: Optional[HardwareSnapshot] = None,
) -> DegradeDecision:
    """按当前资源算出**该用什么**引擎与档位。

    规则（每条都对应一个实测依据）：

    0. **换引擎前先验可用性** —— whisper.cpp 二进制不可用时保留原引擎，
       只按资源降档并给出可操作通知（预置指引见 docs/engines.md）。
    1. **Apple Silicon + faster-whisper** ⇒ 换引擎（faster-whisper 无 Metal 后端）
    2. **CUDA 显存不足** ⇒ 按阶梯降模型档
    3. **系统内存不足** ⇒ 按阶梯降模型档
    4. **CPU 路径 + 内存紧张（< 4 GB）** ⇒ 换 whisper.cpp（最省内存，291 MiB）

    通知文本会随决策一起返回 —— 调用方负责把它送到用户眼前（日志 / Web 进度 /
    转写稿 metadata），**不能只记在日志里**。
    """
    snap = snap or snapshot()
    decision = DegradeDecision(engine=engine, model=model, device=device)
    notices = decision.notices
    reasons = decision.reasons
    cpp_ok = _whisper_cpp_available()

    # --- 规则 1：Apple Silicon ---
    if snap.is_apple_silicon and not _engine_uses_metal(engine):
        if cpp_ok:
            new_engine = "whisper-cpp"
            notices.append(
                f"当前是 Apple Silicon，而 {engine} 没有 Metal 后端"
                "（实测 ctranslate2 直接抛 unsupported device metal）"
                f" ⇒ 已改用 {new_engine}（内存占用最低，实测 tiny 仅 291 MiB）"
            )
            reasons.append("apple-silicon-no-metal")
            decision.engine = new_engine
            decision.changed = True
            engine = new_engine
        else:
            notices.append(
                f"当前是 Apple Silicon，而 {engine} 没有 Metal 后端；"
                "本应改用 whisper.cpp，但未检测到 whisper.cpp 可执行文件"
                " ⇒ 保留原引擎（转录可能失败）。预置方式见 docs/engines.md"
                "（MEDIASCRIBE_WHISPER_CPP_BIN）"
            )
            reasons.append("apple-silicon-no-metal-no-cpp-binary")

    # --- 规则 4：CPU + 机器内存偏小 ⇒ whisper.cpp ---
    # 用**总内存**（硬件能力，稳定可复现），不用瞬时可用内存。
    use_cuda = device == "cuda" or (device == "auto" and snap.cuda_available)
    if (
        not use_cuda
        and engine != "whisper-cpp"
        and snap.ram_total_mb
        and snap.ram_total_mb < MEMORY_PRESSURE_TOTAL_MB
    ):
        if cpp_ok:
            notices.append(
                f"机器总内存仅 {snap.ram_total_mb} MiB（压力线 {MEMORY_PRESSURE_TOTAL_MB} MiB）"
                f" ⇒ 已改用 whisper-cpp（实测峰值内存最低，tiny 仅 291 MiB）"
            )
            reasons.append("memory-pressure-engine-switch")
            decision.engine = "whisper-cpp"
            decision.changed = True
            engine = "whisper-cpp"
        else:
            # 不可用就保留原引擎，交给下方规则 2/3 按资源降档 ——
            # 不能把「能跑的配置」换成「必然失败的配置」（P0-2）。
            notices.append(
                f"机器总内存仅 {snap.ram_total_mb} MiB（压力线 {MEMORY_PRESSURE_TOTAL_MB} MiB）；"
                "本应改用 whisper.cpp，但未检测到 whisper.cpp 可执行文件"
                " ⇒ 保留原引擎并按资源降档。预置方式见 docs/engines.md"
                "（MEDIASCRIBE_WHISPER_CPP_BIN）"
            )
            reasons.append("memory-pressure-no-cpp-binary")

    # --- 规则 2/3：按资源降模型档 ---
    current_model = decision.model
    for _ in range(len(MODEL_LADDER)):
        if use_cuda:
            need = _estimate_vram(engine, current_model, "cuda")
            budget = int(snap.vram_free_mb * VRAM_HEADROOM)
            if not snap.vram_total_mb or need <= budget:
                break
            reason = f"可用显存 {snap.vram_free_mb} MiB（安全预算 {budget} MiB）< {current_model} 估算需求 {need} MiB"
        else:
            need = _estimate_ram(engine, current_model, "cpu")
            budget = snap.ram_available_mb
            # 滞回：需求超过「可用内存 × DEGRADE_HYSTERESIS」才降档，
            # 避免可用内存轻微波动就来回降档。
            if not snap.ram_available_mb or need <= budget * DEGRADE_HYSTERESIS:
                break
            reason = f"可用内存 {snap.ram_available_mb} MiB < {current_model} 估算需求 {need} MiB"
        nxt = _lower_model(current_model)
        if nxt is None:
            notices.append(f"{reason}；已是最小档位（{current_model}），仍可能吃紧")
            reasons.append("ladder-exhausted")
            break
        notices.append(f"{reason} ⇒ 已降档 {current_model} → {nxt}")
        reasons.append("model-degraded")
        decision.model = nxt
        decision.changed = True
        current_model = nxt

    return decision


# ---------------------------------------------------------------------------
# 转录中：过载监测
# ---------------------------------------------------------------------------


class ResourcePressureError(RuntimeError):
    """转录期间资源越过紧急线 —— 结果可能不完整，**不交付**。"""


class RuntimeMonitor:
    """转录期间的后台采样线程。

    用法::

        with RuntimeMonitor(cancel_event=ctx.cancel_event) as mon:
            transcription = transcriber.transcribe(...)
        if mon.pressured:
            raise ResourcePressureError(mon.pressure_reason)

    说明：Python 无法安全地中断一个正在跑的 C 扩展调用，因此本监控能做的是
    ① 及时**通知** ② 置 ``cancel_event`` 让后续阶段尽快退出
    ③ 转录结束后**判定结果不可信**并抛错。真正的强制终止由外部（用户
    Ctrl-C / 进程被杀）负责。
    """

    def __init__(
        self,
        cancel_event: Optional[threading.Event] = None,
        on_notice: Optional[Callable[[str], None]] = None,
        *,
        interval_s: float = 2.0,
        ram_abort_mb: int = RAM_ABORT_MB,
        vram_abort_pct: float = VRAM_ABORT_PCT,
        gpu_temp_abort_c: float = GPU_TEMP_ABORT_C,
    ):
        self.cancel_event = cancel_event
        self.on_notice = on_notice
        self.interval_s = interval_s
        self.ram_abort_mb = ram_abort_mb
        self.vram_abort_pct = vram_abort_pct
        self.gpu_temp_abort_c = gpu_temp_abort_c
        self.pressured = False
        self.pressure_reason = ""
        self.notices: List[str] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- 通知 --------------------------------------------------------------
    def _notify(self, message: str) -> None:
        self.notices.append(message)
        logger.warning("资源守卫: %s", message)
        if self.on_notice is not None:
            try:
                self.on_notice(message)
            except Exception:
                pass  # 通知失败不应影响转录

    # -- 采样 --------------------------------------------------------------
    def _sample(self) -> Optional[str]:
        try:
            import psutil  # type: ignore

            avail = psutil.virtual_memory().available / 1024 / 1024
        except Exception:
            avail = None
        if avail is not None and avail < self.ram_abort_mb:
            return f"系统可用内存 {avail:.0f} MiB 跌破紧急线 {self.ram_abort_mb} MiB"

        rows = _nvidia_smi("memory.used,memory.total,temperature.gpu")
        if rows:
            try:
                used, total, temp = (int(float(x)) for x in rows[0].split(","))
            except Exception:
                rows = []
            if rows:
                if total and used / total * 100 >= self.vram_abort_pct:
                    return (
                        f"显存占用 {used}/{total} MiB（{used / total * 100:.0f}%）"
                        f"越过紧急线 {self.vram_abort_pct:.0f}%"
                    )
                if temp and temp >= self.gpu_temp_abort_c:
                    return f"GPU 温度 {temp}℃ 越过紧急线 {self.gpu_temp_abort_c}℃"
        return None

    # -- 线程 --------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            reason = self._sample()
            if reason is None:
                continue
            if not self.pressured:
                self.pressured = True
                self.pressure_reason = reason
                self._notify(f"检测到资源过载：{reason} ⇒ 请求中止本次转录")
                if self.cancel_event is not None:
                    self.cancel_event.set()
            else:
                # 持续过载时不再重复刷屏
                self.pressure_reason = reason

    def start(self) -> "RuntimeMonitor":
        self._thread = threading.Thread(
            target=self._run, name="mediascribe-resource-guard", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def __enter__(self) -> "RuntimeMonitor":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
