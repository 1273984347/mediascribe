"""Offline Python installation-cost measurement contracts."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bench" / "asr" / "measure_install_cost.py"


def _load():
    spec = importlib.util.spec_from_file_location("mediascribe_measure_install_cost", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_install_command_uses_local_wheelhouse_and_asr_extra(tmp_path):
    module = _load()
    wheel = tmp_path / "mediascribe-3.4.0a1-py3-none-any.whl"
    command = module.install_command(tmp_path / "python", wheel, tmp_path / "wheelhouse")
    assert "mediascribe[faster-whisper]==3.4.0a1" in command
    assert "--no-index" in command
    assert "--only-binary=:all:" in command
    assert str(wheel) not in command


def test_install_environment_disables_network_and_isolates_home(tmp_path):
    module = _load()
    env = module.build_environment(tmp_path, tmp_path / "wheelhouse")
    assert env["PIP_NO_INDEX"] == "1"
    assert env["PIP_CONFIG_FILE"] == os.devnull
    assert env["HOME"] == str(tmp_path / "home")
    assert env["PIP_CACHE_DIR"] == str(tmp_path / "pip-cache")


def test_directory_size_counts_logical_file_bytes(tmp_path):
    module = _load()
    (tmp_path / "a.bin").write_bytes(b"1234")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.bin").write_bytes(b"12345")
    assert module.directory_size(tmp_path) == 9


def test_resource_preflight_never_lowers_threshold(monkeypatch):
    module = _load()
    monkeypatch.setattr(module, "available_memory_mb", lambda: 1024)
    assert module.resource_status()["code"] == "RESOURCE_GUARD_BLOCKED"
    monkeypatch.setattr(module, "available_memory_mb", lambda: 4096)
    assert module.resource_status()["status"] == "ready"
