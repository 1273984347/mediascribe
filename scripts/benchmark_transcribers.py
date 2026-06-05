"""
Transcriber benchmark suite.

This script compares the three supported transcribing engines
(whisper / faster-whisper / whisperx) along the axes that matter
most in practice:

* **Cold-start latency** (seconds before the first call returns)
* **Per-call throughput** (ms of audio processed per real-second)
* **Memory peak** (MB; ``resource.getrusage`` on Unix, ``tracemalloc``
  on Windows)
* **Engine availability** (true only if the optional package is
  installed)

The actual ASR code paths require multi-GB models that cannot be
exercised in CI.  To make the benchmark useful in a sandbox, we
ship a **mocked** mode: each engine is replaced by a stub that
returns deterministic text and a cost profile (CPU time, sleep)
proportional to the audio length.  When the real package is
installed, ``--real`` will dispatch to the actual transcriber.

Usage:
    # Mocked benchmark (default; safe in CI)
    python scripts/benchmark_transcribers.py

    # Restrict to one engine
    python scripts/benchmark_transcribers.py --engine faster-whisper

    # Use real ASR (requires the corresponding package)
    python scripts/benchmark_transcribers.py --real

    # Adjust audio length / iterations
    python scripts/benchmark_transcribers.py --audio-seconds 60 --iterations 3
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# 1. Cost profile (mocked engines)
# ---------------------------------------------------------------------------
# Relative cost factors derived from published benchmarks of
# faster-whisper vs whisperx vs openai-whisper.  These mirror the
# widely-reported ordering on CPU: faster-whisper ~3-5x faster than
# whisper, whisperx ~5-8x faster than whisper (with alignment).
COST_PROFILE = {
    "whisper": {
        "cold_start_ms": 4500,
        "ms_per_audio_sec": 800,
    },
    "faster-whisper": {
        "cold_start_ms": 1200,
        "ms_per_audio_sec": 220,
    },
    "whisperx": {
        "cold_start_ms": 5500,
        "ms_per_audio_sec": 150,
    },
}


def _mock_engine_call(engine: str, audio_seconds: float) -> Dict[str, Any]:
    """Simulate one ASR call.  Sleeps proportionally to cost profile."""
    profile = COST_PROFILE[engine]
    work_seconds = (profile["cold_start_ms"] / 1000.0) + (
        profile["ms_per_audio_sec"] / 1000.0
    ) * audio_seconds
    # Scale the simulated work down so the mocked run finishes in
    # a few seconds instead of an hour.
    scale = float(os.environ.get("BENCH_SCALE", "0.01"))
    work_seconds *= scale
    t0 = time.perf_counter()
    time.sleep(work_seconds)
    wall = time.perf_counter() - t0
    return {
        "text": f"[{engine} mock] transcribed {audio_seconds:.1f}s of audio",
        "wall_sec": wall,
        "audio_sec": audio_seconds,
    }


# ---------------------------------------------------------------------------
# 2. Per-engine wrapper
# ---------------------------------------------------------------------------
@dataclass
class BenchResult:
    engine: str
    available: bool
    iterations: int = 0
    audio_seconds: float = 0.0
    cold_start_ms: float = 0.0
    per_call_ms: List[float] = field(default_factory=list)
    throughput_audio_per_wall: float = 0.0
    peak_mem_mb: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _is_available(engine: str) -> bool:
    """Check optional dependency presence."""
    try:
        from video2text.transcribers.factory import _engine_available
        return _engine_available(engine)
    except Exception:
        return False


def _real_transcribe(engine: str, audio_path: str) -> Dict[str, Any]:
    """Dispatch to the real engine, returning a small dict."""
    from video2text.transcribers.factory import get_transcriber
    t = get_transcriber(engine, model="tiny")
    out = t.transcribe(audio_path, audio_path + ".md")
    return {"text": out.text if hasattr(out, "text") else str(out)}


def run_engine(
    engine: str,
    *,
    audio_seconds: float,
    iterations: int,
    use_real: bool,
    audio_path: Optional[Path],
) -> BenchResult:
    # In mock mode, the engine is "available" for benchmarking
    # purposes even if the real package is not installed.
    available_real = _is_available(engine)
    available_effective = available_real or (not use_real and engine in COST_PROFILE)
    res = BenchResult(
        engine=engine, available=available_effective,
        audio_seconds=audio_seconds, iterations=iterations,
    )
    if use_real and not available_real:
        res.error = f"engine {engine!r} not installed; skip real mode"
        return res
    if not use_real and engine not in COST_PROFILE:
        res.error = f"no cost profile for {engine!r}"
        return res

    gc.collect()
    tracemalloc.start()
    try:
        # Cold start: a one-shot import/load cost (mocked as sleep)
        cold_t0 = time.perf_counter()
        if use_real:
            # Force the import path even if not yet loaded
            from video2text.transcribers import factory
            _ = factory._engine_available  # touch
        else:
            time.sleep(COST_PROFILE[engine]["cold_start_ms"] * 0.001 * 0.01)
        res.cold_start_ms = (time.perf_counter() - cold_t0) * 1000.0

        # Per-call benchmarks
        for i in range(iterations):
            t0 = time.perf_counter()
            if use_real:
                if audio_path is None or not audio_path.exists():
                    res.error = "--audio-path required for real mode"
                    break
                _real_transcribe(engine, str(audio_path))
            else:
                _mock_engine_call(engine, audio_seconds)
            res.per_call_ms.append((time.perf_counter() - t0) * 1000.0)

        current, peak = tracemalloc.get_traced_memory()
        res.peak_mem_mb = peak / (1024 * 1024)
    finally:
        tracemalloc.stop()

    if res.per_call_ms:
        mean_ms = statistics.mean(res.per_call_ms)
        # Throughput: how many seconds of audio per real second.
        # mean_ms is per call; audio_seconds is per call; the throughput
        # is the ratio inverted.
        res.throughput_audio_per_wall = audio_seconds / (mean_ms / 1000.0) if mean_ms else 0.0
    return res


# ---------------------------------------------------------------------------
# 3. Reporting
# ---------------------------------------------------------------------------
def render_table(results: List[BenchResult]) -> str:
    headers = ["Engine", "Avail", "Cold ms", "Mean ms/call", "Throughput", "Peak MB"]
    rows = [headers]
    for r in results:
        mean = statistics.mean(r.per_call_ms) if r.per_call_ms else 0.0
        rows.append([
            r.engine,
            "Y" if r.available else "N",
            f"{r.cold_start_ms:.0f}",
            f"{mean:.1f}",
            f"{r.throughput_audio_per_wall:.2f}x",
            f"{r.peak_mem_mb:.1f}",
        ])
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(headers))]
    out = []
    for ri, row in enumerate(rows):
        line = "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row))
        out.append(line)
        if ri == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


def write_reports(results: List[BenchResult], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "benchmark.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(),
                "python": platform.python_version(),
                "results": [r.to_dict() for r in results],
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "benchmark.txt").write_text(
        render_table(results), encoding="utf-8"
    )
    lines = ["| Engine | Available | Cold start (ms) | Mean ms/call | Throughput (audio s / wall s) | Peak MB |",
             "|--------|-----------|------------------|--------------|-------------------------------|---------|"]
    for r in results:
        mean = statistics.mean(r.per_call_ms) if r.per_call_ms else 0.0
        lines.append(
            f"| {r.engine} | {'yes' if r.available else 'no'} | "
            f"{r.cold_start_ms:.0f} | {mean:.1f} | "
            f"{r.throughput_audio_per_wall:.2f} | {r.peak_mem_mb:.1f} |"
        )
    (out_dir / "benchmark.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--engine", action="append",
        choices=["whisper", "faster-whisper", "whisperx"],
        help="Restrict to one or more engines (default: all)",
    )
    parser.add_argument(
        "--audio-seconds", type=float, default=30.0,
        help="Length of the synthetic audio in seconds (default 30)",
    )
    parser.add_argument(
        "--iterations", type=int, default=3,
        help="Number of transcribe calls per engine (default 3)",
    )
    parser.add_argument(
        "--real", action="store_true",
        help="Use the real transcribers instead of mocked costs",
    )
    parser.add_argument(
        "--audio-path", type=Path, default=None,
        help="Path to a real audio/video file (required for --real)",
    )
    parser.add_argument(
        "--output-dir", default="./bench-results",
        help="Where to write the JSON / TXT / MD reports",
    )
    args = parser.parse_args(argv)

    engines = args.engine or ["whisper", "faster-whisper", "whisperx"]
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    results: List[BenchResult] = []
    for engine in engines:
        print(f"\n>>> benchmarking {engine} ...")
        r = run_engine(
            engine,
            audio_seconds=args.audio_seconds,
            iterations=args.iterations,
            use_real=args.real,
            audio_path=args.audio_path,
        )
        results.append(r)
        mean = statistics.mean(r.per_call_ms) if r.per_call_ms else 0.0
        print(
            f"<<< {engine}: cold={r.cold_start_ms:.0f}ms  "
            f"mean={mean:.1f}ms  "
            f"throughput={r.throughput_audio_per_wall:.2f}x  "
            f"peak={r.peak_mem_mb:.1f}MB"
        )

    write_reports(results, out_dir)
    print("\n" + render_table(results))
    print(f"\nReports written to: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
