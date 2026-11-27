"""Measure offline fresh-venv installation cost for the ASR benchmark."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPORT_SCHEMA = "mediascribe-install-cost/v1"


def available_memory_mb() -> int:
    try:
        import psutil  # type: ignore[import-not-found]

        return int(psutil.virtual_memory().available / 1024 / 1024)
    except Exception:
        pass
    try:
        sysconf = getattr(os, "sysconf", None)
        if sysconf is not None:
            pages = int(sysconf("SC_AVPHYS_PAGES"))
            page_size = int(sysconf("SC_PAGE_SIZE"))
            if pages > 0 and page_size > 0:
                return int(pages * page_size / 1024 / 1024)
    except (ValueError, OSError, AttributeError):
        pass
    return 0


def resource_status(*, min_available_mb: int = 2048) -> Dict[str, Any]:
    available = available_memory_mb()
    if available < min_available_mb:
        return {
            "status": "blocked",
            "code": "RESOURCE_GUARD_BLOCKED",
            "available_mb": available,
            "min_available_mb": min_available_mb,
        }
    return {"status": "ready", "code": None, "available_mb": available}


def directory_size(path: Path) -> int:
    total = 0
    root = Path(path)
    if not root.exists():
        return 0
    for item in root.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def build_environment(run_dir: Path, wheelhouse: Path) -> Dict[str, str]:
    run_dir = Path(run_dir).resolve()
    home = run_dir / "home"
    cache = run_dir / "pip-cache"
    home.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PIP_CACHE_DIR": str(cache),
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_NO_INDEX": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "CUDA_VISIBLE_DEVICES": "-1",
        }
    )
    return env


def install_command(python: Path, wheel: Path, wheelhouse: Path) -> List[str]:
    version = Path(wheel).name.split("-")[1]
    return [
        str(python),
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        str(Path(wheelhouse).resolve()),
        "--only-binary=:all:",
        f"mediascribe[faster-whisper]=={version}",
    ]


def _python_path(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(command: List[str], *, env: Dict[str, str], cwd: Path, log_path: Path) -> float:
    started = time.perf_counter()
    proc = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
    elapsed = time.perf_counter() - started
    log_path.write_text(f"$ {' '.join(command)}\n\n{proc.stdout}\n{proc.stderr}", encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"command failed with exit {proc.returncode}: {log_path}")
    return elapsed


def write_report(report: Dict[str, Any], work_dir: Path) -> tuple[Path, Path]:
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    json_path = work_dir / "install-cost.json"
    markdown_path = work_dir / "install-cost.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# MediaScribe Offline Install Cost",
        "",
        f"- Status: `{report['status']}`",
        f"- Code: `{report.get('code') or '-'}`",
        f"- Network: `{report['network']}`",
        f"- Wheel bytes (logical): {report.get('wheel_bytes_logical', '—')}",
        f"- Wheelhouse bytes (logical): {report.get('wheelhouse_bytes_logical', '—')}",
        f"- Venv create seconds: {report.get('venv_create_s', '—')}",
        f"- Install seconds: {report.get('install_s', '—')}",
        f"- Venv bytes (logical): {report.get('venv_bytes_logical', '—')}",
        f"- Site-packages bytes (logical): {report.get('site_packages_bytes_logical', '—')}",
        "",
        "Install timing covers local wheelhouse resolution/unpacking only; it excludes network download and model preparation.",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Measure offline fresh-venv install cost")
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--min-available-mb", type=int, default=2048)
    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "running",
        "code": None,
        "network": "disabled",
        "resources": resource_status(min_available_mb=args.min_available_mb),
        "wheel_bytes_logical": Path(args.wheel).stat().st_size,
        "wheelhouse_bytes_logical": directory_size(Path(args.wheelhouse)),
    }
    try:
        if report["resources"]["status"] == "blocked":
            raise RuntimeError("RESOURCE_GUARD_BLOCKED")
        env = build_environment(work_dir, Path(args.wheelhouse))
        venv_dir = work_dir / "venv"
        report["venv_create_s"] = _run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            env=env,
            cwd=work_dir,
            log_path=work_dir / "venv.log",
        )
        python = _python_path(venv_dir)
        report["install_s"] = _run(
            install_command(python, Path(args.wheel), Path(args.wheelhouse)),
            env=env,
            cwd=work_dir,
            log_path=work_dir / "install.log",
        )
        report["venv_bytes_logical"] = directory_size(venv_dir)
        site_packages = venv_dir / ("Lib/site-packages" if os.name == "nt" else "lib")
        report["site_packages_bytes_logical"] = directory_size(site_packages)
        report["import_smoke_s"] = _run(
            [
                str(python),
                "-c",
                "import faster_whisper, ctranslate2; print(faster_whisper.__version__)",
            ],
            env=env,
            cwd=work_dir,
            log_path=work_dir / "import-smoke.log",
        )
        report["status"] = "passed"
        report["code"] = None
        json_path, markdown_path = write_report(report, work_dir)
        print(
            json.dumps({"status": "passed", "json": str(json_path), "markdown": str(markdown_path)})
        )
        return 0
    except RuntimeError as exc:
        blocked = str(exc) == "RESOURCE_GUARD_BLOCKED"
        report["status"] = "blocked" if blocked else "failed"
        report["code"] = "RESOURCE_GUARD_BLOCKED" if blocked else "INSTALL_FAILED"
        report["error"] = str(exc)
        json_path, markdown_path = write_report(report, work_dir)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "code": report["code"],
                    "error": report["error"],
                    "json": str(json_path),
                    "markdown": str(markdown_path),
                },
                ensure_ascii=False,
            )
        )
        return 2 if blocked else 1


if __name__ == "__main__":
    sys.exit(main())
