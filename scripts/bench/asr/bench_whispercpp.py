"""
C3 whisper.cpp 实测基准 —— 与 L3 其余引擎同口径。

为什么单独一个脚本
------------------
whisper.cpp 是**独立可执行文件**（不是 Python 库），调用方式、内存模型、
错误率口径都与 CTranslate2 / PyTorch 路径不同。若各写各的，口径必然漂移。
本脚本刻意复用 :mod:`bench_asr` 的错误率计算与语料 reference 格式，
使 C3 的数字能与 C1/C2 **逐行对比**。

实测数据来源
------------
* 二进制：``whisper.cpp`` 官方 GitHub release 的预编译包
  （如 ``b5130`` 的 ``whisper-bin-x64.zip``，8.2 MiB，x64 CPU 版）。
  ⚠️ 注意：**稳定 tag 的 release 资产为 0**，带资产的只有构建号 prerelease
  或较早的稳定版（v1.9.2 / v1.9.3）。
* 权重：GGML 格式。主 HF（``huggingface.co``）不可达时，
  ``hf-mirror.com`` **可达**，实测可完整取到 ``ggml-*.bin``。

跑法
----
::

    python scripts/bench/asr/bench_whispercpp.py \
        --exe output/bench/whisper_cpp/bin/Release/whisper-cli.exe \
        --models-dir output/bench/whisper_cpp \
        --models ggml-tiny.bin,ggml-base.bin \
        --out output/bench/asr_cpp
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "bench" / "asr"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "bench" / "corpus"))

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

from bench_asr import error_rate  # noqa: E402


def run_one(
    exe: Path,
    model: Path,
    wav: Path,
    corpus_dir: Path,
    threads: int = 8,
    extra_args: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """跑一轮 whisper.cpp 转写，采集 RTF / 峰值内存 / 错误率。"""
    stem = wav.stem
    out_prefix = wav.parent / f"_cpp_{stem}_{model.stem}"

    ref_path = corpus_dir / (stem + ".json")
    reference = json.loads(ref_path.read_text(encoding="utf-8"))
    language = reference.get("language", "zh")
    duration = float(reference.get("duration_s") or 0.0)
    ref_text = reference.get("full_text") or ""

    cmd = [
        str(exe),
        "-m",
        str(model),
        "-f",
        str(wav),
        "-l",
        "auto" if language == "mixed" else language,
        "-otxt",
        "-of",
        str(out_prefix),
        "-nt",
        "-t",
        str(threads),
    ]
    if extra_args:
        cmd += extra_args

    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    peak_rss = 0
    while proc.poll() is None:
        time.sleep(0.1)
        if psutil is not None:
            try:
                p = psutil.Process(proc.pid)
                peak_rss = max(peak_rss, int(p.memory_info().rss / 1024 / 1024))
            except Exception:
                pass
    stdout, stderr = proc.communicate()
    wall = time.perf_counter() - t0

    txt_path = Path(str(out_prefix) + ".txt")
    hypothesis = ""
    if txt_path.exists():
        hypothesis = txt_path.read_text(encoding="utf-8", errors="replace").strip()
        try:
            txt_path.unlink()
        except OSError:
            pass

    # whisper.cpp 自带的分项计时（比 CTranslate2 更细）
    timings: Dict[str, float] = {}
    for line in (stderr or "").splitlines():
        if "whisper_print_timings:" in line:
            key = line.split("whisper_print_timings:")[1].strip()
            if "=" in key:
                k, v = key.split("=", 1)
                nums = "".join(ch if (ch.isdigit() or ch in ". ") else " " for ch in v)
                parts = nums.split()
                if parts:
                    try:
                        timings[k.strip()] = float(parts[0])
                    except ValueError:
                        pass

    payload: Dict[str, Any] = {
        "engine": "whisper_cpp",
        "model": model.stem.replace("ggml-", "").replace(".bin", ""),
        "device": "cpu",
        "precision": "ggml",
        "file": wav.name,
        "language": language,
        "duration_s": duration,
        "wall_clock_s": round(wall, 3),
        "real_time_factor": round(wall / duration, 4) if duration else None,
        "sampled_peak_rss_mb": peak_rss,
        "timings_ms": timings,
        "error": None if proc.returncode == 0 else f"exit={proc.returncode}",
    }
    if hypothesis:
        err_pct, ref_tokens, dist = error_rate(ref_text, hypothesis, language)
        payload["error_rate_pct"] = err_pct
        payload["reference_tokens"] = ref_tokens
        payload["edit_distance"] = dist
        payload["hypothesis"] = hypothesis[:2000]
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="whisper.cpp benchmark (same corpus & CER as bench_asr)"
    )
    p.add_argument("--corpus", type=Path, default=REPO_ROOT / "output" / "bench" / "corpus")
    p.add_argument(
        "--exe",
        type=Path,
        default=REPO_ROOT
        / "output"
        / "bench"
        / "whisper_cpp"
        / "bin"
        / "Release"
        / "whisper-cli.exe",
    )
    p.add_argument(
        "--models-dir", type=Path, default=REPO_ROOT / "output" / "bench" / "whisper_cpp"
    )
    p.add_argument(
        "--models", default="ggml-tiny.bin", help="逗号分隔的模型文件名（相对 --models-dir）"
    )
    p.add_argument("--files", default="", help="逗号分隔的语料文件名；空=全部")
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--out", type=Path, default=REPO_ROOT / "output" / "bench" / "asr_cpp")
    args = p.parse_args(argv)

    if not args.exe.exists():
        print(f"[!] 找不到 whisper-cli: {args.exe}", file=sys.stderr)
        return 1

    manifest = json.loads((args.corpus / "corpus_real.json").read_text(encoding="utf-8"))
    files = list(manifest.get("files", []))
    if args.files:
        wanted = {f.strip() for f in args.files.split(",") if f.strip()}
        files = [f for f in files if f.get("name") in wanted]

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    args.out.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    for m in models:
        mp = args.models_dir / m
        if not mp.exists():
            print(f"[skip] 模型不存在: {mp}")
            continue
        for fm in files:
            wav = args.corpus / fm["name"]
            print(f"--- whisper_cpp/{m} {fm['name']} ---", flush=True)
            payload = run_one(args.exe, mp, wav, args.corpus, threads=args.threads)
            print(
                f"  RTF={payload.get('real_time_factor')}  错误率={payload.get('error_rate_pct')}%  "
                f"峰值内存={payload.get('sampled_peak_rss_mb')}MiB",
                flush=True,
            )
            results.append(payload)

    out_json = args.out / "results.json"
    out_json.write_text(
        json.dumps(
            {
                "schema": "mediascribe-benchmark/v2-asr-cpp",
                "engine": "whisper_cpp",
                "exe": str(args.exe),
                "models": models,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[ok] {len(results)} 轮 -> {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
