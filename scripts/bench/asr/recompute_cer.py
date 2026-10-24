"""
按当前口径**重算**已落盘轮次的错误率（不重跑推理）。

为什么需要
----------
跨引擎中文对比的度量口径在实测中被修正过一次：whisper.cpp 与
openai-whisper 的中文输出是**繁体**，faster-whisper 输出**简体**。
不做繁简归一化的字级 CER 会把"字形差异"误判成"识别错误"：

* whisper.cpp base：81.56% →（归一化后）1.19%
* openai-whisper small CUDA：18.36% →（归一化后）?

轮次结果文件里保存了 ``hypothesis``，因此**只需重算 CER**，
不必重跑推理 —— 这让口径修正的成本从"几十分钟"降到"几秒"。

用法
----
::

    python scripts/bench/asr/recompute_cer.py --corpus output/bench/corpus \\
        --rounds output/bench/asr/rounds \\
        --also output/bench/asr_cpp/results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "bench" / "asr"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "benchmark_corpus"))

from bench_asr import error_rate  # noqa: E402


def _recompute_one(
    payload: Dict[str, Any], corpus_dir: Path, dry_run: bool
) -> Optional[Dict[str, Any]]:
    hyp = payload.get("hypothesis")
    fname = payload.get("file")
    if not hyp or not fname:
        return None
    ref_path = corpus_dir / (Path(fname).stem + ".json")
    if not ref_path.exists():
        return None
    reference = json.loads(ref_path.read_text(encoding="utf-8"))
    language = reference.get("language", "zh")
    ref_text = reference.get("full_text") or ""
    err_pct, ref_tokens, dist = error_rate(ref_text, hyp, language)
    old = payload.get("error_rate_pct")
    payload["error_rate_pct"] = err_pct
    payload["reference_tokens"] = ref_tokens
    payload["edit_distance"] = dist
    payload["error_rate_pct_before_normalization"] = old
    return {"old": old, "new": err_pct}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Recompute CER with current normalisation")
    p.add_argument("--corpus", type=Path, default=REPO_ROOT / "output" / "bench" / "corpus")
    p.add_argument("--rounds", type=Path, default=REPO_ROOT / "output" / "bench" / "asr" / "rounds")
    p.add_argument(
        "--also",
        type=Path,
        action="append",
        default=[],
        help="额外的 results.json（含 results 数组）",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    changed = 0
    total = 0
    deltas: List[str] = []

    if args.rounds.exists():
        for f in sorted(args.rounds.glob("*.json")):
            try:
                payload = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            total += 1
            delta = _recompute_one(payload, args.corpus, args.dry_run)
            if delta and delta["old"] != delta["new"]:
                changed += 1
                deltas.append(f"{f.name}: {delta['old']}% -> {delta['new']}%")
            if not args.dry_run:
                f.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for extra in args.also:
        if not extra.exists():
            continue
        try:
            data = json.loads(extra.read_text(encoding="utf-8"))
        except Exception:
            continue
        for payload in data.get("results", []):
            total += 1
            delta = _recompute_one(payload, args.corpus, args.dry_run)
            if delta and delta["old"] != delta["new"]:
                changed += 1
                deltas.append(
                    f"{extra.name}/{payload.get('engine')}/{payload.get('model')}/"
                    f"{payload.get('file')}: {delta['old']}% -> {delta['new']}%"
                )
        if not args.dry_run:
            extra.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] 处理 {total} 轮，{changed} 轮错误率被修正")
    for d in deltas[:30]:
        print("   ", d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
