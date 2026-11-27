"""Offline clean-environment first-success gate contracts."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gates" / "first_success_gate.py"
FIXTURE = ROOT / "tests" / "fixtures" / "golden" / "us016"


def _load_gate():
    spec = importlib.util.spec_from_file_location("mediascribe_first_success_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _acceptance():
    return json.loads((FIXTURE / "acceptance.json").read_text(encoding="utf-8"))


def test_fixed_golden_corpus_hashes_verify():
    gate = _load_gate()
    manifest = gate.verify_corpus(FIXTURE)
    assert (
        manifest["audio_sha256"]
        == "c1f6c7c65f02c1edcb8dd4a3d97221db80fb8dc5668c5f885bd61b45b7855da8"
    )
    assert (
        manifest["reference_sha256"]
        == "4284c6fc6a4dc69d2c32e73c0a9501105098cc3cdadb3fc1515b22a917033f3f"
    )


def test_corpus_tamper_fails_closed(tmp_path):
    gate = _load_gate()
    corpus = tmp_path / "corpus"
    shutil.copytree(FIXTURE, corpus)
    audio = corpus / "us014_zh_3min.wav"
    audio.write_bytes(audio.read_bytes() + b"tampered")
    with pytest.raises(gate.GateFailure) as exc_info:
        gate.verify_corpus(corpus)
    assert exc_info.value.code == "CORPUS_HASH_MISMATCH"


def test_gate_environment_is_offline_and_isolated(tmp_path):
    gate = _load_gate()
    env = gate.gate_environment(tmp_path, tmp_path / "model-cache")
    assert env["PIP_NO_INDEX"] == "1"
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"
    assert env["MEDIASCRIBE_LLM_ENABLED"] == "0"
    assert env["MEDIASCRIBE_INTERNAL_TEST"] == "0"
    assert env["MEDIASCRIBE_EVENT_DB"] == str(tmp_path / "events.sqlite3")
    assert env["HF_HUB_CACHE"] == str(tmp_path / "model-cache")


def test_install_command_requests_faster_whisper_extra(tmp_path):
    gate = _load_gate()
    wheel = tmp_path / "mediascribe-3.4.0a1-py3-none-any.whl"
    command = gate.install_command(tmp_path / "python", wheel, tmp_path / "wheelhouse")
    assert "mediascribe[faster-whisper]==3.4.0a1" in command
    assert "--no-index" in command
    assert str(wheel) not in command


def test_transcribe_command_uses_fixed_local_input_and_no_download(tmp_path):
    gate = _load_gate()
    command = gate.transcribe_command(
        tmp_path / "venv" / "python",
        FIXTURE,
        tmp_path / "workspace",
        _acceptance(),
    )
    assert str(FIXTURE / "us014_zh_3min.wav") in command
    assert "--allow-model-download" not in command
    assert command[command.index("--engine") + 1] == "faster-whisper"
    assert command[command.index("--model") + 1] == "small"
    assert command[command.index("--device") + 1] == "cpu"


def _write_artifact(tmp_path: Path, markdown: str, metadata: dict):
    markdown_path = tmp_path / "result.md"
    metadata_path = tmp_path / "result.json"
    markdown_path.write_text(markdown, encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    return markdown_path, metadata_path


def _metadata():
    return {
        "source": {"kind": "audio", "path": "/fixed/golden.wav"},
        "engine": "faster-whisper",
        "model": "small",
        "language": "zh",
        "llm_post_process": {"status": "llm-disabled"},
        "resource_degrade_reasons": [],
        "selection_requested": {"engine": "faster-whisper", "model": "small", "device": "cpu"},
        "selection_effective": {"engine": "faster-whisper", "model": "small", "device": "cpu"},
    }


def test_artifact_validation_accepts_real_body_and_metadata(tmp_path):
    gate = _load_gate()
    markdown = "# Transcript\n\n## 基本信息\n\n- 转录引擎: faster-whisper\n\n## 转录内容\n\n人工智能改变软件开发，技术债需要评审。\n"
    paths = _write_artifact(tmp_path, markdown, _metadata())
    result = gate.validate_artifact(*paths, _acceptance())
    assert result["artifact_exists"] is True
    assert result["text_nonempty"] is True
    assert result["metadata_consistent"] is True
    assert result["required_keywords_found"] == ["人工智能", "软件开发", "技术债"]


@pytest.mark.parametrize(
    ("markdown", "metadata", "code"),
    [
        ("# Transcript\n\n## 转录内容\n", _metadata(), "ARTIFACT_BODY_EMPTY"),
        ("# Transcript\n\n## 转录内容\n\n没有关键词\n", _metadata(), "ARTIFACT_KEYWORD_MISSING"),
        (
            "# Transcript\n\n## 转录内容\n\n人工智能 软件开发 技术债\n",
            {**_metadata(), "engine": "whisper"},
            "ARTIFACT_METADATA_MISMATCH",
        ),
    ],
)
def test_artifact_validation_rejects_false_success(tmp_path, markdown, metadata, code):
    gate = _load_gate()
    paths = _write_artifact(tmp_path, markdown, metadata)
    with pytest.raises(gate.GateFailure) as exc_info:
        gate.validate_artifact(*paths, _acceptance())
    assert exc_info.value.code == code


def _event(name, properties=None):
    return {
        "event": name,
        "surface": "cli",
        "is_internal_test": False,
        "properties": properties or {},
    }


def test_event_and_metrics_validation_accepts_first_success():
    gate = _load_gate()
    events = {
        "schema": "mediascribe.local-events/v2",
        "events": [
            _event("first_run_started"),
            _event("readiness_checked", {"status": "READY", "exit_code": 0}),
            _event(
                "input_submitted",
                {"input_kind": "local_audio", "duration_bucket": "1-3m", "network_required": False},
            ),
            _event(
                "transcription_finished",
                {
                    "result": "success",
                    "artifact_exists": True,
                    "text_nonempty": True,
                    "metadata_consistent": True,
                },
            ),
        ],
    }
    metrics = {
        "totals": {
            "new_installations": 1,
            "submitted_installs": 1,
            "successful_installs": 1,
        },
        "rates": {"success_rate": 1.0},
    }
    result = gate.validate_events(events, metrics)
    assert result["new_installations"] == 1
    assert result["successful_installs"] == 1
    assert result["success_rate"] == 1.0


def test_event_validation_rejects_sensitive_content():
    gate = _load_gate()
    events = {
        "schema": "mediascribe.local-events/v2",
        "events": [_event("input_submitted", {"raw_input": "/private/golden.wav"})],
    }
    with pytest.raises(gate.GateFailure) as exc_info:
        gate.validate_events(events, {"totals": {}, "rates": {}})
    assert exc_info.value.code == "EVENT_PRIVACY_VIOLATION"


def test_available_memory_uses_sysconf_when_psutil_missing(monkeypatch):
    gate = _load_gate()
    monkeypatch.setitem(__import__("sys").modules, "psutil", None)
    values = {"SC_AVPHYS_PAGES": 1024, "SC_PAGE_SIZE": 4 * 1024 * 1024}
    monkeypatch.setattr(gate.os, "sysconf", lambda name: values[name], raising=False)
    assert gate.available_memory_mb() == 4096


def test_resource_preflight_is_conservative(monkeypatch, tmp_path):
    gate = _load_gate()
    monkeypatch.setattr(gate, "available_memory_mb", lambda: 1024)
    status = gate.resource_status(min_available_mb=2048)
    assert status["status"] == "blocked"
    assert status["code"] == "RESOURCE_GUARD_BLOCKED"
    monkeypatch.setattr(gate, "available_memory_mb", lambda: 4096)
    assert gate.resource_status(min_available_mb=2048)["status"] == "ready"
