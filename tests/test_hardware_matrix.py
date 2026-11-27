"""US-014 hardware-tier matrix, metrics facets, and report contracts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mediascribe.events import schema
from mediascribe.metrics import calculate_metrics
from mediascribe.runtime_guard import HardwareSnapshot, plan

REPORT_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "bench" / "asr" / "hardware_matrix.py"
)


def _load_report_module():
    spec = importlib.util.spec_from_file_location("mediascribe_hardware_matrix", REPORT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("ram_mb", "expected"),
    [
        (0, "unknown"),
        (4095, "unknown"),
        (4096, "constrained_4"),
        (8191, "constrained_4"),
        (8192, "standard_8"),
        (16003, "standard_8"),
        (16383, "standard_8"),
        (16384, "reference_16"),
    ],
)
def test_hardware_tier_uses_conservative_visible_memory_boundaries(ram_mb, expected):
    assert schema._hardware_tier_from_mb(ram_mb) == expected


def test_hardware_tier_probe_failure_is_unknown(monkeypatch):
    monkeypatch.setitem(schema._common_cache, "hardware_tier", None)
    monkeypatch.setattr(schema, "_total_ram_mb", lambda: (_ for _ in ()).throw(OSError("probe")))
    assert schema.hardware_tier() == "unknown"


def test_event_context_rejects_unknown_hardware_tier(tmp_path, monkeypatch):
    import mediascribe.events as events

    monkeypatch.setenv("MEDIASCRIBE_INTERNAL_TEST", "0")
    store = events.EventStore(tmp_path / "events.sqlite3")
    context = schema.common_context()
    context["hardware_tier"] = "imaginary_32"
    with pytest.raises(ValueError, match="hardware_tier"):
        store.record_event("artifact_opened", context=context, properties={"action": "open"})


def _snapshot(ram_mb: int, *, cuda: bool = False, available_mb: int | None = None):
    return HardwareSnapshot(
        cuda_available=cuda,
        gpu_name="simulated" if cuda else "",
        vram_total_mb=8192 if cuda else 0,
        vram_used_mb=1024 if cuda else 0,
        ram_total_mb=ram_mb,
        ram_available_mb=available_mb if available_mb is not None else ram_mb // 2,
        cpu_cores=8,
    )


@pytest.mark.parametrize(
    ("ram_mb", "cpp_available", "device", "expected_engine"),
    [
        (4096, True, "cpu", "whisper-cpp"),
        (4096, False, "cpu", "faster-whisper"),
        (8192, True, "cpu", "faster-whisper"),
        (16384, True, "cpu", "faster-whisper"),
        (4096, True, "auto", "whisper-cpp"),
        (0, True, "cpu", "faster-whisper"),
    ],
)
def test_runtime_guard_simulated_memory_matrix(
    ram_mb, cpp_available, device, expected_engine, monkeypatch
):
    monkeypatch.setattr("mediascribe.runtime_guard._whisper_cpp_available", lambda: cpp_available)
    decision = plan("faster-whisper", "small", device, _snapshot(ram_mb))
    assert decision.engine == expected_engine


def test_runtime_guard_explicit_engine_is_not_replaced_in_simulated_4_gib(monkeypatch):
    monkeypatch.setattr("mediascribe.runtime_guard._whisper_cpp_available", lambda: True)
    decision = plan(
        "faster-whisper",
        "small",
        "cpu",
        _snapshot(4096),
        allow_engine_switch=False,
    )
    assert decision.engine == "faster-whisper"
    assert "explicit-engine-protected" in decision.reasons


def _event(name, install, tier, ts, properties=None):
    return {
        "event": name,
        "ts": ts,
        "install_id": install,
        "surface": "cli",
        "hardware_tier": tier,
        "is_internal_test": False,
        "properties": properties or {},
    }


def test_metrics_are_faceted_by_hardware_tier():
    rows = [
        _event("first_run_started", "eight", "standard_8", 1.0),
        _event(
            "transcription_finished",
            "eight",
            "standard_8",
            2.0,
            {
                "result": "success",
                "artifact_exists": True,
                "text_nonempty": True,
                "metadata_consistent": True,
            },
        ),
        _event("first_run_started", "four", "constrained_4", 1.0),
        _event(
            "transcription_finished",
            "four",
            "constrained_4",
            2.0,
            {"result": "failed", "error_code": "MODEL_LOAD_FAILED"},
        ),
    ]
    metrics = calculate_metrics(rows)
    assert metrics["by_hardware_tier"]["standard_8"]["successful_installs"] == 1
    assert metrics["by_hardware_tier"]["constrained_4"]["failed_installs"] == 1
    assert metrics["by_hardware_tier"]["constrained_4"]["successful_installs"] == 0


def test_report_marks_unobserved_tiers_as_not_run_and_computes_p50_p90():
    module = _load_report_module()
    host = {
        "platform": "Windows-simulated",
        "ram_total_mb": 16003,
        "ram_available_mb": 8000,
        "cuda_available": True,
        "gpu_name": "simulated-gpu",
        "gpu_vram_total_mb": 8192,
    }
    benchmark = {
        "schema": "mediascribe-benchmark/v2-asr",
        "runs": [
            {
                "engine": "faster_whisper",
                "model": "small",
                "device": "cpu",
                "precision": "int8",
                "wall_clock_s": 10.0,
                "error_rate_pct": 1.0,
                "error": None,
            },
            {
                "engine": "faster_whisper",
                "model": "small",
                "device": "cpu",
                "precision": "int8",
                "wall_clock_s": 20.0,
                "error_rate_pct": 2.0,
                "error": None,
            },
            {
                "engine": "faster_whisper",
                "model": "small",
                "device": "cpu",
                "precision": "int8",
                "wall_clock_s": 30.0,
                "error_rate_pct": 3.0,
                "error": None,
            },
            {
                "engine": "faster_whisper",
                "model": "small",
                "device": "cpu",
                "precision": "int8",
                "wall_clock_s": 999.0,
                "error_rate_pct": None,
                "error": "synthetic failure",
            },
        ],
        "meta": {"corpus": "output/test-corpus"},
    }
    report = module.build_report(
        host,
        benchmark,
        evidence_type="simulated_snapshot",
        commit="abc123",
        corpus_manifest={"sha256": "deadbeef", "files": 1},
        now=1_800_000_000.0,
    )
    assert report["schema"] == "mediascribe-hardware-matrix/v1"
    assert report["hardware_tier"] == "standard_8"
    assert report["evidence_type"] == "simulated_snapshot"
    assert report["commit"] == "abc123"
    assert report["corpus"]["sha256"] == "deadbeef"
    assert report["tiers"]["standard_8"]["status"] == "observed"
    assert report["tiers"]["reference_16"]["status"] == "not_run"
    assert report["tiers"]["constrained_4"]["status"] == "not_run"
    row = report["rows"][0]
    assert row["rounds_total"] == 4
    assert row["rounds_ok"] == 3
    assert row["rounds_failed"] == 1
    assert row["wall_clock_p50_s"] == 20.0
    assert row["wall_clock_p90_s"] == 28.0
    assert "999.0" not in json.dumps(row["wall_clock_p50_s"])


def test_report_marks_guard_blocked_tier_and_stable_error_code():
    module = _load_report_module()
    report = module.build_report(
        {"ram_total_mb": 8192},
        {
            "runs": [
                {
                    "engine": "faster_whisper",
                    "model": "small",
                    "device": "cpu",
                    "precision": "int8",
                    "error": "护栏熔断: 系统可用内存不足",
                }
            ]
        },
        evidence_type="real_device",
    )
    assert report["tiers"]["standard_8"]["status"] == "blocked"
    assert report["rows"][0]["error_kinds"] == {"RESOURCE_GUARD_BLOCKED": 1}


def test_report_rejects_invalid_evidence_type():
    module = _load_report_module()
    with pytest.raises(ValueError, match="evidence_type"):
        module.build_report(
            {"ram_total_mb": 8192},
            {"runs": []},
            evidence_type="claimed_real",
        )


def test_report_writes_json_and_markdown(tmp_path):
    module = _load_report_module()
    report = module.build_report(
        {"ram_total_mb": 8192},
        {"runs": []},
        evidence_type="real_device",
        commit="abc123",
        now=1_800_000_000.0,
    )
    json_path, markdown_path = module.write_report(report, tmp_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["schema"] == report["schema"]
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "standard_8" in markdown
    assert "not_run" in markdown
    assert "real_device" in markdown
