"""
L3 实测结果汇总 —— 把 ``results.json`` 压成可读的表格。

**为什么需要独立汇总脚本**：原始 ``results.json`` 里，失败轮次同样带有
``wall_clock_s`` / ``real_time_factor``（那是"加载失败所花的时间"，不是推理速度）。
若不区分，聚合表会把失败轮的 RTF 当成有效速度混进去 —— 实测中曾让
"faster-whisper medium RTF=0.053" 这类**完全失败的组合看起来最快**。

因此本脚本把轮次严格分为三类，并**只在成功轮上做统计**：

* ``ok``      —— 推理完成且算出错误率
* ``skipped`` —— 护栏/环境原因未能完成（模型未缓存、显存不足、被安全中止）
* ``failed``  —— 其它异常
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]


def classify(run: Dict[str, Any]) -> str:
    """把一轮归类为 ok / skipped / failed。"""
    if run.get("error_rate_pct") is not None and not run.get("error"):
        return "ok"
    err = str(run.get("error") or "")
    if "abort_reason" in run and run.get("abort_reason"):
        return "skipped"
    if "LocalEntryNotFoundError" in err or "cached snapshot" in err:
        return "skipped"
    if "护栏熔断" in err or "紧急停止" in err or "超时" in err:
        return "skipped"
    if "CUDA out of memory" in err or "OutOfMemory" in err:
        return "skipped"
    if err:
        return "failed"
    return "failed"


def median(values: List[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 4) if vals else None


def summarize(results_path: Path) -> Dict[str, Any]:
    data = json.loads(results_path.read_text(encoding="utf-8"))
    runs = data.get("runs", [])
    buckets: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in runs:
        key = (
            r.get("engine", "?"),
            r.get("model", "?"),
            r.get("device", "?"),
            r.get("precision", r.get("note") and "" or ""),
        )
        buckets.setdefault(key, []).append(r)

    rows = []
    for key, rs in sorted(buckets.items()):
        ok = [r for r in rs if classify(r) == "ok"]
        other = [r for r in rs if classify(r) != "ok"]
        reasons = Counter(
            str(r.get("abort_reason") or r.get("error") or "")[:90] for r in other
        )
        rows.append(
            {
                "engine": key[0],
                "model": key[1],
                "device": key[2],
                "precision": key[3],
                "n_total": len(rs),
                "n_ok": len(ok),
                "rtf_median": median([r.get("real_time_factor") for r in ok]),
                "err_median": median([r.get("error_rate_pct") for r in ok]),
                "err_min": min([r["error_rate_pct"] for r in ok], default=None),
                "err_max": max([r["error_rate_pct"] for r in ok], default=None),
                "peak_vram_mb": max([r.get("sampled_peak_vram_mb") or 0 for r in ok], default=0),
                "peak_rss_mb": max([r.get("sampled_peak_rss_mb") or 0 for r in ok], default=0),
                "skipped_reasons": dict(reasons),
            }
        )

    return {
        "schema": "mediascribe-benchmark-summary/v1",
        "host": data.get("host", {}),
        "meta": data.get("meta", {}),
        "rows": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Summarise L3 ASR benchmark results")
    p.add_argument(
        "--results",
        type=Path,
        default=REPO_ROOT / "output" / "bench" / "asr" / "results.json",
    )
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--markdown", action="store_true", help="打印 Markdown 表格")
    args = p.parse_args(argv)

    if not args.results.exists():
        print(f"[!] 结果文件不存在: {args.results}", file=sys.stderr)
        return 1

    summary = summarize(args.results)
    rows = summary["rows"]

    if args.markdown:
        print(
            "| 引擎 | 模型 | 设备 | 精度 | 完成/总 | RTF 中位 | 错误率% (中位) | 错误率区间 | 峰值显存 MiB | 峰值内存 MiB |"
        )
        print("|---|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            span = (
                f"{r['err_min']:.2f}–{r['err_max']:.2f}"
                if r["err_min"] is not None
                else "—"
            )
            print(
                f"| {r['engine']} | {r['model']} | {r['device']} | {r['precision'] or '—'} "
                f"| {r['n_ok']}/{r['n_total']} | {r['rtf_median'] if r['rtf_median'] is not None else '—'} "
                f"| {r['err_median'] if r['err_median'] is not None else '—'} | {span} "
                f"| {r['peak_vram_mb'] or '—'} | {r['peak_rss_mb'] or '—'} |"
            )
    else:
        for r in rows:
            print(
                f"{r['engine']:15} {r['model']:9} {r['device']:5} {r['precision'] or '-':13} "
                f"ok={r['n_ok']}/{r['n_total']}  RTF={r['rtf_median']}  err={r['err_median']}%  "
                f"vram={r['peak_vram_mb']}MiB  ram={r['peak_rss_mb']}MiB"
            )
            for reason, cnt in r["skipped_reasons"].items():
                print(f"    未完成 x{cnt}: {reason}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[ok] 汇总 -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
