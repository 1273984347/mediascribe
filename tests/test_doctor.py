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


def test_empty_model_cache_does_not_promise_automatic_download(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "_list_downloaded_models", lambda _cache: [])
    doctor._check_model_cache()
    output = capsys.readouterr().out
    assert "自动下载模型" not in output
    assert "显式授权" in output or "explicitly authorize" in output


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


@pytest.fixture()
def isolated_doctor(monkeypatch, clean_doctor_env):
    monkeypatch.setattr(doctor, "_check_device", lambda: None)
    monkeypatch.setattr(doctor, "_check_gpu_budget", lambda: None)
    monkeypatch.setattr(doctor, "_check_model_cache", lambda: None)
    monkeypatch.setattr(doctor, "_check_cookies", lambda: None)
    monkeypatch.setattr(doctor, "_check_terms", lambda: None)
    monkeypatch.setattr(doctor, "_check_env_config", lambda: None)
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: f"C:/fake/{name}.exe" if name in {"ffmpeg", "ffprobe"} else None,
    )


def _patch_engine_probe(monkeypatch, available):
    def fake_find_spec(name):
        return object() if name in available else None

    monkeypatch.setattr(doctor.importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr("mediascribe.config.find_spec", fake_find_spec)


def _patch_weight_check(monkeypatch, *, ready):
    from mediascribe.model_weights import WeightStatus

    monkeypatch.setattr(
        "mediascribe.model_weights.check_weights",
        lambda engine, model: WeightStatus(
            engine=engine,
            model=model,
            ok=ready,
            location="fake-cache",
            hint="provision model",
        ),
    )


def test_run_doctor_ok_with_ffmpeg(tmp_path, monkeypatch, capsys, clean_doctor_env):
    monkeypatch.setattr(
        shutil, "which", lambda name: "C:/fake/ffmpeg.exe" if name == "ffmpeg" else None
    )
    rc = run_doctor(workspace=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    # 文案走 t();断言兼容 en/zh 两种 locale 渲染
    assert "自检完成" in out or "check complete" in out
    assert "环境自检" in out or "environment check" in out


def test_run_doctor_fails_without_ffmpeg(tmp_path, monkeypatch, capsys, clean_doctor_env):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    rc = run_doctor(workspace=tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "关键缺失" in out or "critical dependency missing" in out


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


def test_doctor_blocks_without_any_engine(tmp_path, monkeypatch, capsys, isolated_doctor):
    _patch_engine_probe(monkeypatch, available=set())

    rc = run_doctor(workspace=tmp_path)

    out = capsys.readouterr().out
    assert rc == 1
    assert "BLOCKED" in out
    assert "ENGINE_UNAVAILABLE" in out


def test_doctor_ready_when_engine_and_model_are_available(
    tmp_path, monkeypatch, capsys, isolated_doctor
):
    _patch_engine_probe(monkeypatch, available={"faster_whisper"})
    _patch_weight_check(monkeypatch, ready=True)

    rc = run_doctor(workspace=tmp_path)

    out = capsys.readouterr().out
    assert rc == 0
    assert "READY" in out
    assert "READY_WITH_WARNINGS" not in out
    assert "BLOCKED" not in out


def test_doctor_warns_when_model_can_be_prepared_online(
    tmp_path, monkeypatch, capsys, isolated_doctor
):
    _patch_engine_probe(monkeypatch, available={"faster_whisper"})
    _patch_weight_check(monkeypatch, ready=False)

    rc = run_doctor(workspace=tmp_path)

    out = capsys.readouterr().out
    assert rc == 0
    assert "READY_WITH_WARNINGS" in out


def test_doctor_warns_when_event_retention_is_invalid(
    tmp_path, monkeypatch, capsys, isolated_doctor
):
    _patch_engine_probe(monkeypatch, available={"faster_whisper"})
    _patch_weight_check(monkeypatch, ready=True)
    monkeypatch.setenv("MEDIASCRIBE_EVENT_DB", str(tmp_path / "events.sqlite3"))
    monkeypatch.setenv("MEDIASCRIBE_EVENT_RETENTION_DAYS", "-1")

    rc = run_doctor(workspace=tmp_path)

    out = capsys.readouterr().out
    assert rc == 0
    assert "READY_WITH_WARNINGS" in out
    assert "EVENT_RETENTION_INVALID" in out


def test_doctor_blocks_missing_model_in_offline_mode(
    tmp_path, monkeypatch, capsys, isolated_doctor
):
    _patch_engine_probe(monkeypatch, available={"faster_whisper"})
    _patch_weight_check(monkeypatch, ready=False)

    rc = run_doctor(workspace=tmp_path, offline=True)

    out = capsys.readouterr().out
    assert rc == 1
    assert "BLOCKED" in out
    assert "MODEL_NOT_READY" in out


def test_doctor_blocks_unsupported_python(tmp_path, monkeypatch, capsys, isolated_doctor):
    from collections import namedtuple

    _patch_engine_probe(monkeypatch, available={"faster_whisper"})
    _patch_weight_check(monkeypatch, ready=True)
    version_info = namedtuple("version_info", "major minor micro")
    monkeypatch.setattr(doctor.sys, "version_info", version_info(3, 10, 0))

    rc = run_doctor(workspace=tmp_path)

    out = capsys.readouterr().out
    assert rc == 1
    assert "BLOCKED" in out
    assert "PYTHON_VERSION" in out


def test_doctor_blocks_unwritable_workspace(tmp_path, monkeypatch, capsys, isolated_doctor):
    _patch_engine_probe(monkeypatch, available={"faster_whisper"})
    _patch_weight_check(monkeypatch, ready=True)

    def fail_probe(self, *args, **kwargs):
        if self.name == ".doctor_write_probe":
            raise PermissionError("read-only")
        return original_write_text(self, *args, **kwargs)

    original_write_text = Path.write_text
    monkeypatch.setattr(Path, "write_text", fail_probe)

    rc = run_doctor(workspace=tmp_path)

    out = capsys.readouterr().out
    assert rc == 1
    assert "BLOCKED" in out
    assert "OUTPUT_WRITE_FAILED" in out
