"""Release publishing must depend on the offline first-success gate."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow():
    return yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )


def test_release_workflow_runs_first_success_gate_after_build():
    jobs = _workflow()["jobs"]
    assert "first-success" in jobs
    gate = jobs["first-success"]
    assert "build" in gate["needs"]
    commands = "\n".join(
        step.get("run", "") + " " + str(step.get("env", {})) for step in gate["steps"]
    )
    assert "scripts/gates/first_success_gate.py" in commands
    assert "--no-index" in commands or "PIP_NO_INDEX" in commands
    assert "HF_HUB_OFFLINE" in commands or "first_success_gate.py" in commands
    assert "mediascribe[faster-whisper]" in commands


def test_publish_jobs_require_successful_first_success_gate():
    jobs = _workflow()["jobs"]
    assert "first-success" in jobs["publish-pypi"]["needs"]
    assert "first-success" in jobs["github-release"]["needs"]
    condition = jobs["github-release"].get("if", "")
    assert "needs.first-success.result == 'success'" in condition
