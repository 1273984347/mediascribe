"""Real ASR benchmark corpus, repeat, resume, and summary contracts."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASR_DIR = ROOT / "scripts" / "bench" / "asr"
FIXTURE = ROOT / "tests" / "fixtures" / "golden" / "us016"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"mediascribe_{name}", ASR_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_load_corpus_supports_verified_golden_manifest():
    bench = _load("bench_asr")
    rows = bench.load_corpus(FIXTURE)
    assert rows == [
        {
            "name": "us014_zh_3min.wav",
            "language": "zh",
            "duration_s": 179.837,
            "reference": "us014_zh_3min.json",
            "manifest_verified": True,
        }
    ]


def test_golden_corpus_tamper_fails_closed(tmp_path):
    bench = _load("bench_asr")
    corpus = tmp_path / "corpus"
    shutil.copytree(FIXTURE, corpus)
    audio = corpus / "us014_zh_3min.wav"
    audio.write_bytes(audio.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="hash"):
        bench.load_corpus(corpus)


def test_corpus_fingerprint_contains_fixed_hashes():
    bench = _load("bench_asr")
    fingerprint = bench.corpus_fingerprint(FIXTURE)
    assert (
        fingerprint["audio_sha256"]
        == "c1f6c7c65f02c1edcb8dd4a3d97221db80fb8dc5668c5f885bd61b45b7855da8"
    )
    assert (
        fingerprint["reference_sha256"]
        == "4284c6fc6a4dc69d2c32e73c0a9501105098cc3cdadb3fc1515b22a917033f3f"
    )


def test_repeat_round_files_are_unique_and_repeat_one_is_compatible(tmp_path):
    bench = _load("bench_asr")
    item = bench.PlanItem("faster_whisper", "small", "cpu", "int8")
    first = bench._round_file(tmp_path, item, "clip.wav", repeat_index=1)
    second = bench._round_file(tmp_path, item, "clip.wav", repeat_index=2)
    assert first.name == "faster_whisper_small_cpu_int8_clip.json"
    assert second.name == "faster_whisper_small_cpu_int8_clip__r02.json"
    assert first != second


def test_repeat_validation_rejects_non_positive_values():
    bench = _load("bench_asr")
    assert bench.validate_repeat(1) == 1
    assert bench.validate_repeat(3) == 3
    with pytest.raises(ValueError):
        bench.validate_repeat(0)
    with pytest.raises(ValueError):
        bench.validate_repeat(-1)


def _cached_round():
    return {
        "engine": "faster_whisper",
        "model": "small",
        "device": "cpu",
        "precision": "int8",
        "file": "clip.wav",
        "repeat_index": 2,
        "corpus_audio_sha256": "abc",
        "real_time_factor": 0.5,
        "error_rate_pct": 1.0,
        "error": None,
        "abort_reason": None,
    }


def test_resume_accepts_only_matching_successful_round(tmp_path):
    bench = _load("bench_asr")
    item = bench.PlanItem("faster_whisper", "small", "cpu", "int8")
    cached = _cached_round()
    assert bench.round_is_reusable(
        cached,
        item=item,
        wav_name="clip.wav",
        repeat_index=2,
        corpus_fingerprint={"audio_sha256": "abc"},
    )
    assert not bench.round_is_reusable(
        {**cached, "error": "failed"},
        item=item,
        wav_name="clip.wav",
        repeat_index=2,
        corpus_fingerprint={"audio_sha256": "abc"},
    )
    assert not bench.round_is_reusable(
        {**cached, "corpus_audio_sha256": "old"},
        item=item,
        wav_name="clip.wav",
        repeat_index=2,
        corpus_fingerprint={"audio_sha256": "abc"},
    )


def test_summary_computes_p50_p90_and_excludes_failed_rounds(tmp_path):
    summary = _load("summarize_asr")
    runs = []
    for value in (10.0, 20.0, 30.0):
        runs.append(
            {
                "engine": "faster_whisper",
                "model": "small",
                "device": "cpu",
                "precision": "int8",
                "wall_clock_s": value,
                "real_time_factor": value / 100.0,
                "error_rate_pct": value / 10.0,
                "error": None,
            }
        )
    runs.append(
        {
            "engine": "faster_whisper",
            "model": "small",
            "device": "cpu",
            "precision": "int8",
            "wall_clock_s": 999.0,
            "real_time_factor": 9.99,
            "error_rate_pct": None,
            "error": "synthetic failure",
        }
    )
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"runs": runs}), encoding="utf-8")
    row = summary.summarize(path)["rows"][0]
    assert row["n_total"] == 4
    assert row["n_ok"] == 3
    assert row["wall_clock_p50_s"] == 20.0
    assert row["wall_clock_p90_s"] == 28.0
    assert row["rtf_p50"] == 0.2
    assert row["rtf_p90"] == 0.28
    assert row["err_p50_pct"] == 2.0
    assert row["err_p90_pct"] == 2.8
