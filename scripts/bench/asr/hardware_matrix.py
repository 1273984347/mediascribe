"""Build an evidence-labeled 16/8/4 GiB hardware validation report."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mediascribe.events.schema import HARDWARE_TIERS, _hardware_tier_from_mb  # noqa: E402

MATRIX_SCHEMA = "mediascribe-hardware-matrix/v1"
EVIDENCE_TYPES = frozenset({"real_device", "virtualized_device", "simulated_snapshot"})
TIER_ORDER = ("reference_16", "standard_8", "constrained_4", "unknown")


def percentile(values: Sequence[float], quantile: float) -> Optional[float]:
    numbers = sorted(float(value) for value in values)
    if not numbers:
        return None
    if len(numbers) == 1:
        return round(numbers[0], 3)
    position = (len(numbers) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(numbers[lower], 3)
    fraction = position - lower
    return round(numbers[lower] + (numbers[upper] - numbers[lower]) * fraction, 3)


def _round_key(run: Dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(run.get("engine") or "unknown"),
        str(run.get("model") or "unknown"),
        str(run.get("device") or "unknown"),
        str(run.get("precision") or "unknown"),
    )


def _error_kind(error: Any) -> str:
    text = str(error or "unknown")
    if text.startswith("护栏熔断"):
        return "RESOURCE_GUARD_BLOCKED"
    return text.split(":", 1)[0]


def _artifact_summary(checks: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(checks)
    valid = [
        row
        for row in rows
        if row.get("artifact_exists") is True
        and row.get("text_nonempty") is True
        and row.get("metadata_consistent") is True
    ]
    return {
        "rounds_total": len(rows),
        "valid_artifacts": len(valid),
        "artifact_integrity_rate": round(len(valid) / len(rows), 4) if rows else None,
    }


def build_report(
    host: Dict[str, Any],
    benchmark: Dict[str, Any],
    *,
    evidence_type: str,
    commit: str = "unknown",
    corpus_manifest: Optional[Dict[str, Any]] = None,
    artifact_checks: Optional[Iterable[Dict[str, Any]]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError(f"unsupported evidence_type: {evidence_type!r}")

    try:
        ram_total_mb = int(host.get("ram_total_mb") or 0)
    except (TypeError, ValueError):
        ram_total_mb = 0
    tier = _hardware_tier_from_mb(ram_total_mb)
    if tier not in HARDWARE_TIERS:
        raise ValueError(f"invalid hardware_tier: {tier!r}")

    runs = list(benchmark.get("runs") or [])
    grouped: Dict[tuple[str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[_round_key(run)].append(run)

    rows: List[Dict[str, Any]] = []
    for key, group in sorted(grouped.items()):
        successful = [
            run for run in group if run.get("error_rate_pct") is not None and not run.get("error")
        ]
        failed = [run for run in group if run not in successful]
        wall_times = [float(run["wall_clock_s"]) for run in successful if run.get("wall_clock_s")]
        error_rates = [
            float(run["error_rate_pct"])
            for run in successful
            if run.get("error_rate_pct") is not None
        ]
        error_kinds = Counter(_error_kind(run.get("error")) for run in failed)
        rows.append(
            {
                "engine": key[0],
                "model": key[1],
                "device": key[2],
                "precision": key[3],
                "rounds_total": len(group),
                "rounds_ok": len(successful),
                "rounds_failed": len(failed),
                "wall_clock_p50_s": percentile(wall_times, 0.50),
                "wall_clock_p90_s": percentile(wall_times, 0.90),
                "error_rate_p50_pct": percentile(error_rates, 0.50),
                "error_rate_p90_pct": percentile(error_rates, 0.90),
                "peak_vram_mb": max(
                    (int(run.get("sampled_peak_vram_mb") or 0) for run in successful),
                    default=0,
                ),
                "peak_rss_mb": max(
                    (int(run.get("sampled_peak_rss_mb") or 0) for run in successful),
                    default=0,
                ),
                "error_kinds": dict(error_kinds),
            }
        )

    has_success = any(
        run.get("error_rate_pct") is not None and not run.get("error") for run in runs
    )
    guard_blocked = any(str(run.get("error") or "").startswith("护栏熔断") for run in runs)
    if has_success:
        tier_status = "observed"
    elif guard_blocked:
        tier_status = "blocked"
    elif runs:
        tier_status = "failed"
    else:
        tier_status = "not_run"

    tiers: Dict[str, Any] = {
        name: {"status": "not_run", "evidence_type": None} for name in TIER_ORDER
    }
    tiers[tier] = {
        "status": tier_status,
        "evidence_type": evidence_type,
        "raw_ram_total_mb": ram_total_mb,
        "hardware_tier": tier,
    }

    timestamp = time.time() if now is None else float(now)
    report: Dict[str, Any] = {
        "schema": MATRIX_SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp)),
        "evidence_type": evidence_type,
        "commit": commit or "unknown",
        "hardware_tier": tier,
        "hardware": dict(host),
        "tiers": tiers,
        "corpus": dict(corpus_manifest or {}),
        "rows": rows,
        "limitations": [
            "Only the observed tier has device evidence; other tiers remain not_run.",
            "Simulated snapshots validate decisions, not real memory pressure or performance.",
        ],
    }
    if artifact_checks is not None:
        report["artifact_integrity"] = _artifact_summary(artifact_checks)
    return report


def _markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# MediaScribe Hardware Matrix",
        "",
        f"- Schema: `{report['schema']}`",
        f"- Evidence: `{report['evidence_type']}`",
        f"- Commit: `{report['commit']}`",
        f"- Observed tier: `{report['hardware_tier']}`",
        f"- Raw RAM: {report['tiers'][report['hardware_tier']].get('raw_ram_total_mb', 0)} MiB",
        "",
        "## Tier coverage",
        "",
        "| Tier | Status | Evidence |",
        "|---|---|---|",
    ]
    for tier in TIER_ORDER:
        entry = report["tiers"][tier]
        lines.append(f"| {tier} | {entry['status']} | {entry.get('evidence_type') or '—'} |")
    lines.extend(
        [
            "",
            "## Benchmark rounds",
            "",
            "| Engine | Model | Device | Precision | OK/Total | Wall P50 s | Wall P90 s | Error P50 % |",
            "|---|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["rows"]:
        lines.append(
            f"| {row['engine']} | {row['model']} | {row['device']} | {row['precision']} "
            f"| {row['rounds_ok']}/{row['rounds_total']} "
            f"| {row['wall_clock_p50_s'] if row['wall_clock_p50_s'] is not None else '—'} "
            f"| {row['wall_clock_p90_s'] if row['wall_clock_p90_s'] is not None else '—'} "
            f"| {row['error_rate_p50_pct'] if row['error_rate_p50_pct'] is not None else '—'} |"
        )
    if not report["rows"]:
        lines.append("| — | — | — | — | 0/0 | — | — | — |")
    if "artifact_integrity" in report:
        integrity = report["artifact_integrity"]
        lines.extend(
            [
                "",
                "## Artifact integrity",
                "",
                f"- Valid artifacts: {integrity['valid_artifacts']}/{integrity['rounds_total']}",
                f"- Integrity rate: {integrity['artifact_integrity_rate']}",
            ]
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def write_report(report: Dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "hardware-matrix.json"
    markdown_path = target / "hardware-matrix.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _load_json(path: Optional[Path], default: Dict[str, Any]) -> Dict[str, Any]:
    if path is None:
        return default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build an evidence-labeled hardware matrix report")
    parser.add_argument("--host-json", type=Path, default=None)
    parser.add_argument("--benchmark", type=Path, default=None)
    parser.add_argument("--corpus-manifest", type=Path, default=None)
    parser.add_argument("--artifact-checks", type=Path, default=None)
    parser.add_argument(
        "--evidence-type",
        choices=sorted(EVIDENCE_TYPES),
        default="real_device",
    )
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA") or "unknown")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "output" / "hardware-matrix")
    args = parser.parse_args(argv)

    if args.host_json is None:
        from bench_asr import detect_hardware

        host = detect_hardware().to_dict()
    else:
        host = _load_json(args.host_json, {})
    benchmark = _load_json(args.benchmark, {"runs": []})
    corpus = _load_json(args.corpus_manifest, {})
    artifact_checks = None
    if args.artifact_checks is not None:
        artifact_checks = json.loads(args.artifact_checks.read_text(encoding="utf-8"))

    report = build_report(
        host,
        benchmark,
        evidence_type=args.evidence_type,
        commit=args.commit,
        corpus_manifest=corpus,
        artifact_checks=artifact_checks,
    )
    json_path, markdown_path = write_report(report, args.out_dir)
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
