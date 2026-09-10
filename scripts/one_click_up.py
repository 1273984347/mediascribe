"""
MediaScribe 一键启动 — Web UI + 浏览器扩展 host。

Cross-platform one-click launcher: starts the FastAPI Web UI in
the background, optionally opens the user's browser, prints the
browser-extension install instructions, and tails the server log
until the user hits Ctrl-C.

Usage::

    # Default port 8000, foreground, opens browser
    python scripts/one_click_up.py

    # Daemon mode (background)
    python scripts/one_click_up.py --daemon

    # Custom port
    python scripts/one_click_up.py --port 8080

    # No browser auto-open
    python scripts/one_click_up.py --no-browser

    # Status / stop
    python scripts/one_click_up.py --status
    python scripts/one_click_up.py --stop

    # Pick between local Python and Docker
    python scripts/one_click_up.py --mode docker

The script is the canonical entry point.  The ``.sh`` / ``.ps1``
wrappers in this folder just invoke it with the right Python
interpreter.  ``make up`` / ``just up`` do the same.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).parent.parent
PID_FILE = ROOT / ".one-click.pid"
LOG_FILE = ROOT / ".one-click.log"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _port_free(host: str, port: int) -> bool:
    """Return True if the (host, port) is free to bind."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _read_pid() -> Optional[int]:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _write_pid(pid: int) -> None:
    PID_FILE.write_text(str(pid), encoding="utf-8")


def _clear_pid() -> None:
    if PID_FILE.exists():
        PID_FILE.unlink()


def _process_alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}"], text=True,
            )
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _wait_for_health(url: str, timeout: float = 30.0) -> bool:
    """Poll ``/api/health`` until 200 or timeout."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
def check_prereqs(mode: str) -> List[str]:
    """Return a list of human-readable warnings / hard errors."""
    msgs: List[str] = []
    if mode == "local":
        if _which("ffmpeg") is None:
            msgs.append("WARN: ffmpeg not on PATH (audio extract will fail).")
        try:
            import fastapi  # noqa: F401
        except ImportError:
            msgs.append(
                "ERROR: fastapi not installed. Run: "
                "pip install \"mediascribe[web]\""
            )
    elif mode == "docker":
        if _which("docker") is None:
            msgs.append("ERROR: docker not installed or not on PATH.")
    return msgs


# ---------------------------------------------------------------------------
# Launchers
# ---------------------------------------------------------------------------
@dataclass
class LaunchResult:
    ok: bool
    mode: str
    pid: Optional[int] = None
    url: Optional[str] = None
    messages: List[str] = field(default_factory=list)


def launch_local(host: str, port: int, *, reload: bool) -> LaunchResult:
    """Start uvicorn in the foreground (or as a child process when daemon)."""
    msgs: List[str] = []
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        return LaunchResult(
            ok=False, mode="local",
            messages=["fastapi/uvicorn not installed; "
                      "run pip install \"mediascribe[web]\""],
        )
    cmd = [
        sys.executable, "-m", "uvicorn",
        "mediascribe.web.app:app",
        "--host", host, "--port", str(port),
    ]
    if reload:
        cmd.append("--reload")
    log_fp = open(LOG_FILE, "ab", buffering=0)
    log_fp.write(
        f"\n=== launched {datetime.now(timezone.utc).isoformat()} ===\n"
        f"command: {' '.join(cmd)}\n".encode("utf-8")
    )
    try:
        proc = subprocess.Popen(cmd, stdout=log_fp, stderr=subprocess.STDOUT,
                                cwd=str(ROOT))
    except FileNotFoundError as exc:
        return LaunchResult(ok=False, mode="local", messages=[str(exc)])
    _write_pid(proc.pid)
    url = f"http://{host}:{port}"
    msgs.append(f"started uvicorn pid={proc.pid} url={url}")
    return LaunchResult(ok=True, mode="local", pid=proc.pid, url=url,
                        messages=msgs)


def launch_docker(host: str, port: int) -> LaunchResult:
    """Start the Web UI via docker compose."""
    if _which("docker") is None:
        return LaunchResult(ok=False, mode="docker",
                            messages=["docker not installed"])
    compose_file = ROOT / "docker-compose.yml"
    if not compose_file.exists():
        return LaunchResult(
            ok=False, mode="docker",
            messages=[f"docker-compose.yml missing at {compose_file}"],
        )
    cmd = ["docker", "compose", "-f", str(compose_file),
           "up", "-d", "--build"]
    log_fp = open(LOG_FILE, "ab", buffering=0)
    rc = subprocess.call(cmd, stdout=log_fp, stderr=subprocess.STDOUT,
                         cwd=str(ROOT))
    if rc != 0:
        return LaunchResult(ok=False, mode="docker",
                            messages=[f"docker compose up failed (rc={rc})"])
    # The container is named ``mediascribe-web`` per docker-compose.yml
    # and exposes the same port.  PID file is a stand-in.
    compose_pid = subprocess.check_output(
        ["docker", "inspect", "-f", "{{.State.Pid}}", "mediascribe-web"],
        text=True,
    ).strip()
    _write_pid(int(compose_pid))
    url = f"http://{host}:{port}"
    return LaunchResult(ok=True, mode="docker", pid=int(compose_pid), url=url,
                        messages=[f"docker container up; pid={compose_pid}"])


# ---------------------------------------------------------------------------
# Extension install hints
# ---------------------------------------------------------------------------
def extension_hints(host: str, port: int) -> List[str]:
    """Human-readable instructions for loading the browser extension."""
    url = f"http://{host}:{port}"
    return [
        "Browser extension install (one-time):",
        "  1. Open chrome://extensions  (or edge://extensions)",
        "  2. Enable Developer mode (top right).",
        "  3. Click 'Load unpacked' and pick the extension/ folder.",
        f"  4. Open the extension's options page and set endpoint to {url}",
        "  5. Click the toolbar icon on a video page to send it to the API.",
    ]


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------
def cmd_up(args: argparse.Namespace) -> int:
    msgs = check_prereqs(args.mode)
    hard_errors = [m for m in msgs if m.startswith("ERROR")]
    if hard_errors:
        for m in hard_errors:
            print(m)
        return 1
    for m in msgs:
        if m.startswith("WARN"):
            print(m)

    if not _port_free(args.host, args.port):
        print(f"port {args.port} is already in use. Try --port <other>.")
        return 1

    if args.mode == "docker":
        result = launch_docker(args.host, args.port)
    else:
        result = launch_local(args.host, args.port, reload=args.reload)
    for m in result.messages:
        print(m)
    if not result.ok:
        return 1

    url = result.url or f"http://{args.host}:{args.port}"
    health = f"{url}/api/health"
    print(f"\n>>> waiting for {health} ...")
    if _wait_for_health(health, timeout=30):
        print(f">>> {health} is up.")
    else:
        print(f">>> {health} did not respond within 30s. See {LOG_FILE}.")
    print(f"\nWeb UI:  {url}")
    print(f"Health:  {health}")
    print(f"Logs:    {LOG_FILE}")
    print()
    for line in extension_hints(args.host, args.port):
        print(line)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception as exc:  # pragma: no cover - best-effort
            print(f"(could not open browser: {exc})")

    if args.daemon:
        print("\nDaemon mode: server is running in the background.")
        print("  Stop with: python scripts/one_click_up.py --stop")
        print("  Status:    python scripts/one_click_up.py --status")
        print(f"  Logs:      tail -f {LOG_FILE}")
        return 0

    # Foreground: wait for the child to exit (or Ctrl-C).
    pid = _read_pid()
    if pid is None:
        return 0
    try:
        while _process_alive(pid):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n>>> stopping...")
        try:
            if os.name == "nt":
                subprocess.call(["taskkill", "/F", "/PID", str(pid)])
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception as exc:
            print(f"stop failed: {exc}")
    finally:
        _clear_pid()
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    pid = _read_pid()
    if pid is None:
        print("not running.")
        return 0
    if not _process_alive(pid):
        print(f"pid {pid} is not alive; clearing pid file.")
        _clear_pid()
        return 0
    try:
        if os.name == "nt":
            subprocess.call(["taskkill", "/F", "/PID", str(pid)])
        else:
            os.kill(pid, signal.SIGTERM)
        print(f"stopped pid={pid}")
    except Exception as exc:
        print(f"failed to stop pid {pid}: {exc}")
        return 1
    _clear_pid()
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    pid = _read_pid()
    if pid is None:
        print("not running.")
        return 1
    alive = _process_alive(pid)
    print(f"pid: {pid}  alive: {alive}  log: {LOG_FILE}")
    return 0 if alive else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=False)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default="127.0.0.1")
    common.add_argument("--port", type=int, default=8000)
    common.add_argument("--mode", choices=["local", "docker"], default="local")

    p_up = sub.add_parser("up", parents=[common], help="Start the Web UI")
    p_up.add_argument("--daemon", action="store_true",
                      help="Run in the background and exit")
    p_up.add_argument("--reload", action="store_true",
                      help="Pass --reload to uvicorn (dev only)")
    p_up.add_argument("--no-browser", action="store_true",
                      help="Skip the auto-open browser step")
    p_up.set_defaults(func=cmd_up)

    p_stop = sub.add_parser("stop", help="Stop a daemonised server")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="Show whether the server is up")
    p_status.set_defaults(func=cmd_status)

    # If no subcommand, behave as ``up`` for ergonomics
    # (the common case is "just start it").
    args = parser.parse_args(argv)
    if args.cmd is None:
        # Re-parse with ``up`` as the default
        argv2 = ["up"] + (argv or [])
        args = parser.parse_args(argv2)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
