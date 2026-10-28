"""
Runtime resource guard — hardware self-adaptation (automatic degrade)
plus overload protection (monitoring and safe abort).

Why it exists
-------------
MediaScribe runs on **users' machines**, and those machines span a huge
range: an 8 GB-RAM old laptop (no discrete GPU), a 16 GB + 8 GB-VRAM
gaming laptop, a 32 GB + 16 GB-VRAM workstation, a Mac. The same
configuration can range from "done in seconds" to "OOM crash" depending
on the machine.

Local measurements from 2026-10-23 provide the decision basis
(CPU-tier peak RAM / CUDA-tier peak VRAM):

======================  ==============  ==============  ==============
Engine / tier           Peak VRAM       Peak RAM        Note
======================  ==============  ==============  ==============
faster-whisper small    614 MiB        1167 MiB        CUDA int8_float16
faster-whisper large-v3 2816 MiB       3757 MiB        CUDA int8_float16
faster-whisper small    —              952 MiB         CPU int8
faster-whisper large-v3 —              3613 MiB        CPU int8
openai-whisper small    1965 MiB       1963 MiB        CUDA fp16
openai-whisper medium   4774 MiB       4074 MiB        CUDA fp16
**whisper.cpp tiny**     —              **291 MiB**     CPU ggml
======================  ==============  ==============  ==============

⇒ whisper.cpp is the **most memory-frugal** (291 MiB); on a 4 GB machine
it is the only viable option.

Two defense layers
------------------
1. **Before transcription (:func:`plan`)** — automatically **degrade the
   model tier** or **switch engines** (Apple Silicon / memory pressure)
   based on current resources, and produce **human-readable notices**.
2. **During transcription (:class:`RuntimeMonitor`)** — a background
   thread keeps sampling memory / VRAM / temperature; past an abort line
   it **notifies + requests cancellation** (best effort). If a real
   overload occurred, transcription ends by raising
   :class:`ResourcePressureError` — **a possibly incomplete result is
   never delivered**.

The estimation tables are the **single source**: :mod:`scripts.bench.asr.bench_asr`
imports this module's data ⇒ benchmark and product never diverge.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .i18n import t

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
    """Hardware and resource levels at one moment."""

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


_NVIDIA_SMI_PATH: Optional[str] = None


def _nvidia_smi_path() -> Optional[str]:
    """Resolve ``nvidia-smi`` once per process.

    之前每次采样都重新扫全 PATH(RT-1 小时转录 = 1800 次 PATH 扫描,
    2026-10-29 审查 P2);which 结果进程内不变,缓存一次即可。
    """
    global _NVIDIA_SMI_PATH
    if _NVIDIA_SMI_PATH is None:
        import shutil

        _NVIDIA_SMI_PATH = shutil.which("nvidia-smi") or ""
    return _NVIDIA_SMI_PATH or None


def _nvidia_smi(query: str) -> List[str]:
    import subprocess

    smi = _nvidia_smi_path()
    if not smi:
        return []
    try:
        proc = subprocess.run(
            [smi, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
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
    """Collect current hardware and resource levels (probe failures are
    recorded, never raised)."""
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
            # 顺带填 used —— 旧实现只填 total,vram_free_mb 恒等于总量,
            # torch 探测路径上规则 2 的显存预算永远偏乐观。
            try:
                free_b, total_b = torch.cuda.mem_get_info(0)
                snap.vram_used_mb = int((total_b - free_b) / 1024 / 1024)
            except Exception:
                pass  # 极老 torch 无 mem_get_info: 保持 0, 与旧行为一致
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
    """Result of a degrade decision."""

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
    """Return the next lighter tier; ``None`` when already the lightest.

    Position is resolved by **family** (``large-v2`` maps to the
    ``large-v3`` family) ⇒ each family degrades at most once, never spins
    in place on the ladder.
    """
    fam = _family(model)
    if fam not in MODEL_LADDER:
        return None
    idx = MODEL_LADDER.index(fam)
    if idx + 1 >= len(MODEL_LADDER):
        return None
    return MODEL_LADDER[idx + 1]


def _engine_uses_metal(engine: str) -> bool:
    """Whether the engine uses the Metal (MPS) backend on Apple Silicon.

    Measured (2026-10-23): ``ctranslate2.get_supported_compute_types("metal")``
    raises ``ValueError: unsupported device metal`` outright ⇒
    **faster-whisper is entirely unusable on Macs** (not slow — the
    backend does not exist).
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
                t(
                    "RUNTIME_APPLE_SWITCH",
                    engine=engine,
                    new_engine=new_engine,
                )
            )
            reasons.append("apple-silicon-no-metal")
            decision.engine = new_engine
            decision.changed = True
            engine = new_engine
        else:
            notices.append(t("RUNTIME_APPLE_NO_CPP", engine=engine))
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
                t(
                    "RUNTIME_MEM_SWITCH",
                    ram=snap.ram_total_mb,
                    limit=MEMORY_PRESSURE_TOTAL_MB,
                )
            )
            reasons.append("memory-pressure-engine-switch")
            decision.engine = "whisper-cpp"
            decision.changed = True
            engine = "whisper-cpp"
        else:
            # 不可用就保留原引擎，交给下方规则 2/3 按资源降档 ——
            # 不能把「能跑的配置」换成「必然失败的配置」（P0-2）。
            notices.append(
                t(
                    "RUNTIME_MEM_NO_CPP",
                    ram=snap.ram_total_mb,
                    limit=MEMORY_PRESSURE_TOTAL_MB,
                )
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
            reason = t(
                "RUNTIME_VRAM_SHORT",
                free=snap.vram_free_mb,
                budget=budget,
                model=current_model,
                need=need,
            )
        else:
            need = _estimate_ram(engine, current_model, "cpu")
            budget = snap.ram_available_mb
            # 滞回：需求超过「可用内存 × DEGRADE_HYSTERESIS」才降档，
            # 避免可用内存轻微波动就来回降档。
            if not snap.ram_available_mb or need <= budget * DEGRADE_HYSTERESIS:
                break
            reason = t(
                "RUNTIME_RAM_SHORT",
                free=snap.ram_available_mb,
                model=current_model,
                need=need,
            )
        nxt = _lower_model(current_model)
        if nxt is None:
            notices.append(t("RUNTIME_LADDER_FLOOR", reason=reason, model=current_model))
            reasons.append("ladder-exhausted")
            break
        notices.append(t("RUNTIME_DEGRADED", reason=reason, old=current_model, new=nxt))
        reasons.append("model-degraded")
        decision.model = nxt
        decision.changed = True
        current_model = nxt

    return decision


# ---------------------------------------------------------------------------
# 转录中：过载监测
# ---------------------------------------------------------------------------


class ResourcePressureError(RuntimeError):
    """Resources crossed an abort line during transcription — the result
    may be incomplete and is **never delivered**."""


class RuntimeMonitor:
    """Background sampling thread active during transcription.

    Usage::

        with RuntimeMonitor(cancel_event=ctx.cancel_event) as mon:
            transcription = transcriber.transcribe(...)
        if mon.pressured:
            raise ResourcePressureError(mon.pressure_reason)

    Note: Python cannot safely interrupt a running C-extension call, so
    this monitor can only ① notify in time ② set ``cancel_event`` so
    later stages exit soon ③ declare the result untrustworthy after
    transcription ends and raise. Actual force-kill is left to the
    outside (user Ctrl-C / process kill).
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
        logger.warning(t("RUNTIME_LOG_PREFIX", message=message))
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
            return t("RUNTIME_RAM_ABORT", avail=avail, mb=self.ram_abort_mb)

        rows = _nvidia_smi("memory.used,memory.total,temperature.gpu")
        if rows:
            try:
                used, total, temp = (int(float(x)) for x in rows[0].split(","))
            except Exception:
                rows = []
            if rows:
                if total and used / total * 100 >= self.vram_abort_pct:
                    return t(
                        "RUNTIME_VRAM_ABORT",
                        used=used,
                        total=total,
                        pct=used / total * 100,
                        limit=self.vram_abort_pct,
                    )
                if temp and temp >= self.gpu_temp_abort_c:
                    return t("RUNTIME_TEMP_ABORT", temp=temp, limit=self.gpu_temp_abort_c)
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
                self._notify(t("RUNTIME_OVERLOAD", reason=reason))
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
