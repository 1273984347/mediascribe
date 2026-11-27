"""Public documentation must match the current CLI and packaging contract."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_install_examples_include_an_asr_engine_extra():
    readme = _text("README.md")
    getting_started = _text("docs/getting-started.md")
    web = _text("docs/web-ui.md")
    mcp = _text("docs/mcp.md")
    assert "mediascribe[web,faster-whisper]" in readme + getting_started + web
    assert "mediascribe[mcp,faster-whisper]" in readme + getting_started + mcp


def test_model_download_is_documented_as_explicit_opt_in():
    docs = "\n".join(
        _text(path)
        for path in (
            "README.md",
            "docs/getting-started.md",
            "docs/使用说明.md",
            "docs/engines.md",
            "docs/web-ui.md",
            "docs/mcp.md",
        )
    )
    assert "--allow-model-download" in docs
    assert "allow_model_download" in docs
    assert "首次运行自动下载模型" not in docs
    assert "首次运行会下载 Whisper 模型" not in docs


def test_public_agent_and_mcp_contracts_use_current_names():
    readme = _text("README.md")
    mcp = _text("docs/mcp.md")
    assert "mediascribe.agent-output/v1" in readme
    assert "video2text.agent-output/v1" not in readme
    assert "transcribe_url" not in mcp
    assert '"source"' in mcp
    assert "whisper_model" in mcp
    assert "output_dir" in mcp


def test_benchmark_docs_describe_repeat_and_install_cost():
    benchmarking = _text("docs/benchmarking.md")
    assert "--repeat 3" in benchmarking
    assert "measure_install_cost.py" in benchmarking
    assert "tests/fixtures/golden/us016" in benchmarking


def test_mkdocs_strict_gate_is_enabled():
    assert "strict: true" in _text("mkdocs.yml")


def test_verify_help_exposes_model_download_authorization(capsys):
    from mediascribe.__main__ import main

    with pytest.raises(SystemExit) as exc_info:
        main(["verify", "--help"])
    assert exc_info.value.code == 0
    assert "--allow-model-download" in capsys.readouterr().out


def test_archive_help_does_not_advertise_unimplemented_dry_run():
    from mediascribe.batch_cli import build_parser
    from mediascribe.i18n import t

    parser = build_parser(t)
    assert "--dry-run" not in parser.format_help()
