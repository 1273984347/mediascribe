#!/usr/bin/env bash
# Video2Text 一键启动 — POSIX shell 包装。
#
# This is a thin wrapper around the cross-platform Python launcher.
# It exists so that users on Linux/macOS (or Git Bash on Windows) can
# type a familiar ``./scripts/one_click_up.sh up`` instead of having to
# remember the Python module path.
#
# Usage::
#
#     ./scripts/one_click_up.sh           # start the Web UI (default = up)
#     ./scripts/one_click_up.sh up        # explicit up
#     ./scripts/one_click_up.sh stop      # stop a daemonised server
#     ./scripts/one_click_up.sh status    # show status
#     ./scripts/one_click_up.sh up --port 8080 --daemon
#
# Anything you would pass to ``one_click_up.py`` is forwarded verbatim.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Pick a Python. Prefer python3 (POSIX), fall back to python.
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "error: neither python3 nor python is on PATH" >&2
    exit 127
fi

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/one_click_up.py" "$@"
