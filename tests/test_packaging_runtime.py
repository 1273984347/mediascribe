"""Runtime packaging contracts for installable CLI, Web, MCP, and events."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_runtime_subpackages_are_declared_for_wheel_builds():
    packages = set(PYPROJECT["tool"]["setuptools"]["packages"])
    assert "mediascribe.events" in packages
    assert "mediascribe.mcp_server" in packages


def test_web_static_assets_are_declared_as_package_data():
    package_data = PYPROJECT["tool"]["setuptools"]["package-data"]
    web_patterns = set(package_data.get("mediascribe.web", []))
    assert "static/**/*" in web_patterns or "static/*" in web_patterns
