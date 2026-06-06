"""
Profile CLI — render a Markdown or JSON report from JSONL timings.

Usage::

    python -m video2text.profile path/to/timings.jsonl
    python -m video2text.profile path/to/timings.jsonl --top 5
    python -m video2text.profile path/to/timings.jsonl --by-stage download
    python -m video2text.profile path/to/timings.jsonl --since 2026-06-01
    python -m video2text.profile path/to/timings.jsonl --json

JSONL format (one line per profiled call)::

    {"ts": "2026-06-06T10:00:00+00:00", "label": "download", "duration_sec": 1.42}

The CLI aggregates by label and reports count, total, mean, p50, p95,
and max.  Filtering options:

* ``--top N``        keep only the N slowest labels
* ``--by-stage S``   keep only records whose label contains "S"
* ``--since YYYY-MM-DD`` drop records older than the date (uses ``ts``)
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class AggregatedStage:
    label: str
    count: int
    total_sec: float
    mean_sec: float
    p50_sec: float
    p95_sec: float
    max_sec: float
    min_sec: float


def read_jsonl(path: Path) -> List[dict]:
    """Load every line of ``path`` as a JSON object.  Blank lines skipped."""
    if not path.exists():
        raise FileNotFoundError(f"profile: file not found: {path}")
    out: List[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # Skip corrupt lines rather than crash
            continue
    return out


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def filter_records(
    records: List[dict],
    *,
    by_stage: Optional[str] = None,
    since: Optional[str] = None,
) -> List[dict]:
    """Apply CLI filters to a list of records."""
    out: List[dict] = list(records)
    if by_stage:
        out = [r for r in out if by_stage in r.get("label", "")]
    if since:
        try:
            cutoff = datetime.fromisoformat(since)
        except ValueError:
            raise ValueError(f"profile: --since expects YYYY-MM-DD, got {since!r}")
        kept = []
        for r in out:
            ts = _parse_iso(r.get("ts", ""))
            if ts is None:
                kept.append(r)
                continue
            # Normalise cutoff to naive UTC if records are tz-aware
            if ts.tzinfo is not None and cutoff.tzinfo is None:
                from datetime import timezone
                cutoff = cutoff.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                kept.append(r)
        out = kept
    return out


def aggregate(records: List[dict]) -> List[AggregatedStage]:
    """Group records by label and produce per-stage aggregates."""
    grouped: dict[str, list[float]] = {}
    for r in records:
        label = r.get("label", "unknown")
        dur = r.get("duration_sec")
        if not isinstance(dur, (int, float)):
            continue
        grouped.setdefault(label, []).append(float(dur))
    out: List[AggregatedStage] = []
    for label, durations in grouped.items():
        if not durations:
            continue
        sorted_d = sorted(durations)
        n = len(sorted_d)
        # p50 / p95 via simple percentile
        def pct(p: float) -> float:
            if n == 1:
                return sorted_d[0]
            k = int(round(p / 100.0 * (n - 1)))
            return sorted_d[k]
        try:
            p50 = statistics.median(sorted_d)
        except statistics.StatisticsError:
            p50 = sorted_d[0]
        out.append(AggregatedStage(
            label=label,
            count=n,
            total_sec=sum(sorted_d),
            mean_sec=sum(sorted_d) / n,
            p50_sec=p50,
            p95_sec=pct(95),
            max_sec=max(sorted_d),
            min_sec=min(sorted_d),
        ))
    out.sort(key=lambda s: s.total_sec, reverse=True)
    return out


def render_markdown(stages: List[AggregatedStage], *, top: Optional[int] = None) -> str:
    """Format stages as a Markdown table."""
    if top is not None and top > 0:
        stages = stages[:top]
    if not stages:
        return "*(no profile data)*\n"
    lines = [
        "# Profile Report",
        "",
        "| Stage | Count | Total (s) | Mean (s) | p50 (s) | p95 (s) | Max (s) | Min (s) |",
        "|-------|------:|----------:|---------:|--------:|--------:|--------:|--------:|",
    ]
    total = sum(s.total_sec for s in stages)
    for s in stages:
        lines.append(
            f"| `{s.label}` | {s.count} | "
            f"{s.total_sec:.3f} | {s.mean_sec:.3f} | "
            f"{s.p50_sec:.3f} | {s.p95_sec:.3f} | "
            f"{s.max_sec:.3f} | {s.min_sec:.3f} |"
        )
    lines.append("")
    lines.append(f"**Total wall time**: {total:.3f} s")
    lines.append("")
    return "\n".join(lines)


def render_json(stages: List[AggregatedStage], *, top: Optional[int] = None) -> str:
    if top is not None and top > 0:
        stages = stages[:top]
    return json.dumps(
        {
            "stages": [asdict(s) for s in stages],
            "total_sec": sum(s.total_sec for s in stages),
        },
        ensure_ascii=False,
        indent=2,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="video2text.profile",
        description="Render a profile report from JSONL timings.",
    )
    parser.add_argument("path", type=Path, help="Path to JSONL timings file")
    parser.add_argument("--top", type=int, default=None, help="Keep top N slowest stages")
    parser.add_argument("--by-stage", type=str, default=None, help="Filter by label substring")
    parser.add_argument(
        "--since", type=str, default=None,
        help="Drop records before YYYY-MM-DD",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of Markdown",
    )
    args = parser.parse_args(argv)

    records = read_jsonl(args.path)
    records = filter_records(records, by_stage=args.by_stage, since=args.since)
    stages = aggregate(records)
    if args.json:
        print(render_json(stages, top=args.top))
    else:
        print(render_markdown(stages, top=args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
