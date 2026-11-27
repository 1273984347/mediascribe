"""Offline clean-environment first-success release gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPORT_SCHEMA = "mediascribe-first-success/v1"
_FORBIDDEN_PROPERTY_TOKENS = (
    "url",
    "path",
    "file",
    "title",
    "text",
    "transcript",
    "cookie",
    "token",
    "secret",
    "key",
    "raw_input",
)


class GateFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_corpus(corpus_dir: Path) -> Dict[str, Any]:
    corpus_dir = Path(corpus_dir)
    manifest_path = corpus_dir / "manifest.json"
    if not manifest_path.is_file():
        raise GateFailure("CORPUS_MANIFEST_MISSING", f"missing corpus manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks = (
        ("us014_zh_3min.wav", "audio_sha256"),
        ("us014_zh_3min.json", "reference_sha256"),
    )
    for filename, key in checks:
        path = corpus_dir / filename
        if not path.is_file() or sha256_file(path) != manifest.get(key):
            raise GateFailure("CORPUS_HASH_MISMATCH", f"golden corpus hash mismatch: {filename}")
    if not (corpus_dir / "acceptance.json").is_file():
        raise GateFailure("CORPUS_ACCEPTANCE_MISSING", "missing acceptance.json")
    return manifest


def available_memory_mb() -> int:
    try:
        import psutil  # type: ignore[import-not-found]

        return int(psutil.virtual_memory().available / 1024 / 1024)
    except Exception:
        pass
    try:
        sysconf = getattr(os, "sysconf", None)
        if sysconf is not None:
            pages = int(sysconf("SC_AVPHYS_PAGES"))
            page_size = int(sysconf("SC_PAGE_SIZE"))
            if pages > 0 and page_size > 0:
                return int(pages * page_size / 1024 / 1024)
    except (ValueError, OSError, AttributeError):
        pass
    return 0


def resource_status(*, min_available_mb: int = 2048) -> Dict[str, Any]:
    available = available_memory_mb()
    if available < min_available_mb:
        return {
            "status": "blocked",
            "code": "RESOURCE_GUARD_BLOCKED",
            "available_mb": available,
            "min_available_mb": min_available_mb,
        }
    return {
        "status": "ready",
        "code": None,
        "available_mb": available,
        "min_available_mb": min_available_mb,
    }


def gate_environment(run_dir: Path, model_cache: Path) -> Dict[str, str]:
    run_dir = Path(run_dir).resolve()
    home = run_dir / "home"
    workspace = run_dir / "workspace"
    app_data = run_dir / "app-data"
    for path in (home, workspace, app_data):
        path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(app_data),
            "MEDIASCRIBE_WORKSPACE": str(workspace),
            "MEDIASCRIBE_APP_DATA": str(app_data),
            "MEDIASCRIBE_EVENT_DB": str(run_dir / "events.sqlite3"),
            "MEDIASCRIBE_EVENT_RETENTION_DAYS": "90",
            "MEDIASCRIBE_INTERNAL_TEST": "0",
            "MEDIASCRIBE_LLM_ENABLED": "0",
            "MEDIASCRIBE_E2E": "0",
            "HF_HOME": str(Path(model_cache).resolve().parent),
            "HF_HUB_CACHE": str(Path(model_cache).resolve()),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PIP_NO_INDEX": "1",
        }
    )
    return env


def transcribe_command(
    python: Path,
    corpus_dir: Path,
    workspace: Path,
    acceptance: Dict[str, Any],
) -> List[str]:
    output = Path(workspace) / "transcripts" / "us016-result.md"
    return [
        str(python),
        "-m",
        "mediascribe",
        "transcribe",
        str(Path(corpus_dir) / "us014_zh_3min.wav"),
        "--workspace",
        str(workspace),
        "--engine",
        str(acceptance["engine"]),
        "--model",
        str(acceptance["model"]),
        "--device",
        str(acceptance["device"]),
        "--language",
        str(acceptance["language"]),
        "--output",
        str(output),
    ]


def _artifact_body(markdown: str) -> str:
    for heading in ("## 转录内容", "## Transcript"):
        if heading in markdown:
            return markdown.split(heading, 1)[1].strip()
    return markdown.strip()


def validate_artifact(
    markdown_path: Path,
    metadata_path: Path,
    acceptance: Dict[str, Any],
) -> Dict[str, Any]:
    markdown_path = Path(markdown_path)
    metadata_path = Path(metadata_path)
    if not markdown_path.is_file() or not metadata_path.is_file():
        raise GateFailure("ARTIFACT_MISSING", "Markdown or metadata artifact is missing")
    markdown = markdown_path.read_text(encoding="utf-8")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    body = _artifact_body(markdown)
    if not body:
        raise GateFailure("ARTIFACT_BODY_EMPTY", "artifact contains no transcript body")
    found = [keyword for keyword in acceptance["required_keywords"] if keyword in body]
    if found != acceptance["required_keywords"]:
        raise GateFailure(
            "ARTIFACT_KEYWORD_MISSING", "artifact is missing required golden semantics"
        )
    requested = metadata.get("selection_requested") or {}
    effective = metadata.get("selection_effective") or {}
    expected = {
        "engine": acceptance["engine"],
        "model": acceptance["model"],
        "device": acceptance["device"],
    }
    consistent = (
        (metadata.get("source") or {}).get("kind") == "audio"
        and metadata.get("engine") == expected["engine"]
        and metadata.get("model") == expected["model"]
        and requested == expected
        and effective == expected
        and not metadata.get("resource_degrade_reasons")
        and (metadata.get("llm_post_process") or {}).get("status") == "llm-disabled"
    )
    if not consistent:
        raise GateFailure("ARTIFACT_METADATA_MISMATCH", "artifact metadata violates acceptance")
    return {
        "artifact_exists": True,
        "text_nonempty": True,
        "metadata_consistent": True,
        "required_keywords_found": found,
    }


def _validate_privacy(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in _FORBIDDEN_PROPERTY_TOKENS) and lowered not in {
                "text_nonempty"
            }:
                raise GateFailure("EVENT_PRIVACY_VIOLATION", f"forbidden event field: {key}")
            _validate_privacy(child)
    elif isinstance(value, list):
        for child in value:
            _validate_privacy(child)


def validate_events(
    events_payload: Dict[str, Any], metrics_payload: Dict[str, Any]
) -> Dict[str, Any]:
    _validate_privacy(events_payload)
    if events_payload.get("schema") != "mediascribe.local-events/v2":
        raise GateFailure("EVENT_SCHEMA_INVALID", "event export is not local-events/v2")
    rows = list(events_payload.get("events") or [])
    names = {row.get("event") for row in rows}
    required = {
        "first_run_started",
        "readiness_checked",
        "input_submitted",
        "transcription_finished",
    }
    if not required <= names:
        raise GateFailure("EVENT_SEQUENCE_INCOMPLETE", "first-success event sequence is incomplete")
    if any(row.get("is_internal_test") for row in rows):
        raise GateFailure(
            "EVENT_INTERNAL_TEST", "first-success gate events must not be internal tests"
        )
    submitted = [row for row in rows if row.get("event") == "input_submitted"]
    properties = submitted[-1].get("properties") or {}
    if (
        properties.get("input_kind") != "local_audio"
        or properties.get("duration_bucket") != "1-3m"
        or properties.get("network_required") is not False
    ):
        raise GateFailure("EVENT_INPUT_INVALID", "local input event has incorrect classification")
    terminal = [row for row in rows if row.get("event") == "transcription_finished"][-1]
    terminal_properties = terminal.get("properties") or {}
    if (
        terminal_properties.get("result") != "success"
        or terminal_properties.get("artifact_exists") is not True
        or terminal_properties.get("text_nonempty") is not True
        or terminal_properties.get("metadata_consistent") is not True
    ):
        raise GateFailure("EVENT_SUCCESS_INVALID", "success event lacks validated artifact flags")
    totals = metrics_payload.get("totals") or {}
    rates = metrics_payload.get("rates") or {}
    result = {
        "new_installations": totals.get("new_installations"),
        "submitted_installs": totals.get("submitted_installs"),
        "successful_installs": totals.get("successful_installs"),
        "success_rate": rates.get("success_rate"),
    }
    expected = {
        "new_installations": 1,
        "submitted_installs": 1,
        "successful_installs": 1,
        "success_rate": 1.0,
    }
    if result != expected:
        raise GateFailure("METRICS_MISMATCH", f"first-success metrics mismatch: {result}")
    return result


def _run_step(
    name: str,
    command: List[str],
    *,
    env: Dict[str, str],
    cwd: Path,
    log_dir: Path,
) -> subprocess.CompletedProcess:
    log_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    (log_dir / f"{name}.log").write_text(
        f"$ {' '.join(command)}\n\n{proc.stdout}\n{proc.stderr}", encoding="utf-8"
    )
    if proc.returncode != 0:
        raise GateFailure(
            f"STEP_{name.upper()}_FAILED", f"{name} failed with exit {proc.returncode}"
        )
    return proc


def install_command(python: Path, wheel: Path, wheelhouse: Path) -> List[str]:
    version = Path(wheel).name.split("-")[1]
    return [
        str(python),
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        str(Path(wheelhouse).resolve()),
        f"mediascribe[faster-whisper]=={version}",
    ]


def _python_path(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def write_report(report: Dict[str, Any], work_dir: Path) -> tuple[Path, Path]:
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    json_path = work_dir / "first-success-report.json"
    markdown_path = work_dir / "first-success-report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# MediaScribe First Success Gate",
        "",
        f"- Status: `{report['status']}`",
        f"- Code: `{report.get('code') or '-'}`",
        f"- Commit: `{report.get('commit') or 'unknown'}`",
        f"- Evidence: `{report.get('evidence_type') or 'real_device'}`",
        "",
        "## Steps",
        "",
    ]
    lines.extend(f"- `{step['name']}`: {step['status']}" for step in report.get("steps", []))
    if report.get("error"):
        lines.extend(["", "## Error", "", str(report["error"])])
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def run_gate(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "running",
        "code": None,
        "commit": args.commit,
        "evidence_type": "real_device",
        "steps": [],
        "resources": resource_status(min_available_mb=args.min_available_mb),
    }
    try:
        if report["resources"]["status"] == "blocked":
            raise GateFailure(
                "RESOURCE_GUARD_BLOCKED", "available memory is below the gate threshold"
            )
        report["corpus"] = verify_corpus(Path(args.corpus))
        acceptance = json.loads((Path(args.corpus) / "acceptance.json").read_text(encoding="utf-8"))
        env = gate_environment(work_dir, Path(args.model_cache))
        venv_dir = work_dir / "venv"
        _run_step(
            "venv",
            [sys.executable, "-m", "venv", str(venv_dir)],
            env=env,
            cwd=work_dir,
            log_dir=work_dir / "logs",
        )
        report["steps"].append({"name": "venv", "status": "passed"})
        python = _python_path(venv_dir)
        _run_step(
            "install",
            install_command(python, Path(args.wheel), Path(args.wheelhouse)),
            env=env,
            cwd=work_dir,
            log_dir=work_dir / "logs",
        )
        report["steps"].append({"name": "install", "status": "passed"})
        _run_step(
            "doctor",
            [
                str(python),
                "-m",
                "mediascribe",
                "doctor",
                "--offline",
                "--workspace",
                str(work_dir / "workspace"),
            ],
            env=env,
            cwd=work_dir,
            log_dir=work_dir / "logs",
        )
        report["steps"].append({"name": "doctor", "status": "passed"})
        _run_step(
            "model-ready",
            [
                str(python),
                "-c",
                "from mediascribe.model_weights import check_weights; "
                "s=check_weights('faster-whisper','small'); assert s.ok, s.message()",
            ],
            env=env,
            cwd=work_dir,
            log_dir=work_dir / "logs",
        )
        report["steps"].append({"name": "model-ready", "status": "passed"})
        _run_step(
            "transcribe",
            transcribe_command(python, Path(args.corpus), work_dir / "workspace", acceptance),
            env=env,
            cwd=work_dir,
            log_dir=work_dir / "logs",
        )
        report["steps"].append({"name": "transcribe", "status": "passed"})
        report["artifact"] = validate_artifact(
            work_dir / "workspace" / "transcripts" / "us016-result.md",
            work_dir / "workspace" / "metadata" / "us016-result.json",
            acceptance,
        )
        report["steps"].append({"name": "artifact", "status": "passed"})
        events_path = work_dir / "events.json"
        _run_step(
            "events-export",
            [
                str(python),
                "-m",
                "mediascribe",
                "events",
                "export",
                "--output",
                str(events_path),
            ],
            env=env,
            cwd=work_dir,
            log_dir=work_dir / "logs",
        )
        metrics_proc = _run_step(
            "events-metrics",
            [str(python), "-m", "mediascribe", "events", "metrics"],
            env=env,
            cwd=work_dir,
            log_dir=work_dir / "logs",
        )
        report["metrics"] = validate_events(
            json.loads(events_path.read_text(encoding="utf-8")),
            json.loads(metrics_proc.stdout),
        )
        report["steps"].append({"name": "events", "status": "passed"})
        report["status"] = "passed"
        report["code"] = None
        json_path, markdown_path = write_report(report, work_dir)
        print(
            json.dumps({"status": "passed", "json": str(json_path), "markdown": str(markdown_path)})
        )
        return 0
    except GateFailure as exc:
        report["status"] = "blocked" if exc.code == "RESOURCE_GUARD_BLOCKED" else "failed"
        report["code"] = exc.code
        report["error"] = str(exc)
        json_path, markdown_path = write_report(report, work_dir)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "code": exc.code,
                    "error": str(exc),
                    "json": str(json_path),
                    "markdown": str(markdown_path),
                },
                ensure_ascii=False,
            )
        )
        return 2 if report["status"] == "blocked" else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Offline clean-environment first-success gate")
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA") or "unknown")
    parser.add_argument("--min-available-mb", type=int, default=2048)
    return run_gate(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
