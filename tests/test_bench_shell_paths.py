from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy_to_repo(tmp_path: Path, relative: str) -> Path:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / relative, target)
    return target


def test_b2_server_fails_without_a_discoverable_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIASCRIBE_REPO_ROOT", raising=False)
    script = _copy_to_repo(tmp_path, "scripts/bench/shell/apps/b2-react/server.py")

    with pytest.raises(RuntimeError, match="MEDIASCRIBE_REPO_ROOT"):
        _load_module("b2_server_without_repo", script)


def test_b5_build_paths_follow_script_location(tmp_path):
    script = _copy_to_repo(tmp_path, "scripts/bench/shell/apps/b5-wasm/build_wasm.py")
    module = _load_module("b5_build_paths", script)

    assert module.TOOLS == tmp_path / "output/bench/shell/tools"
    assert module.WORK == tmp_path / "output/bench/shell/b5-build"
    assert module.APPDIR == tmp_path / "output/bench/shell/b5-app"


def test_b5_diag_default_path_follows_script_location(tmp_path):
    script = _copy_to_repo(tmp_path, "scripts/bench/shell/apps/b5-wasm/diag_wasm.py")
    module = _load_module("b5_diag_paths", script)

    assert module.DEFAULT_MAIN_JS == tmp_path / "output/bench/shell/b5-app/main.js"
