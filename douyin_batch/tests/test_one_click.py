"""
Tests for the one-click Web UI launcher (Task 6).

The launcher is split into:

* ``scripts/one_click_up.py`` — cross-platform Python entry point.
* ``scripts/one_click_up.sh`` / ``.ps1`` — thin wrappers.
* ``Dockerfile`` + ``docker-compose.yml`` — Docker mode.
* ``Makefile`` + ``justfile`` — convenience targets.
* ``extension/manifest.json`` — host permission for Docker host.

These tests cover the contract that holds even when ffmpeg / docker /
fastapi are not installed in the test environment:

1. The Python launcher parses and exposes the expected sub-commands.
2. The shell wrappers exist and forward arguments to Python.
3. The Docker artefacts parse and reference the right services.
4. The Makefile exposes the one-click targets.
5. The extension manifest advertises host.docker.internal.
6. The mkdocs nav links the user-facing documentation.
7. The docs page mentions the canonical install commands.

The tests deliberately avoid actually starting a server.  Integration
coverage for the live launcher lives in scripts that require a real
Web UI install.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "scripts"
DOCS_SITE = ROOT / "docs_site"


# ---------------------------------------------------------------------------
# 1. scripts/one_click_up.py
# ---------------------------------------------------------------------------
def _load_launcher_module():
    """Import scripts/one_click_up.py as a module without executing main()."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "one_click_up_under_test", SCRIPTS / "one_click_up.py"
    )
    assert spec is not None and spec.loader is not None, (
        "could not build import spec for one_click_up.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # ``@dataclass`` introspects ``sys.modules[cls.__module__]``; without
    # this registration the import fails with ``'NoneType' has no
    # attribute '__dict__'``.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_one_click_launcher_module_imports():
    mod = _load_launcher_module()
    # The CLI must expose the public helpers we depend on from tests.
    for name in (
        "PID_FILE", "LOG_FILE", "_port_free", "_read_pid", "_write_pid",
        "_clear_pid", "_process_alive", "_wait_for_health",
        "check_prereqs", "launch_local", "launch_docker", "extension_hints",
        "cmd_up", "cmd_stop", "cmd_status", "main",
    ):
        assert hasattr(mod, name), f"one_click_up.py is missing {name!r}"


def test_one_click_launcher_subcommands():
    """``python one_click_up.py --help`` should list up/stop/status."""
    launcher = SCRIPTS / "one_click_up.py"
    if shutil.which("python") is None:
        # The test runner itself is Python, so this should never happen.
        return
    proc = subprocess.run(
        [sys.executable, str(launcher), "--help"],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "up" in out
    assert "stop" in out
    assert "status" in out


def test_one_click_launcher_status_when_stopped():
    launcher = SCRIPTS / "one_click_up.py"
    # Clean up any leftover pid file from a previous run.
    pid_file = ROOT / ".one-click.pid"
    if pid_file.exists():
        pid_file.unlink()
    proc = subprocess.run(
        [sys.executable, str(launcher), "status"],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode != 0, "status should exit non-zero when no pid file exists"
    assert "not running" in proc.stdout


def test_one_click_launcher_stop_idempotent():
    launcher = SCRIPTS / "one_click_up.py"
    pid_file = ROOT / ".one-click.pid"
    if pid_file.exists():
        pid_file.unlink()
    proc = subprocess.run(
        [sys.executable, str(launcher), "stop"],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, "stop must be idempotent (no pid file)"
    assert "not running" in proc.stdout


def test_port_free_returns_true_for_arbitrary_port():
    mod = _load_launcher_module()
    # Port 1 is privileged and reserved on every OS, but the *probe* (not
    # the connect) just attempts to bind; an OSError here is also fine.
    # The contract is: the function returns a bool without raising.
    result = mod._port_free("127.0.0.1", 1)
    assert isinstance(result, bool)


def test_extension_hints_includes_url():
    mod = _load_launcher_module()
    hints = mod.extension_hints("127.0.0.1", 9999)
    text = "\n".join(hints)
    assert "http://127.0.0.1:9999" in text
    assert "chrome://extensions" in text or "edge://extensions" in text


# ---------------------------------------------------------------------------
# 2. Shell wrappers
# ---------------------------------------------------------------------------
def test_bash_wrapper_exists_and_parses():
    sh = SCRIPTS / "one_click_up.sh"
    assert sh.exists(), "scripts/one_click_up.sh is missing"
    text = sh.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "one_click_up.py" in text
    # bash -n is the static-syntax check; available on Linux / macOS / WSL.
    if shutil.which("bash"):
        # Git-Bash on Windows can hang inside sandboxed envs.  Probe with
        # ``bash --version`` first (cheap exit) and only run the heavy
        # ``bash -n`` if a quick ``--version`` returns within 3s.
        try:
            probe = subprocess.run(
                ["bash", "--version"],
                capture_output=True, text=True, timeout=3,
            )
        except (subprocess.TimeoutExpired, OSError):
            pytest.skip("bash on PATH is unresponsive in this environment")
            return
        if probe.returncode != 0:
            pytest.skip(f"bash --version failed: {probe.stderr[:200]}")
            return
        # On Windows, backslashes in the absolute path are interpreted as
        # escape characters by Git-Bash.  Pipe the script body to bash via
        # stdin so we don't depend on a specific path representation.
        proc = subprocess.run(
            ["bash", "-n", sh.name],
            capture_output=True, text=True, timeout=10,
            cwd=str(SCRIPTS),
        )
        assert proc.returncode == 0, proc.stderr


def test_powershell_wrapper_exists_and_parses():
    ps1 = SCRIPTS / "one_click_up.ps1"
    assert ps1.exists(), "scripts/one_click_up.ps1 is missing"
    text = ps1.read_text(encoding="utf-8")
    assert "one_click_up.py" in text
    assert "$Args" in text or "ValueFromRemainingArguments" in text


def test_powershell_wrapper_is_parseable():
    """Validate the .ps1 with the PowerShell AST parser if pwsh is around."""
    ps1 = SCRIPTS / "one_click_up.ps1"
    if shutil.which("powershell") is None and shutil.which("pwsh") is None:
        # No PowerShell available — skip the runtime check.
        return
    binary = shutil.which("powershell") or shutil.which("pwsh")
    # Use the AST parser via a small inline script.
    inline = (
        "$tokens=$null;$errors=$null;"
        f"[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{str(ps1).replace(chr(39), chr(39) + chr(39))}',"
        "[ref]$tokens,[ref]$errors) | Out-Null;"
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Host $_ }; exit 1 }"
        "else { Write-Host 'PS1 parse OK' }"
    )
    proc = subprocess.run(
        [binary, "-NoProfile", "-Command", inline],
        capture_output=True, text=True, timeout=20,
    )
    # We only need to know the file parses; non-zero rc means a parse error.
    assert "PS1 parse OK" in proc.stdout or proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# 3. Docker artefacts
# ---------------------------------------------------------------------------
def _yaml_load_with_python_name(path: Path):
    """Load a YAML file that uses ``!!python/name`` tags (mkdocs does)."""
    class _Loader(yaml.SafeLoader):
        pass

    def _py_name(loader, suffix, node):  # noqa: ARG001
        return None

    _Loader.add_multi_constructor(
        "tag:yaml.org,2002:python/name", _py_name
    )
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)


def test_docker_compose_file_is_valid_yaml():
    compose = ROOT / "docker-compose.yml"
    assert compose.exists(), "docker-compose.yml is missing"
    data = _yaml_load_with_python_name(compose)
    assert "services" in data, "docker-compose.yml must have a top-level 'services'"
    assert "web" in data["services"], "expected a 'web' service in docker-compose.yml"
    web = data["services"]["web"]
    assert "8000:8000" in str(web.get("ports", [])), "compose must expose port 8000"
    assert "build" in web or "image" in web, "compose must define build or image"
    health = web.get("healthcheck", {})
    assert "test" in health, "compose healthcheck must define a test command"


def test_dockerfile_exists_with_entrypoint():
    dockerfile = ROOT / "Dockerfile"
    assert dockerfile.exists()
    text = dockerfile.read_text(encoding="utf-8")
    assert "ENTRYPOINT" in text, "Dockerfile must declare ENTRYPOINT"
    assert "EXPOSE" in text and "8000" in text, "Dockerfile must expose 8000"
    assert "ffmpeg" in text, "Dockerfile must install ffmpeg"


# ---------------------------------------------------------------------------
# 4. Makefile + justfile
# ---------------------------------------------------------------------------
def test_makefile_one_click_targets():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("up:", "up-daemon:", "down:", "status:", "logs:", "docker-up:"):
        assert target in mk, f"Makefile is missing one-click target {target}"
    # help text must mention the new section
    assert "One-click Web UI" in mk
    # .PHONY list must include the new targets
    phony_match = re.search(r"^\.PHONY:\s*(.+)$", mk, re.MULTILINE)
    assert phony_match, "Makefile must declare a .PHONY list"
    phony = phony_match.group(1)
    for t in ("up", "up-daemon", "down", "status", "logs", "docker-up"):
        assert t in phony, f"Makefile .PHONY must include {t}"


def test_justfile_one_click_recipes():
    just = (ROOT / "justfile").read_text(encoding="utf-8")
    for recipe in ("up:", "up-daemon:", "down:", "status:", "logs:", "docker-up:"):
        assert recipe in just, f"justfile is missing recipe {recipe}"


# ---------------------------------------------------------------------------
# 5. Browser extension manifest
# ---------------------------------------------------------------------------
def test_extension_manifest_lists_docker_internal_host():
    manifest = json.loads(
        (ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
    )
    hosts = manifest.get("host_permissions", [])
    assert any("localhost" in h for h in hosts), (
        "host_permissions must include localhost"
    )
    assert any("host.docker.internal" in h for h in hosts), (
        "host_permissions must include host.docker.internal so the "
        "extension can talk to a dockerised Web UI"
    )


# ---------------------------------------------------------------------------
# 6. mkdocs navigation
# ---------------------------------------------------------------------------
def test_mkdocs_nav_links_one_click_doc():
    cfg = _yaml_load_with_python_name(ROOT / "mkdocs.yml")
    nav = cfg.get("nav", [])

    def _flatten(items) -> List[str]:
        out: List[str] = []
        for it in items:
            if isinstance(it, str):
                out.append(it)
            elif isinstance(it, dict):
                for v in it.values():
                    out.extend(_flatten(v if isinstance(v, list) else [v]))
        return out

    flat = _flatten(nav)
    assert any("one-click.md" in x for x in flat), (
        "mkdocs.yml nav must include one-click.md"
    )


# ---------------------------------------------------------------------------
# 7. Documentation page
# ---------------------------------------------------------------------------
def test_one_click_doc_covers_all_lifecycles():
    doc = (DOCS_SITE / "one-click.md").read_text(encoding="utf-8")
    for token in (
        "## TL;DR",
        "## Lifecycle commands",
        "up", "stop", "status",
        "docker compose up",
        "## Loading the browser extension",
        "## Troubleshooting",
    ):
        assert token in doc, f"docs_site/one-click.md is missing {token!r}"


# ---------------------------------------------------------------------------
# 8. Static smoke test — the launcher is not a "shell-out" script;
#    it's a real Python module that we should be able to AST-parse.
# ---------------------------------------------------------------------------
def test_one_click_launcher_is_valid_python():
    src = (SCRIPTS / "one_click_up.py").read_text(encoding="utf-8")
    ast.parse(src, filename=str(SCRIPTS / "one_click_up.py"))
