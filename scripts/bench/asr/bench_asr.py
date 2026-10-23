"""
L3 推理引擎层基准 —— 硬件自适应 + 资源护栏版。

为什么要有「自适应」和「护栏」
------------------------------
MediaScribe 是**完全离线**产品，用户机器从「无独显老笔记本」到「16GB 显存桌面卡」
到「M 系列 Mac」都可能是。如果基准脚本写死 ``--model large-v3 --device cuda``，
在 4GB 显存机器上会直接 OOM，在无独显机器上会静默退化成极慢的 CPU 推理 ——
**测出来的数字没有跨机可比性**。

因此本脚本分三层：

1. **探测层**（:class:`HardwareProfile`）：如实记录 CPU / RAM / GPU / 显存 / 温度，
   写进 results.json，作为结论的适用边界。
2. **规划层**（:func:`plan_matrix`）：按探测到的资源自动决定跑哪些
   ``(engine, model, device, precision)`` 组合，并在**显存/内存不足时降档或跳过**
   而不是硬跑。
3. **护栏层**（:class:`Guard`）：跑每一轮之前与运行期间持续检查

   * 显存水位（不足则不加载模型）
   * 可用内存（不足则跳过大模型）
   * GPU 温度（超阈值则等待降温，超硬阈值则中止）
   * 单轮墙钟超时（熔断，避免卡死的推理拖垮整场基准）
   * 线程数限流（不占满全部核心，给系统留出散热余量）

跑法
----
::

    # 自动按本机能力规划（推荐）
    python scripts/bench/asr/bench_asr.py --corpus output/bench/corpus

    # 只跑能跑的档位，显式指定
    python scripts/bench/asr/bench_asr.py --corpus output/bench/corpus \\
        --engines faster_whisper --models small --devices cpu

    # 复现某一轮（worker 模式，主进程自动调用）
    python scripts/bench/asr/bench_asr.py --worker --engine faster_whisper \\
        --model small --device cpu --file 01_zh_huihui.wav

产物落在 ``output/bench/asr/``（被 .gitignore 忽略），入库的只有本脚本。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "benchmark_corpus"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "bench" / "asr"))

try:
    from zh_normalize import normalize_cjk  # type: ignore
except Exception:  # pragma: no cover - 归一化不可用时退化为原样比较

    def normalize_cjk(text: str, language: str) -> str:  # type: ignore
        return text

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

BENCH_SCHEMA = "mediascribe-benchmark/v2-asr"


# ===========================================================================
# 1. 探测层
# ===========================================================================


@dataclass
class HardwareProfile:
    """本机硬件与软件实况 —— 结论适用边界的依据。"""

    platform: str = ""
    python: str = ""
    cpu_brand: str = ""
    physical_cores: int = 0
    logical_cores: int = 0
    ram_total_mb: int = 0
    ram_available_mb: int = 0
    gpu_name: str = ""
    gpu_vram_total_mb: int = 0
    gpu_vram_free_mb: int = 0
    cuda_available: bool = False
    cuda_version: str = ""
    torch_version: str = ""
    ctranslate2_version: str = ""
    nvidia_smi: bool = False
    gpu_temp_c: int = 0
    gpu_power_w: float = 0.0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _run(cmd: List[str], timeout: int = 20) -> Optional[str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode == 0:
            return proc.stdout
    except Exception:
        pass
    return None


def _nvidia_smi(query: str) -> List[str]:
    out = _run(
        [
            "nvidia-smi",
            f"--query-gpu={query}",
            "--format=csv,noheader,nounits",
        ]
    )
    if not out:
        return []
    return [line.strip() for line in out.strip().splitlines() if line.strip()]


def detect_hardware() -> HardwareProfile:
    """探测本机硬件。任何一项探测失败都只记录、不抛异常 —— 缺一项不该毁掉整场基准。"""
    prof = HardwareProfile()
    prof.platform = platform.platform()
    prof.python = sys.version.split()[0]

    # --- CPU ---
    try:
        import multiprocessing

        prof.logical_cores = multiprocessing.cpu_count() or 0
    except Exception:
        prof.logical_cores = os.cpu_count() or 0

    brand = platform.processor()
    if psutil is not None:
        try:
            prof.physical_cores = psutil.cpu_count(logical=False) or 0
            prof.logical_cores = psutil.cpu_count(logical=True) or 0
            freq = None
            try:
                freq = psutil.cpu_freq()
            except Exception:
                freq = None
            prof.cpu_brand = brand or "unknown"
            if freq and freq.max:
                prof.cpu_brand = f"{prof.cpu_brand} @ {freq.max:.0f}MHz"
            vm = psutil.virtual_memory()
            prof.ram_total_mb = int(vm.total / 1024 / 1024)
            prof.ram_available_mb = int(vm.available / 1024 / 1024)
        except Exception as exc:  # pragma: no cover
            prof.notes.append(f"psutil 探测部分失败: {exc}")
    if not prof.cpu_brand:
        prof.cpu_brand = brand or "unknown"

    # --- GPU ---
    try:
        import torch  # type: ignore

        prof.torch_version = torch.__version__
        prof.cuda_available = bool(torch.cuda.is_available())
        if prof.cuda_available:
            prof.cuda_version = str(getattr(torch.version, "cuda", "") or "")
            idx = torch.cuda.current_device()
            props = torch.cuda.get_device_properties(idx)
            prof.gpu_name = props.name
            try:
                free, total = torch.cuda.mem_get_info(idx)
                prof.gpu_vram_total_mb = int(total / 1024 / 1024)
                prof.gpu_vram_free_mb = int(free / 1024 / 1024)
            except Exception:
                prof.gpu_vram_total_mb = int(props.total_memory / 1024 / 1024)
    except Exception as exc:
        prof.notes.append(f"torch 探测失败: {exc}")

    try:
        import ctranslate2  # type: ignore

        prof.ctranslate2_version = ctranslate2.__version__
    except Exception:
        pass

    # nvidia-smi 兜底（torch 不可用时也能拿到显存与温度）
    if shutil.which("nvidia-smi"):
        prof.nvidia_smi = True
        mem = _nvidia_smi("memory.total,memory.used")
        if mem and not prof.gpu_vram_total_mb:
            try:
                total_mb, used_mb = (int(x) for x in mem[0].split(","))
                prof.gpu_vram_total_mb = total_mb
                prof.gpu_vram_free_mb = max(0, total_mb - used_mb)
            except Exception:
                pass
        if not prof.gpu_name:
            names = _nvidia_smi("name")
            if names:
                prof.gpu_name = names[0]
        temp = _nvidia_smi("temperature.gpu")
        if temp:
            try:
                prof.gpu_temp_c = int(float(temp[0]))
            except Exception:
                pass
        power = _nvidia_smi("power.draw")
        if power:
            try:
                prof.gpu_power_w = float(power[0])
            except Exception:
                pass

    return prof


# ===========================================================================
# 2. 规划层 —— 按硬件能力决定跑什么
# ===========================================================================

#: 各 (engine, model, precision) 的**估算**常驻显存（MiB）。
#: 经验值 + 实测校准，用于在加载前就判定「这台机器跑不跑得动」。
VRAM_ESTIMATE_MB: Dict[Tuple[str, str, str], int] = {
    ("faster_whisper", "small", "int8"): 700,
    ("faster_whisper", "small", "int8_float16"): 800,
    ("faster_whisper", "medium", "int8"): 1400,
    ("faster_whisper", "medium", "int8_float16"): 1600,
    ("faster_whisper", "medium", "float16"): 2600,
    ("faster_whisper", "large-v3", "int8"): 2200,
    ("faster_whisper", "large-v3", "int8_float16"): 2800,
    ("faster_whisper", "large-v3", "float16"): 5200,
    ("openai_whisper", "small", "float16"): 1200,
    ("openai_whisper", "medium", "float16"): 2000,
    ("openai_whisper", "large-v3", "float16"): 3600,
}

#: CPU 推理时的**估算**常驻内存（MiB）——比 GPU 档更吃内存（还要放权重 + 激活）。
RAM_ESTIMATE_MB: Dict[Tuple[str, str, str], int] = {
    ("faster_whisper", "small", "int8"): 1200,
    ("faster_whisper", "medium", "int8"): 2200,
    ("faster_whisper", "large-v3", "int8"): 4200,
    ("openai_whisper", "small", "float32"): 1600,
    ("openai_whisper", "medium", "float32"): 3200,
    ("openai_whisper", "large-v3", "float32"): 6800,
}

#: 模型规模排序 —— 用于按资源降档
MODEL_SCALE = {"tiny": 1, "base": 2, "small": 3, "medium": 4, "large-v3": 5}


@dataclass
class PlanItem:
    """一轮待跑的组合。"""

    engine: str
    model: str
    device: str
    precision: str
    language: str = "zh"
    enabled: bool = True
    skip_reason: str = ""

    def key(self) -> str:
        return f"{self.engine}|{self.model}|{self.device}|{self.precision}"


def _default_precision(engine: str, device: str) -> str:
    if engine == "faster_whisper":
        return "int8_float16" if device == "cuda" else "int8"
    return "float16" if device == "cuda" else "float32"


def plan_matrix(
    prof: HardwareProfile,
    engines: Sequence[str],
    models: Sequence[str],
    devices: Sequence[str],
    *,
    vram_headroom: float = 1.35,
    min_vram_free_mb: int = 1500,
    min_ram_free_mb: int = 3000,
    enable_float16: bool = False,
) -> List[PlanItem]:
    """按硬件能力规划矩阵，并对跑不动的组合**显式标记 skip_reason**。

    降档策略（从重到轻）：

    * CUDA 显存不足 → 优先降精度（float16 → int8_float16 → int8），仍不足则跳过
    * 无 CUDA → 只跑 CPU
    * 可用内存不足 → 跳过大模型的 CPU 轮（会 OOM）
    * 显式要求 float16 对比时，只有显存 ≥ 12GB 才排入矩阵
    """
    plan: List[PlanItem] = []

    for engine in engines:
        for model in models:
            for device in devices:
                if device == "cuda" and not prof.cuda_available:
                    plan.append(
                        PlanItem(engine, model, device, _default_precision(engine, device),
                                 enabled=False, skip_reason="本机无 CUDA 设备")
                    )
                    continue
                if device == "cuda" and prof.gpu_vram_free_mb < min_vram_free_mb:
                    plan.append(
                        PlanItem(engine, model, device, _default_precision(engine, device),
                                 enabled=False,
                                 skip_reason=f"可用显存 {prof.gpu_vram_free_mb}MiB < 下限 {min_vram_free_mb}MiB")
                    )
                    continue

                precisions = [_default_precision(engine, device)]
                if device == "cuda" and enable_float16 and prof.gpu_vram_total_mb >= 12000:
                    precisions.insert(0, "float16")

                for prec in precisions:
                    item = PlanItem(engine, model, device, prec)
                    if device == "cuda":
                        need = VRAM_ESTIMATE_MB.get((engine, model, prec), 3000)
                        budget = int(prof.gpu_vram_free_mb / vram_headroom)
                        if need > budget:
                            # 尝试降精度
                            fallback = None
                            for alt in ("int8_float16", "int8"):
                                alt_need = VRAM_ESTIMATE_MB.get((engine, model, alt))
                                if alt_need and alt_need <= budget:
                                    fallback = alt
                                    break
                            if fallback:
                                item.precision = fallback
                                need = VRAM_ESTIMATE_MB[(engine, model, fallback)]
                                item.skip_reason = (
                                    f"精度降档：{prec} 需 {need}MiB > 预算 {budget}MiB → {fallback}"
                                )
                            else:
                                item.enabled = False
                                item.skip_reason = (
                                    f"显存不足：最轻档 {need}MiB > 预算 {budget}MiB"
                                )
                    else:
                        need = RAM_ESTIMATE_MB.get((engine, model, prec), 3000)
                        if need > prof.ram_available_mb:
                            item.enabled = False
                            item.skip_reason = (
                                f"可用内存 {prof.ram_available_mb}MiB < 估算需求 {need}MiB"
                            )
                    plan.append(item)
    return plan


# ===========================================================================
# 3. 护栏层
# ===========================================================================


class Guard:
    """跑基准时的资源护栏。

    目标：**绝不让基准把用户的笔记本压垮**。具体手段：

    * ``guard.check_before_run()`` —— 跑前查显存/内存/温度，不达标直接熔断
    * ``guard.sample()`` —— 跑中采样显存/内存/温度，触发硬阈值则中止 worker
    * ``guard.cooldown()`` —— 轮次之间等待降温，给散热留余量
    """

    def __init__(
        self,
        prof: HardwareProfile,
        *,
        max_gpu_temp_c: int = 82,
        hard_gpu_temp_c: int = 92,
        vram_abort_pct: float = 97.0,
        sys_ram_abort_mb: int = 512,
        sys_ram_start_mb: int = 2048,
        cpu_abort_pct: float = 97.0,
        cpu_abort_seconds: float = 20.0,
        disk_min_free_mb: int = 2048,
        proc_rss_floor_mb: int = 120,
        timeout_s: int = 1800,
        cool_target_c: int = 72,
        cpu_freq_drop_ratio: float = 0.78,
    ):
        self.prof = prof
        self.max_gpu_temp_c = max_gpu_temp_c
        self.hard_gpu_temp_c = hard_gpu_temp_c
        self.vram_abort_pct = vram_abort_pct
        self.sys_ram_abort_mb = sys_ram_abort_mb
        self.sys_ram_start_mb = sys_ram_start_mb
        self.cpu_abort_pct = cpu_abort_pct
        self.cpu_abort_seconds = cpu_abort_seconds
        self.disk_min_free_mb = disk_min_free_mb
        self.proc_rss_floor_mb = proc_rss_floor_mb
        self.timeout_s = timeout_s
        self.cool_target_c = cool_target_c
        self.cpu_freq_drop_ratio = cpu_freq_drop_ratio
        self.events: List[Dict[str, Any]] = []
        self._cpu_overload_since: Optional[float] = None
        self._disk_checked = False
        self._max_freq_seen = 0.0

    # -- 事件记录 ----------------------------------------------------------
    def _event(self, kind: str, msg: str, **extra: Any) -> None:
        self.events.append({"kind": kind, "msg": msg, **extra})
        print(f"  [guard:{kind}] {msg}", flush=True)

    # -- 系统级水位探测 ---------------------------------------------------
    def sys_ram_free_mb(self) -> int:
        """系统**可用**内存（MiB）。

        这是本护栏最关键的一项：Windows 内存耗尽不会只杀掉基准进程，
        而是把整机拖垮 —— 连同用户同时开着的其它会话。
        """
        if psutil is None:
            return 10**9
        try:
            return int(psutil.virtual_memory().available / 1024 / 1024)
        except Exception:
            return 10**9

    def cpu_load_pct(self) -> float:
        """系统 CPU 使用率（0-100）。"""
        if psutil is None:
            return 0.0
        try:
            return float(psutil.cpu_percent(interval=None))
        except Exception:
            return 0.0

    def disk_free_mb(self, path: Path) -> int:
        """目标目录所在磁盘的剩余空间（MiB）。"""
        try:
            usage = shutil.disk_usage(str(path))
            return int(usage.free / 1024 / 1024)
        except Exception:
            return 10**9

    def cpu_freq_state(self) -> Tuple[float, float]:
        """返回 (当前 MHz, 最大 MHz)；不可用返回 (0, 0)。

        **降频代理**：笔记本 CPU 过热时会先降频再降速，硬读 ACPI 温度在
        Windows 下极不准，而频率下跌是跨平台、无需管理员权限的可靠信号。
        """
        if psutil is None:
            return (0.0, 0.0)
        try:
            freq = psutil.cpu_freq()
            if freq is None:
                return (0.0, 0.0)
            return (float(freq.current or 0.0), float(freq.max or 0.0))
        except Exception:
            return (0.0, 0.0)

    def overload_reason(self, out_dir: Optional[Path] = None) -> str:
        """返回第一个触发的**系统级**过载原因；无过载返回空串。

        阈值用**运行中紧急线**（``sys_ram_abort_mb``）—— 真正危险的临界点。
        跑前另有更高的**就绪线**（``sys_ram_start_mb``），保证启动时留足余量。
        两条线必须分开：推理本身要吃内存，可用内存会短暂下降，
        若两者同值，边缘状态会反复触发误杀。
        """
        # 1) 系统可用内存 —— 优先级最高
        free_ram = self.sys_ram_free_mb()
        if free_ram < self.sys_ram_abort_mb:
            return f"系统可用内存 {free_ram}MiB < 紧急线 {self.sys_ram_abort_mb}MiB"

        # 2) 磁盘余量（长时间基准会写出大量中间文件）
        if out_dir is not None and not self._disk_checked:
            self._disk_checked = True
            disk_free = self.disk_free_mb(out_dir)
            if disk_free < self.disk_min_free_mb:
                return f"磁盘剩余 {disk_free}MiB < 下限 {self.disk_min_free_mb}MiB"

        # 3) CPU 持续打满 —— 笔记本的降频保护
        load = self.cpu_load_pct()
        if load >= self.cpu_abort_pct:
            if self._cpu_overload_since is None:
                self._cpu_overload_since = time.time()
            elif time.time() - self._cpu_overload_since >= self.cpu_abort_seconds:
                return f"CPU 持续占用 {load:.0f}% ≥ {self.cpu_abort_pct}% 超过 {self.cpu_abort_seconds:.0f}s"
        else:
            self._cpu_overload_since = None

        # 4) CPU 过热降频代理 —— 与**本机自身**观测到的最高频率比，
        #    而不是与理论最大值比（笔记本轻负载时频率本来就低，
        #    用理论最大值会误判）。
        cur, mx = self.cpu_freq_state()
        if cur > 0:
            self._max_freq_seen = max(self._max_freq_seen, cur)
            ref = max(mx, self._max_freq_seen)
            if ref > 0 and cur / ref < self.cpu_freq_drop_ratio:
                return (
                    f"CPU 频率 {cur:.0f}MHz 低于本机观测峰值 {ref:.0f}MHz 的 "
                    f"{self.cpu_freq_drop_ratio:.0%}（疑似过热降频）"
                )

        return ""

    def safe_stop(self, proc: "subprocess.Popen") -> str:
        """安全中止 worker：先温和终止，超时再强杀。

        返回实际采取的动作，供结果文件记录（区分"温和中止"与"强杀"）。
        """
        try:
            proc.terminate()
            try:
                proc.wait(timeout=8)
                return "terminate"
            except Exception:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                return "kill"
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            return "kill"

    # -- GPU 状态 -----------------------------------------------------------
    def gpu_state(self) -> Tuple[int, int, int]:
        """返回 (温度℃, 显存已用 MiB, 显存总量 MiB)；不可用返回 (0, 0, 0)。"""
        if not self.prof.nvidia_smi:
            return (0, 0, 0)
        out = _nvidia_smi("temperature.gpu,memory.used,memory.total")
        if not out:
            return (0, 0, 0)
        try:
            temp, used, total = (int(float(x)) for x in out[0].split(","))
            return (temp, used, total)
        except Exception:
            return (0, 0, 0)

    def gpu_temp(self) -> int:
        return self.gpu_state()[0]

    # -- 跑前检查 -----------------------------------------------------------
    def wait_for_capacity(
        self,
        item: PlanItem,
        out_dir: Optional[Path] = None,
        max_wait_s: float = 300.0,
        poll_s: float = 10.0,
    ) -> Tuple[bool, str]:
        """资源不足时**等待就绪**而不是立刻放弃。

        为什么要有等待：同一台机器上常同时跑着别的任务（用户的编辑器、
        其它会话、浏览器），内存占用是**波动的** —— 等一会往往就释放了。
        直接拒绝会把基准变成"看运气"，等待则让它自适应机器的忙闲。

        返回 (是否放行, 原因)。等待超时后返回 False 并说明仍然不满足的原因。
        """
        deadline = time.time() + max_wait_s
        reason = ""
        waited = 0.0
        while True:
            ok, why = self.check_before_run(item, out_dir)
            if ok:
                if waited:
                    self._event("wait", f"等待 {waited:.0f}s 后资源就绪")
                return True, ""
            reason = why
            if time.time() >= deadline:
                return False, f"等待 {max_wait_s:.0f}s 后仍不满足条件：{reason}"
            print(f"  [guard] 资源未就绪，等待中（{reason}）", flush=True)
            time.sleep(poll_s)
            waited += poll_s

    def check_before_run(self, item: PlanItem, out_dir: Optional[Path] = None) -> Tuple[bool, str]:
        """跑前就绪检查。返回 (是否放行, 拒绝原因)。

        这里用**就绪线**（比运行中紧急线高）—— 启动时要留足余量，
        否则模型加载到一半才发现内存不够。
        """
        if item.device == "cuda":
            temp, used, total = self.gpu_state()
            if temp and temp >= self.hard_gpu_temp_c:
                return (False, f"GPU 温度 {temp}℃ ≥ 硬阈值 {self.hard_gpu_temp_c}℃，拒绝启动")
            if total:
                used_pct = used / total * 100
                if used_pct >= self.vram_abort_pct:
                    return (False, f"显存占用 {used_pct:.1f}% ≥ 上限 {self.vram_abort_pct}%，拒绝启动")
        free_ram = self.sys_ram_free_mb()
        if free_ram < self.sys_ram_start_mb:
            return (False, f"系统可用内存 {free_ram}MiB < 就绪线 {self.sys_ram_start_mb}MiB")
        if out_dir is not None:
            disk_free = self.disk_free_mb(out_dir)
            if disk_free < self.disk_min_free_mb:
                return (False, f"磁盘剩余 {disk_free}MiB < 下限 {self.disk_min_free_mb}MiB")
        return (True, "")

    # -- 跑中采样 -----------------------------------------------------------
    def sample(self) -> Dict[str, Any]:
        """采一次样：GPU 温度/显存 + 本进程内存 + 系统级水位。"""
        temp, used, total = self.gpu_state()
        rss = 0
        if psutil is not None:
            try:
                rss = int(psutil.Process().memory_info().rss / 1024 / 1024)
            except Exception:
                rss = 0
        cur_freq, max_freq = self.cpu_freq_state()
        return {
            "gpu_temp_c": temp,
            "gpu_used_mb": used,
            "gpu_total_mb": total,
            "rss_mb": rss,
            "sys_ram_free_mb": self.sys_ram_free_mb(),
            "cpu_load_pct": self.cpu_load_pct(),
            "cpu_freq_mhz": cur_freq,
            "cpu_freq_max_mhz": max_freq,
        }

    def should_abort(
        self, snap: Dict[str, Any], out_dir: Optional[Path] = None, elapsed_s: float = 0.0
    ) -> str:
        """判断是否该中止本轮。返回空串表示继续。

        ``elapsed_s`` 是启动宽限期：worker 刚拉起时 RSS 天然很低（Python 解释器
        还在加载），过早用「内存异常偏低」判死会误杀正常的启动过程。
        """
        temp = snap.get("gpu_temp_c", 0)
        if temp and temp >= self.hard_gpu_temp_c:
            return f"GPU 温度 {temp}℃ ≥ 硬阈值 {self.hard_gpu_temp_c}℃"
        used, total = snap.get("gpu_used_mb", 0), snap.get("gpu_total_mb", 0)
        if total and used / total * 100 >= self.vram_abort_pct:
            return f"显存占用 {used}/{total}MiB 超限"
        if elapsed_s >= 15 and snap.get("rss_mb", 0) < self.proc_rss_floor_mb:
            return "进程内存异常偏低（疑似 worker 已退出）"
        return self.overload_reason(out_dir)

    # -- 降温 ---------------------------------------------------------------
    def cooldown(self, seconds: float = 3.0) -> None:
        """轮次之间等待降温：GPU 超软阈值就等，CPU 降频则额外让出核心。"""
        if not self.prof.nvidia_smi:
            time.sleep(seconds)
            return
        deadline = time.time() + 300
        while time.time() < deadline:
            temp = self.gpu_temp()
            if temp <= self.cool_target_c:
                break
            self._event(
                "cooldown",
                f"GPU {temp}℃ > 冷却目标 {self.cool_target_c}℃，等待降温",
            )
            time.sleep(10)
        cur, mx = self.cpu_freq_state()
        if cur and mx and cur / mx < self.cpu_freq_drop_ratio:
            self._event(
                "cooldown",
                f"CPU 频率 {cur:.0f}MHz/{mx:.0f}MHz 偏低（降频中），额外让出 {seconds * 2:.0f}s",
            )
            time.sleep(seconds * 2)
        time.sleep(seconds)

    # -- 线程限流 -----------------------------------------------------------
    def limit_threads(self) -> int:
        """给推理留核心但不全占满 —— 笔记本散热余量。"""
        cores = self.prof.physical_cores or max(1, (self.prof.logical_cores or 2) // 2)
        n = max(1, cores // 2)
        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
            os.environ.setdefault(var, str(n))
        try:
            import torch  # type: ignore

            torch.set_num_threads(n)
        except Exception:
            pass
        return n


# ===========================================================================
# 4. CER / WER
# ===========================================================================


def _levenshtein(a: Sequence[str], b: Sequence[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[len(b)]


def tokenize(text: str, language: str) -> List[str]:
    """按语言切 token：中文按字、英文按词、中英混说混合切。"""
    import re

    text = text.strip()
    if language == "en":
        return re.findall(r"[a-z0-9']+", text.lower())
    if language == "mixed":
        toks = re.findall(r"[A-Za-z0-9']+|[^\s]", text)
        return [t.lower() if t.isascii() else t for t in toks if re.search(r"[0-9A-Za-z\u4e00-\u9fff]", t)]
    return [c for c in text if re.search(r"[0-9A-Za-z\u4e00-\u9fff]", c)]


def error_rate(reference: str, hypothesis: str, language: str) -> Tuple[float, int, int]:
    """返回 (错误率 %, 参考 token 数, 编辑距离)。中文=字错率 CER，英文=词错率 WER。

    **中英混说与中文都要先做繁简归一化** —— whisper.cpp 的中文输出是繁体，
    不归一化直接做字级比较会得到 80%+ 的假错误率（实测）。详见
    :mod:`scripts.bench.asr.zh_normalize`。
    """
    ref = tokenize(normalize_cjk(reference, language), language)
    hyp = tokenize(normalize_cjk(hypothesis, language), language)
    if not ref:
        return (0.0, 0, 0)
    dist = _levenshtein(ref, hyp)
    return (round(100.0 * dist / len(ref), 2), len(ref), dist)


# ===========================================================================
# 5. Worker —— 单轮执行（独立进程，便于隔离崩溃与精确采样）
# ===========================================================================


def run_worker(args: argparse.Namespace) -> int:
    """跑一轮推理并把结果写成 JSON。"""
    from gen_corpus_real import normalize_for_cer  # noqa: E402  (路径已在顶部注入)

    corpus_dir = Path(args.corpus)
    wav = corpus_dir / args.file
    ref_path = corpus_dir / (Path(args.file).stem + ".json")
    reference = json.loads(ref_path.read_text(encoding="utf-8"))
    language = reference.get("language", "zh")
    ref_text = reference.get("full_text") or reference.get("full_text_nopunct") or ""

    t0 = time.perf_counter()
    text = ""
    error: Optional[str] = None
    extra: Dict[str, Any] = {}

    try:
        if args.engine == "faster_whisper":
            from faster_whisper import WhisperModel  # type: ignore

            compute_type = args.precision
            device = "cuda" if args.device == "cuda" else "cpu"
            compute_type = {
                "int8": "int8",
                "int8_float16": "int8_float16",
                "float16": "float16",
            }[compute_type]
            model = WhisperModel(
                args.model,
                device=device,
                compute_type=compute_type,
                local_files_only=True,  # 离线硬约束：只用本地缓存
            )
            segments, info = model.transcribe(
                str(wav),
                language=None if language == "mixed" else language,
                vad_filter=False,
                beam_size=5,
            )
            text = "".join(seg.text for seg in segments)
            extra["detected_language"] = getattr(info, "language", None)

        elif args.engine == "openai_whisper":
            import whisper  # type: ignore

            device = "cuda" if args.device == "cuda" else "cpu"
            model = whisper.load_model(args.model, device=device)
            # PyTorch 走自己的 caching allocator，可以给出**精确**的分配量 ——
            # 作为 Windows 下「整机显存差值」口径的交叉校验。
            torch_alloc_mb = None
            try:
                import torch  # type: ignore

                if device == "cuda":
                    torch.cuda.reset_peak_memory_stats()
                result = model.transcribe(
                    str(wav),
                    language=None if language == "mixed" else language,
                    fp16=(device == "cuda"),
                )
                if device == "cuda":
                    torch_alloc_mb = int(
                        torch.cuda.max_memory_allocated() / 1024 / 1024
                    )
            except Exception:
                raise
            text = result.get("text", "")
            extra["detected_language"] = result.get("language")
            extra["torch_peak_alloc_mb"] = torch_alloc_mb
        else:
            raise ValueError(f"未知引擎: {args.engine}")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    wall = time.perf_counter() - t0
    duration_s = float(reference.get("duration_s") or 0.0)

    payload: Dict[str, Any] = {
        "engine": args.engine,
        "model": args.model,
        "device": args.device,
        "precision": args.precision,
        "file": args.file,
        "language": language,
        "duration_s": duration_s,
        "wall_clock_s": round(wall, 3),
        "real_time_factor": round(wall / duration_s, 4) if duration_s else None,
        "error": error,
        "hypothesis": text.strip(),
        "extra": extra,
    }
    if not error:
        err_pct, ref_tokens, dist = error_rate(ref_text, text, language)
        payload["error_rate_pct"] = err_pct
        payload["reference_tokens"] = ref_tokens
        payload["edit_distance"] = dist
        payload["normalized_reference"] = normalize_for_cer(ref_text, language)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


# ===========================================================================
# 6. 主进程 —— 编排 + 采样
# ===========================================================================


def _sample_gpu_usage() -> Tuple[int, int, int]:
    """整机 GPU 显存占用（MiB）/ 温度 / 总量。

    **为什么查不到"按进程"的显存**：Windows 消费级 GPU 跑在 WDDM 模式下，
    ``nvidia-smi --query-compute-apps`` 会返回 ``[N/A]`` —— 这不是 bug，
    是该模式不暴露 per-process 显存统计。因此本基准采用**整机占用差值**：
    启动 worker 前记基线，运行中取 ``max(used) - baseline`` 作为该轮的
    显存占用。本场景下只有 worker 在用 GPU，差值口径成立。

    对走 PyTorch allocator 的引擎（openai-whisper），worker 另报
    ``torch.cuda.max_memory_allocated()`` 的精确值作为交叉校验。
    """
    if not shutil.which("nvidia-smi"):
        return (0, 0, 0)
    out = _nvidia_smi("memory.used,temperature.gpu,memory.total")
    if not out:
        return (0, 0, 0)
    try:
        used, temp, total = (int(float(x)) for x in out[0].split(","))
        return (used, temp, total)
    except Exception:
        return (0, 0, 0)


def _round_file(out_dir: Path, item: PlanItem, wav_name: str) -> Path:
    """一轮结果的落盘路径（主进程与 worker 共用同一命名规则）。"""
    return out_dir / "rounds" / (
        f"{item.engine}_{item.model}_{item.device}_{item.precision}_{Path(wav_name).stem}.json"
    )


def _eta(run_times: List[float], remaining: int) -> Optional[float]:
    """按已完成轮次的平均耗时估算剩余秒数。"""
    if not run_times or remaining <= 0:
        return None
    return sum(run_times) / len(run_times) * remaining


def _write_progress(
    out_dir: Path,
    *,
    done: int,
    total: int,
    current: str,
    eta_seconds: Optional[float],
    prof: HardwareProfile,
    guard: Guard,
) -> None:
    """把进度写成状态文件 —— 外部可读，便于长跑时观察与事后追溯。"""
    payload = {
        "schema": "mediascribe-benchmark-progress/v1",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "done": done,
        "total": total,
        "current": current,
        "eta_seconds": round(eta_seconds, 1) if eta_seconds is not None else None,
        "host": {
            "gpu": prof.gpu_name,
            "vram_total_mb": prof.gpu_vram_total_mb,
            "sys_ram_free_mb": guard.sys_ram_free_mb(),
            "cpu_load_pct": guard.cpu_load_pct(),
            "gpu_temp_c": guard.gpu_temp(),
        },
    }
    try:
        (out_dir / "progress.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def run_round(
    item: PlanItem,
    wav_name: str,
    corpus_dir: Path,
    guard: Guard,
    out_dir: Path,
    rtf_hist: Optional[Dict[str, List[float]]] = None,
    heartbeat_s: float = 5.0,
) -> Dict[str, Any]:
    """跑一轮并在过程中采样显存/内存/温度。

    跑中做三件事：

    1. **心跳**：长轮（可能几分钟）每 ``heartbeat_s`` 打印一次进度，
       让用户知道没卡死，同时暴露实时温度/显存/系统内存水位。
    2. **过载检测**：系统级水位越界 ⇒ 安全中止（温和终止优先）。
    3. **采样**：记录峰值显存 / 峰值内存 / 峰值温度。
    """
    res_file = _round_file(out_dir, item, wav_name)
    res_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--engine", item.engine,
        "--model", item.model,
        "--device", item.device,
        "--precision", item.precision,
        "--file", wav_name,
        "--corpus", str(corpus_dir),
        "--out", str(res_file),
    ]
    env = dict(os.environ)
    t0 = time.perf_counter()
    # 整机显存基线（worker 启动前的占用）—— Windows 下无法按进程查显存，
    # 只能用差值近似该轮的显存开销。
    gpu_base_used, _bt, _btot = _sample_gpu_usage()
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=env)
    peak_vram = 0
    peak_rss = 0
    peak_temp = 0
    peak_sys_ram_free = 10**9
    abort_reason = ""
    abort_action = ""
    last_beat = 0.0

    # 该组合的历史 RTF —— 用于心跳里估算剩余时间
    hist_key = item.key()
    hist = (rtf_hist or {}).get(hist_key) or []
    expected_rtf = sorted(hist)[len(hist) // 2] if hist else None

    # 语料时长（RTF 与 ETA 估算的基数）
    try:
        dur = float(
            json.loads(
                (corpus_dir / (Path(wav_name).stem + ".json")).read_text(encoding="utf-8")
            ).get("duration_s")
            or 0.0
        )
    except Exception:
        dur = 0.0

    while proc.poll() is None:
        time.sleep(0.25)
        elapsed = time.perf_counter() - t0
        if psutil is not None:
            try:
                p = psutil.Process(proc.pid)
                peak_rss = max(peak_rss, int(p.memory_info().rss / 1024 / 1024))
                for child in p.children(recursive=True):
                    try:
                        peak_rss = max(peak_rss, int(child.memory_info().rss / 1024 / 1024))
                    except Exception:
                        pass
            except Exception:
                pass
        used, temp, _total = _sample_gpu_usage()
        peak_vram = max(peak_vram, max(0, used - gpu_base_used))
        peak_temp = max(peak_temp, temp)
        sys_ram_free = guard.sys_ram_free_mb()
        peak_sys_ram_free = min(peak_sys_ram_free, sys_ram_free)

        # --- 心跳 ---
        if elapsed - last_beat >= heartbeat_s:
            last_beat = elapsed
            eta = ""
            if expected_rtf and dur:
                eta = f" 预计总耗时≈{dur * expected_rtf:.0f}s"
            rtf_now = (elapsed / dur) if dur else 0.0
            print(
                f"  ⏱ {elapsed:6.1f}s  RTF≈{rtf_now:.3f}"
                f"  GPU {temp}℃  显存 +{max(0, used - gpu_base_used)}MiB"
                f"  系统内存 {sys_ram_free}MiB  CPU {guard.cpu_load_pct():.0f}%{eta}",
                flush=True,
            )

        # --- 过载检测（GPU 温度硬阈值 + 系统级水位） ---
        snap = {
            "gpu_temp_c": temp,
            "gpu_used_mb": used,
            "gpu_total_mb": guard.gpu_state()[2],
            "rss_mb": peak_rss,
        }
        reason = guard.should_abort(snap, out_dir, elapsed_s=elapsed)
        if reason:
            abort_reason = reason
            abort_action = guard.safe_stop(proc)
            print(f"  [guard] 紧急停止（{abort_action}）：{reason}", flush=True)
            break
        if elapsed > guard.timeout_s:
            abort_reason = f"单轮超时（>{guard.timeout_s}s）"
            abort_action = guard.safe_stop(proc)
            print(f"  [guard] 紧急停止（{abort_action}）：{abort_reason}", flush=True)
            break
    try:
        proc.wait(timeout=30)
    except Exception:
        proc.kill()
    wall = time.perf_counter() - t0

    payload: Dict[str, Any] = {}
    if res_file.exists():
        try:
            payload = json.loads(res_file.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    if not payload:
        payload = {
            "engine": item.engine,
            "model": item.model,
            "device": item.device,
            "precision": item.precision,
            "file": wav_name,
            "error": abort_reason or f"worker 退出码 {proc.returncode}，无结果文件",
        }
    payload["sampled_peak_vram_mb"] = peak_vram
    payload["sampled_peak_rss_mb"] = peak_rss
    payload["sampled_peak_gpu_temp_c"] = peak_temp
    payload["sampled_min_sys_ram_free_mb"] = peak_sys_ram_free
    payload["driver_wall_clock_s"] = round(wall, 3)
    payload["abort_reason"] = abort_reason
    payload["abort_action"] = abort_action
    payload["note"] = item.skip_reason
    # 采样值必须**写回轮次文件** —— 否则断点续跑复用时，峰值显存/内存/温度
    # 这些主进程侧采集的指标会丢失（worker 自己写不了这些）。
    try:
        res_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    return payload


# ===========================================================================
# 7. 主流程
# ===========================================================================


def load_corpus(corpus_dir: Path) -> List[Dict[str, Any]]:
    manifest = corpus_dir / "corpus_real.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return list(data.get("files", []))
    # 兜底：扫 wav
    return [
        {"name": w.name, "language": "unknown", "duration_s": None}
        for w in sorted(corpus_dir.glob("*.wav"))
    ]


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="L3 ASR engine benchmark (adaptive + guarded)")
    p.add_argument("--corpus", type=Path, default=REPO_ROOT / "output" / "bench" / "corpus")
    p.add_argument("--out", type=Path, default=REPO_ROOT / "output" / "bench" / "asr")
    p.add_argument("--engines", default="faster_whisper,openai_whisper")
    p.add_argument("--models", default="small,medium,large-v3")
    p.add_argument("--devices", default="cuda,cpu")
    p.add_argument("--files", default="", help="逗号分隔的语料文件名；空=全部")
    p.add_argument("--enable-float16", action="store_true", help="显存≥12GB 时额外跑 float16 对比")
    p.add_argument("--max-gpu-temp-c", type=int, default=82, help="GPU 软阈值：超过则等待降温")
    p.add_argument("--hard-gpu-temp-c", type=int, default=92, help="GPU 硬阈值：超过则紧急停止")
    p.add_argument("--sys-ram-abort-mb", type=int, default=512, help="运行中系统可用内存紧急线，跌破即停止")
    p.add_argument("--sys-ram-start-mb", type=int, default=2048, help="跑前系统可用内存就绪线，不足则等待")
    p.add_argument("--wait-seconds", type=float, default=300.0, help="资源未就绪时的最长等待秒数")
    p.add_argument("--cpu-abort-pct", type=float, default=97.0, help="CPU 持续占用超过此值即紧急停止")
    p.add_argument("--disk-min-free-mb", type=int, default=2048, help="输出盘剩余低于此值即紧急停止")
    p.add_argument("--heartbeat-s", type=float, default=5.0, help="轮内心跳间隔（秒）")
    p.add_argument("--timeout-s", type=int, default=1800)
    p.add_argument("--cooldown-s", type=float, default=3.0)
    p.add_argument("--force", action="store_true", help="覆盖已有结果（默认续跑复用）")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--engine", default="", help=argparse.SUPPRESS)
    p.add_argument("--model", default="", help=argparse.SUPPRESS)
    p.add_argument("--device", default="cpu", help=argparse.SUPPRESS)
    p.add_argument("--precision", default="int8", help=argparse.SUPPRESS)
    p.add_argument("--file", default="", help=argparse.SUPPRESS)
    p.add_argument("--out-json", default="", help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if args.worker:
        return run_worker(args)

    corpus_dir = args.corpus
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rounds").mkdir(parents=True, exist_ok=True)

    prof = detect_hardware()
    guard = Guard(
        prof,
        max_gpu_temp_c=args.max_gpu_temp_c,
        hard_gpu_temp_c=args.hard_gpu_temp_c,
        sys_ram_abort_mb=args.sys_ram_abort_mb,
        sys_ram_start_mb=args.sys_ram_start_mb,
        cpu_abort_pct=args.cpu_abort_pct,
        disk_min_free_mb=args.disk_min_free_mb,
        timeout_s=args.timeout_s,
    )
    threads = guard.limit_threads()

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    devices = [d.strip() for d in args.devices.split(",") if d.strip()]

    plan = plan_matrix(
        prof, engines, models, devices, enable_float16=args.enable_float16
    )
    files = load_corpus(corpus_dir)
    if args.files:
        wanted = {f.strip() for f in args.files.split(",") if f.strip()}
        files = [f for f in files if f.get("name") in wanted]

    print("=== 硬件探测 ===")
    print(json.dumps(prof.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n[guard] 推理线程数限制 = {threads}")
    print(f"\n=== 计划（{len(plan)} 个组合 × {len(files)} 个语料）===")
    for it in plan:
        flag = "OK " if it.enabled else "SKIP"
        extra = f"  ← {it.skip_reason}" if it.skip_reason else ""
        print(f"  [{flag}] {it.engine:15} {it.model:9} {it.device:5} {it.precision:14}{extra}")

    if args.dry_run:
        print("\n[dry-run] 未执行")
        return 0

    results: List[Dict[str, Any]] = []
    rtf_hist: Dict[str, List[float]] = {}
    run_times: List[float] = []
    total = sum(1 for it in plan if it.enabled) * len(files)
    done = 0
    resumed = 0
    started = time.perf_counter()

    for it in plan:
        if not it.enabled:
            continue
        for fm in files:
            wav = fm.get("name")
            if not wav:
                continue
            done += 1
            label = f"[{done}/{total}] {it.engine}/{it.model}/{it.device}/{it.precision} {wav}"

            # --- 断点续跑：已有完整结果就跳过 ---
            res_file = _round_file(out_dir, it, wav)
            if not args.force and res_file.exists():
                try:
                    cached = json.loads(res_file.read_text(encoding="utf-8"))
                except Exception:
                    cached = {}
                if cached and not cached.get("abort_reason"):
                    results.append(cached)
                    resumed += 1
                    rt = cached.get("real_time_factor")
                    if rt:
                        rtf_hist.setdefault(it.key(), []).append(float(rt))
                    print(f"{label}\n  [skip] 已有结果，续跑跳过（RTF={rt}）", flush=True)
                    _write_progress(
                        out_dir, done=done, total=total, current=label,
                        eta_seconds=_eta(run_times, total - done), prof=prof, guard=guard,
                    )
                    continue

            print(f"\n--- {label} ---", flush=True)
            ok, why = guard.wait_for_capacity(it, out_dir, max_wait_s=args.wait_seconds)
            if not ok:
                print(f"  [guard] 熔断：{why}")
                results.append(
                    {
                        "engine": it.engine,
                        "model": it.model,
                        "device": it.device,
                        "precision": it.precision,
                        "file": wav,
                        "error": f"护栏熔断: {why}",
                    }
                )
                _write_progress(
                    out_dir, done=done, total=total, current=label,
                    eta_seconds=_eta(run_times, total - done), prof=prof, guard=guard,
                )
                continue

            payload = run_round(
                it, wav, corpus_dir, guard, out_dir,
                rtf_hist=rtf_hist, heartbeat_s=args.heartbeat_s,
            )
            elapsed = float(payload.get("driver_wall_clock_s") or 0.0)
            if elapsed:
                run_times.append(elapsed)
            rt = payload.get("real_time_factor")
            if rt and not payload.get("error"):
                rtf_hist.setdefault(it.key(), []).append(float(rt))
            err = payload.get("error")
            er = payload.get("error_rate_pct")
            print(
                f"  RTF={rt}  错误率={er}%  峰值显存={payload.get('sampled_peak_vram_mb')}MiB"
                f"  峰值内存={payload.get('sampled_peak_rss_mb')}MiB"
                f"  温度={payload.get('sampled_peak_gpu_temp_c')}℃"
                + (f"  ERROR: {err}" if err else "")
            )
            results.append(payload)
            _write_progress(
                out_dir, done=done, total=total, current=label,
                eta_seconds=_eta(run_times, total - done), prof=prof, guard=guard,
            )
            guard.cooldown(args.cooldown_s)

    wall_total = time.perf_counter() - started
    summary = {
        "schema": BENCH_SCHEMA,
        "host": prof.to_dict(),
        "guard": {
            "max_gpu_temp_c": guard.max_gpu_temp_c,
            "hard_gpu_temp_c": guard.hard_gpu_temp_c,
            "sys_ram_abort_mb": guard.sys_ram_abort_mb,
            "cpu_abort_pct": guard.cpu_abort_pct,
            "disk_min_free_mb": guard.disk_min_free_mb,
            "timeout_s": guard.timeout_s,
            "threads": threads,
            "events": guard.events,
        },
        "plan": [asdict(i) for i in plan],
        "runs": results,
        "meta": {
            "rounds_total": total,
            "rounds_resumed": resumed,
            "rounds_executed": len(results) - resumed,
            "wall_clock_s": round(wall_total, 2),
            "corpus": str(corpus_dir),
        },
    }
    out_json = out_dir / "results.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ok] {len(results)} 轮结果（续跑复用 {resumed} 轮）-> {out_json}")
    print(f"[ok] 总耗时 {wall_total / 60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
