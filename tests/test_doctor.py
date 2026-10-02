"""Tests for ``mediascribe.doctor`` — 环境自检 (此前零测试, 2026-10-03 审查补齐)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mediascribe import doctor
from mediascribe.doctor import (
    _check,
    _engine_available,
    _list_downloaded_models,
    _model_cache_dir,
    run_doctor,
)


# ---------------------------------------------------------------------------
# _check — 打印 + 返回布尔
# ---------------------------------------------------------------------------
def test_check_ok_prints_checkmark(capsys):
    assert _check("ffmpeg", True, "/usr/bin/ffmpeg") is True
    out = capsys.readouterr().out
    assert "✅" in out and "ffmpeg" in out and "/usr/bin/ffmpeg" in out


def test_check_fail_prints_warn_and_hint(capsys):
    assert _check("ffmpeg", False, "未找到", "装一下") is False
    out = capsys.readouterr().out
    assert "⚠️" in out and "💡" in out and "装一下" in out


def test_check_ok_hides_hint(capsys):
    _check("x", True, "", "不应出现")
    assert "不应出现" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _engine_available
# ---------------------------------------------------------------------------
def test_engine_available_true_for_installed_module():
    ok, detail = _engine_available("os")
    assert ok is True and detail == "已安装"


def test_engine_available_false_for_missing_module():
    ok, detail = _engine_available("definitely_not_a_real_module_xyz")
    assert ok is False and detail == "未安装"


# ---------------------------------------------------------------------------
# _model_cache_dir — env 优先级
# ---------------------------------------------------------------------------
def test_cache_dir_env_override(monkeypatch):
    monkeypatch.setenv("MEDIASCRIBE_CACHE_DIR", "D:/cache-custom")
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert _model_cache_dir() == Path("D:/cache-custom")


def test_cache_dir_xdg_fallback(monkeypatch):
    monkeypatch.delenv("MEDIASCRIBE_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", "D:/xdg")
    assert _model_cache_dir() == Path("D:/xdg") / "mediascribe"


def test_cache_dir_home_default(monkeypatch):
    monkeypatch.delenv("MEDIASCRIBE_CACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert _model_cache_dir() == Path.home() / ".cache" / "mediascribe"


# ---------------------------------------------------------------------------
# _list_downloaded_models
# ---------------------------------------------------------------------------
def test_list_models_missing_dir_returns_empty(tmp_path):
    assert _list_downloaded_models(tmp_path / "nope") == []


def test_list_models_none_returns_empty():
    assert _list_downloaded_models(None) == []


def test_list_models_dirs_only_skips_dotfiles(tmp_path):
    (tmp_path / "large-v3").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    assert _list_downloaded_models(tmp_path) == ["large-v3"]


# ---------------------------------------------------------------------------
# run_doctor — 退出码由 ffmpeg 决定
# ---------------------------------------------------------------------------
@pytest.fixture()
def clean_doctor_env(monkeypatch):
    monkeypatch.delenv("MEDIASCRIBE_WECHAT_COOKIE", raising=False)
    monkeypatch.delenv("MEDIASCRIBE_WORKSPACE", raising=False)


def test_run_doctor_ok_with_ffmpeg(tmp_path, monkeypatch, capsys, clean_doctor_env):
    monkeypatch.setattr(
        shutil, "which", lambda name: "C:/fake/ffmpeg.exe" if name == "ffmpeg" else None
    )
    rc = run_doctor(workspace=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "自检完成" in out
    assert "环境自检" in out


def test_run_doctor_fails_without_ffmpeg(tmp_path, monkeypatch, capsys, clean_doctor_env):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    rc = run_doctor(workspace=tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "关键缺失" in out


def test_run_doctor_default_workspace_from_env(tmp_path, monkeypatch, capsys, clean_doctor_env):
    monkeypatch.setenv("MEDIASCRIBE_WORKSPACE", str(tmp_path / "ws-env"))
    monkeypatch.setattr(shutil, "which", lambda name: "ffmpeg" if name == "ffmpeg" else None)
    rc = run_doctor()
    assert rc == 0
    assert (tmp_path / "ws-env").is_dir()
    assert "ws-env" in capsys.readouterr().out


def test_run_doctor_reports_wechat_cookie_env(tmp_path, monkeypatch, capsys, clean_doctor_env):
    monkeypatch.setenv("MEDIASCRIBE_WECHAT_COOKIE", "SESSDATA=abc")
    monkeypatch.setattr(shutil, "which", lambda name: "ffmpeg" if name == "ffmpeg" else None)
    run_doctor(workspace=tmp_path)
    out = capsys.readouterr().out
    assert "已配置" in out
