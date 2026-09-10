"""
v3.2.0b 复现 benchmark — whisperx vs faster-whisper 跨 engine / model 对比。

用法
----

::

    # 真 benchmark(需要 GPU + whisperx + faster-whisper)
    python scripts/benchmark_transcribers.py \\
        --corpus scripts/benchmark_corpus \\
        --engines faster_whisper,whisperx \\
        --models base,small,medium \\
        --device auto \\
        --output runs/bench-2026-06-06.json

    # CI 假数据(无 GPU / 无模型权重也跑得动)
    python scripts/benchmark_transcribers.py \\
        --engines fake \\
        --output runs/bench-smoke.json

输出
----
JSON,schema 固定::

    {
      "schema": "mediascribe-benchmark/v1",
      "host": { "device": "cuda", "device_name": "RTX 4090", ... },
      "engines": [ {name, model, device, wall_clock_s, real_time_factor,
                     wer_pct, peak_vram_mb, first_token_latency_ms} ],
      "files":   [ {name, duration_s, language} ]
    }

测试
----
``douyin_batch/tests/test_benchmark_transcribers.py`` 验证 schema 稳定
+ ``--engines fake`` 假数据路径。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCHEMA = "mediascribe-benchmark/v1"


# ---------------------------------------------------------------------------
# v3.1.0 兼容: 旧的 COST_PROFILE / BenchResult / render_table / write_reports
# 这些 API 由 ``douyin_batch/tests/test_benchmark_transcribers.py`` 锁定,
# v3.2.0b 不能移除;新写法的功能在 :func:`benchmark` / :class:`EngineResult`。
# ---------------------------------------------------------------------------
COST_PROFILE: Dict[str, Dict[str, float]] = {
    "whisper":       {"cold_start_ms": 12_000.0, "ms_per_audio_sec": 1_500.0},
    "faster-whisper": {"cold_start_ms":  1_500.0, "ms_per_audio_sec":   200.0},
    "whisperx":      {"cold_start_ms":  3_500.0, "ms_per_audio_sec":   260.0},
}


@dataclass
class BenchResult:
    """v3.1.0 老的 benchmark 行 — 仅给 :func:`render_table` / :func:`write_reports` 用。

    v3.2.0b 新流用 :class:`EngineResult` (按 engine × model × file 展开)。
    """

    engine: str
    available: bool = True
    cold_start_ms: float = 0.0
    per_call_ms: List[float] = field(default_factory=list)
    peak_mem_mb: float = 0.0


def render_table(results: Sequence["BenchResult"]) -> str:
    """把 :class:`BenchResult` 列表渲染成 ASCII 表格。"""
    lines = [
        "| Engine         | Available | Cold start (ms) | per-call (ms) | peak mem (MiB) |",
        "|----------------|-----------|-----------------|---------------|----------------|",
    ]
    for r in results:
        avail = "yes" if r.available else "no"
        per_call = ", ".join(f"{x:.1f}" for x in r.per_call_ms) or "-"
        lines.append(
            f"| {r.engine:<14} | {avail:<9} | {r.cold_start_ms:>15.1f} | {per_call:<13} | {r.peak_mem_mb:>14.2f} |"
        )
    return "\n".join(lines)


def write_reports(results: Sequence["BenchResult"], out_dir: Path) -> None:
    """把 :class:`BenchResult` 列表写成 json / txt / md 三件套。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "mediascribe-benchmark-results/v1",
        "results": [
            {
                "engine": r.engine,
                "available": r.available,
                "cold_start_ms": r.cold_start_ms,
                "per_call_ms": list(r.per_call_ms),
                "peak_mem_mb": r.peak_mem_mb,
            }
            for r in results
        ],
    }
    (out_dir / "benchmark.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "benchmark.txt").write_text(render_table(results), encoding="utf-8")
    (out_dir / "benchmark.md").write_text(
        render_table(results).replace("|", "\\|").replace("\n", "  \n"),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 数据类 (v3.2.0b 新)
# ---------------------------------------------------------------------------
@dataclass
class EngineResult:
    """单个 (engine, model) 组合的运行结果。"""

    engine: str
    model: str
    device: str
    wall_clock_s: float
    real_time_factor: float
    wer_pct: Optional[float]  # None = placeholder / no reference
    peak_vram_mb: Optional[int]
    first_token_latency_ms: Optional[float]
    file: str
    language: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HostInfo:
    """运行环境探针。"""

    python: str
    platform: str
    device: str
    device_name: Optional[str] = None
    vram_total_mb: Optional[int] = None
    cuda_available: bool = False
    mps_available: bool = False
    cpu_count: int = 1


# ---------------------------------------------------------------------------
# HostInfo 探针
# ---------------------------------------------------------------------------
def detect_host(device_hint: str) -> HostInfo:
    info = HostInfo(
        python=sys.version.split()[0],
        platform=platform.platform(),
        device=device_hint,
        cpu_count=os.cpu_count() or 1,
    )
    try:
        import torch  # type: ignore

        info.cuda_available = bool(torch.cuda.is_available())
        if hasattr(torch.backends, "mps"):
            info.mps_available = bool(torch.backends.mps.is_available())
        if info.cuda_available:
            info.device = "cuda"
            try:
                idx = torch.cuda.current_device()
                props = torch.cuda.get_device_properties(idx)
                info.device_name = props.name
                try:
                    _, total = torch.cuda.mem_get_info(idx)
                    info.vram_total_mb = int(total / (1024 * 1024))
                except Exception:
                    pass
            except Exception:
                pass
        elif info.mps_available:
            info.device = "metal"
    except Exception:
        pass
    return info


# ---------------------------------------------------------------------------
# Corpus 扫描
# ---------------------------------------------------------------------------
def scan_corpus(corpus_dir: Path) -> List[Dict[str, Any]]:
    """从 ``corpus.json`` 读 manifest;找不到就回退到 ``*.wav`` glob。"""
    manifest = corpus_dir / "corpus.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return list(data.get("files", []))
    # 兜底:扫 wav
    out = []
    for wav in sorted(corpus_dir.glob("*.wav")):
        out.append({"name": wav.name, "language": "unknown", "duration_s": None})
    return out


def load_reference(corpus_dir: Path, file_name: str) -> Optional[Dict[str, Any]]:
    """读 reference JSON;WER 计算由 caller 决定。"""
    stem = Path(file_name).stem
    candidates = [
        corpus_dir / f"{stem}.json",
        corpus_dir / f"{stem}_reference.json",
    ]
    for c in candidates:
        if c.exists():
            try:
                return json.loads(c.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


# ---------------------------------------------------------------------------
# WER 计算 (简化版 — 字符级 levenshtein,中文按 char,英文按 word)
# ---------------------------------------------------------------------------
def char_wer(reference: str, hypothesis: str) -> float:
    """字符级 WER (%)。

    真实 corpus 用 jiwer / 全字 WER,这里用 O(N*M) 动态规划
    Levenshtein 距离,够 CI smoke test 用。
    """
    if not reference:
        return 100.0 if hypothesis else 0.0
    ref = list(reference.strip())
    hyp = list(hypothesis.strip())
    n, m = len(ref), len(hyp)
    if m == 0:
        return 100.0
    # 1D DP
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            cur[j] = min(
                cur[j - 1] + 1,        # insert
                prev[j] + 1,           # delete
                prev[j - 1] + cost,    # substitute
            )
        prev = cur
    return round(100.0 * prev[m] / n, 2)


# ---------------------------------------------------------------------------
# 跑单个 (engine, model, file)
# ---------------------------------------------------------------------------
def run_one(
    engine: str,
    model: str,
    device: str,
    file_meta: Dict[str, Any],
    corpus_dir: Path,
) -> EngineResult:
    """跑一个组合,返回 :class:`EngineResult`。

    ``engine == "fake"`` 时返回稳定 canned 数据,CI 不需要真模型。
    """
    name = file_meta["name"]
    lang = file_meta.get("language", "unknown")
    duration_s = file_meta.get("duration_s") or 0.0

    if engine == "fake":
        # Canned 数据,稳定可测
        return EngineResult(
            engine=engine,
            model=model,
            device=device,
            wall_clock_s=0.42,
            real_time_factor=round(0.42 / max(duration_s, 1.0), 4),
            wer_pct=0.0,
            peak_vram_mb=None,
            first_token_latency_ms=12.0,
            file=name,
            language=lang,
        )

    # 真跑路径(留空 stub — v3.2.0b Tier 1 落实时填)
    t0 = time.perf_counter()
    try:
        # 真实实现 import / transcribe / 算 WER
        raise NotImplementedError(
            "real engine path is not yet implemented in v3.2.0b Tier 1; "
            "use --engines fake for now"
        )
    finally:
        wall = time.perf_counter() - t0

    return EngineResult(
        engine=engine,
        model=model,
        device=device,
        wall_clock_s=round(wall, 3),
        real_time_factor=round(wall / max(duration_s, 1.0), 4),
        wer_pct=None,
        peak_vram_mb=None,
        first_token_latency_ms=None,
        file=name,
        language=lang,
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def benchmark(
    corpus_dir: Path,
    engines: Sequence[str],
    models: Sequence[str],
    device: str,
) -> Dict[str, Any]:
    host = detect_host(device)
    files = scan_corpus(corpus_dir)
    if not files:
        print(f"[warn] no .wav in {corpus_dir}; results will be empty", file=sys.stderr)

    results: List[EngineResult] = []
    for eng in engines:
        for m in models:
            for fm in files:
                results.append(run_one(eng, m, host.device, fm, corpus_dir))
    return {
        "schema": SCHEMA,
        "host": asdict(host),
        "engines": [r.to_dict() for r in results],
        "files": [
            {"name": f["name"], "duration_s": f.get("duration_s"), "language": f.get("language")}
            for f in files
        ],
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument(
        "--corpus",
        type=Path,
        default=Path("scripts/benchmark_corpus"),
        help="Path to benchmark corpus directory (default: scripts/benchmark_corpus)",
    )
    p.add_argument(
        "--engines",
        default="fake",
        help="Comma-separated engine list (default: fake). Real: faster_whisper,whisperx",
    )
    p.add_argument(
        "--models",
        default="base",
        help="Comma-separated model list (default: base)",
    )
    p.add_argument(
        "--device",
        default="auto",
        help="Device hint: auto|cpu|cuda|metal|rocm (default: auto)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write JSON to this path; default = stdout",
    )
    args = p.parse_args(argv)

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    data = benchmark(args.corpus, engines, models, args.device)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"[ok] wrote {args.output} ({len(data['engines'])} runs)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
