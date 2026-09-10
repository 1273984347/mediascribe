"""
Generate a self-contained **recording package** of the E2E suite.

This script runs ``scripts/run_real_e2e.py`` and
``scripts/run_wechat_mp_e2e.py``, captures every line of stdout
and stderr, copies the resulting JSON / TXT / MD reports, and
bundles them all into ``e2e-recording/<timestamp>/`` with a
top-level ``README.md`` that explains what is in each file.

The package is meant to be:

1. Produced on a developer machine with full network access.
2. Attached to a GitHub release / Pull Request so reviewers can
   audit the real-URL behaviour without running the suite
   themselves.
3. Diff-able across releases: the JSON report is
   deterministic-by-design (timestamps aside) so a
   ``git diff e2e-recording/`` between two tags shows
   regressions cleanly.

Usage::

    # Full run
    python scripts/capture_e2e_recording.py --output-dir ./e2e-recording

    # Single-platform capture
    python scripts/capture_e2e_recording.py --platform bilibili

    # Include only detection (no real downloads)
    python scripts/capture_e2e_recording.py --url-only
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"


def run_subprocess(args: List[str], log_path: Path, timeout: int = 1800) -> Tuple[int, str, str]:
    """Run a subprocess and tee its output to ``log_path`` + return (rc, stdout, stderr)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            args,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        rc, out, err = -1, exc.stdout or "", (exc.stderr or "") + f"\n[TIMEOUT after {timeout}s]"
    log_path.write_text(
        f"=== command ===\n{' '.join(args)}\n\n"
        f"=== exit code ===\n{rc}\n\n"
        f"=== stdout ===\n{out}\n\n=== stderr ===\n{err}\n",
        encoding="utf-8",
    )
    return rc, out, err


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--output-dir",
        default="./e2e-recording",
        help="Where to write the recording package",
    )
    parser.add_argument("--platform", action="append", help="Restrict to one or more platforms")
    parser.add_argument("--url-only", action="store_true", help="Capture detector-only mode")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_root = Path(args.output_dir).resolve() / ts
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"[capture] writing to {out_root}")

    summary: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform_filter": args.platform or [],
        "url_only": args.url_only,
        "runs": [],
    }

    # 1. Real E2E suite
    e2e_args = [
        sys.executable,
        str(SCRIPTS / "run_real_e2e.py"),
        "--output-dir",
        str(out_root / "real-e2e"),
    ]
    if args.url_only:
        e2e_args.append("--url-only")
    if args.platform:
        for p in args.platform:
            e2e_args.extend(["--platform", p])
    rc, out, err = run_subprocess(e2e_args, out_root / "real-e2e.log", timeout=args.timeout)
    summary["runs"].append(
        {
            "name": "run_real_e2e",
            "rc": rc,
            "ok_lines": sum(1 for line in out.splitlines() if line.startswith("[OK")),
            "fail_lines": sum(1 for line in out.splitlines() if line.startswith("[FAIL")),
            "skip_lines": sum(1 for line in out.splitlines() if line.startswith("[SKIP")),
        }
    )
    # Copy generated summary files (if any) into the package.
    for fname in ("summary.json", "summary.txt", "summary.md"):
        src = out_root / "real-e2e" / fname
        if src.exists():
            shutil.copy2(src, out_root / f"real-e2e.{fname}")

    # 2. WeChat MP E2E (only if not URL-only)
    if not args.url_only:
        wm_args = [
            sys.executable,
            str(SCRIPTS / "run_wechat_mp_e2e.py"),
            "--from-fixture",
            "--output-dir",
            str(out_root / "wechat-mp-e2e"),
            "--wechat-cookies",
            "wxuin=demo; pass_ticket=demo",
            "--dry-run",
        ]
        rc, out, err = run_subprocess(wm_args, out_root / "wechat-mp-e2e.log", timeout=args.timeout)
        summary["runs"].append(
            {
                "name": "run_wechat_mp_e2e",
                "rc": rc,
                "ok_lines": sum(1 for line in out.splitlines() if line.startswith("[OK")),
                "fail_lines": sum(1 for line in out.splitlines() if line.startswith("[FAIL")),
                "skip_lines": sum(1 for line in out.splitlines() if line.startswith("[SKIP")),
            }
        )
        for fname in ("summary.json", "summary.txt", "summary.md"):
            src = out_root / "wechat-mp-e2e" / fname
            if src.exists():
                shutil.copy2(src, out_root / f"wechat-mp-e2e.{fname}")

    # 3. Write the bundle-level summary
    (out_root / "SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 4. Write the bundle-level README
    readme = [
        f"# E2E Recording — {ts}",
        "",
        f"Generated at `{summary['generated_at']}` by",
        "`scripts/capture_e2e_recording.py`.",
        "",
        "## What is in this package",
        "",
        "| File | Description |",
        "|------|-------------|",
        "| `SUMMARY.json` | machine-readable summary of the run |",
        "| `real-e2e.log` | full stdout/stderr of `run_real_e2e.py` |",
        "| `real-e2e.summary.{json,txt,md}` | per-platform results |",
        "| `wechat-mp-e2e.log` | full stdout/stderr of `run_wechat_mp_e2e.py` |",
        "| `wechat-mp-e2e.summary.{json,txt,md}` | WeChat MP results |",
        "",
        "## How to reproduce",
        "",
        "```bash",
        'pip install "mediascribe[all]"',
        "python scripts/capture_e2e_recording.py --output-dir ./e2e-recording",
        "```",
        "",
        "## Counts",
        "",
    ]
    for run in summary["runs"]:
        readme.append(
            f"- `{run['name']}`: rc={run['rc']}  "
            f"ok={run['ok_lines']}  fail={run['fail_lines']}  "
            f"skip={run['skip_lines']}"
        )
    (out_root / "README.md").write_text("\n".join(readme), encoding="utf-8")

    print(f"\n[capture] package ready at: {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
