"""Controlled real-ASR interruption and explicit resume gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_DIR = Path(__file__).resolve().parent
BENCH_DIR = REPO_ROOT / "scripts" / "bench" / "asr"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(GATE_DIR) not in sys.path:
    sys.path.insert(0, str(GATE_DIR))
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

from bench_asr import detect_hardware  # noqa: E402
from first_success_gate import (  # noqa: E402
    GateFailure as FirstSuccessGateFailure,
)
from first_success_gate import resource_status  # noqa: E402
from first_success_gate import verify_corpus as _verify_corpus

REPORT_SCHEMA = "mediascribe-resume-gate/v1"
_TAIL_TOLERANCE_SECONDS = 5.0


class GateFailure(RuntimeError):
    """A resume gate contract failed without producing valid evidence."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 fingerprint for an evidence file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_corpus(corpus_dir: Path) -> dict[str, Any]:
    """Reuse the release gate's fixed golden-corpus verification."""
    try:
        manifest: dict[str, Any] = _verify_corpus(Path(corpus_dir))
        return manifest
    except FirstSuccessGateFailure as exc:
        raise GateFailure(exc.code, str(exc)) from exc


def _atomic_write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def worker_environment(run_dir: Path, model_cache: Path | None = None) -> dict[str, str]:
    """Build an offline, isolated environment for original and resume workers."""
    run_dir = Path(run_dir).resolve()
    model_cache = (
        Path(model_cache).resolve()
        if model_cache is not None
        else Path.home() / ".cache/huggingface/hub"
    )
    home = run_dir / "home"
    workspace = run_dir / "workspace"
    app_data = run_dir / "app-data"
    for path in (home, workspace, app_data):
        path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": os.pathsep.join(
                part for part in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if part
            ),
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(app_data),
            "MEDIASCRIBE_WORKSPACE": str(workspace),
            "MEDIASCRIBE_APP_DATA": str(app_data),
            "MEDIASCRIBE_EVENT_DB": str(run_dir / "events.sqlite3"),
            "MEDIASCRIBE_EVENT_RETENTION_DAYS": "90",
            "MEDIASCRIBE_INTERNAL_TEST": "1",
            "MEDIASCRIBE_LLM_ENABLED": "0",
            "MEDIASCRIBE_E2E": "0",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PIP_NO_INDEX": "1",
            "HF_HOME": str(model_cache.parent),
            "HF_HUB_CACHE": str(model_cache),
        }
    )
    return env


def build_worker_command(
    worker: str,
    corpus_dir: Path,
    run_dir: Path,
    *,
    interrupt_after: int = 2,
) -> list[str]:
    """Build the fixed worker invocation used by the parent gate."""
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        worker,
        "--corpus",
        str(Path(corpus_dir).resolve()),
        "--run-dir",
        str(Path(run_dir).resolve()),
        "--interrupt-after",
        str(interrupt_after),
    ]


def _spawn_worker(command: list[str], env: dict[str, str]) -> subprocess.Popen:
    worker = command[command.index("--worker") + 1]
    run_dir = Path(command[command.index("--run-dir") + 1])
    log_path = run_dir / f"{worker}-worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        return subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )


def _segment_count(db_path: Path, job_id: str) -> int:
    try:
        with sqlite3.connect(f"{Path(db_path).as_uri()}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM job_segments WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return int(row[0])
    except (sqlite3.Error, OSError, TypeError):
        return 0


def wait_for_segments(
    db_path: Path,
    job_id: str,
    threshold: int,
    process: Any,
    timeout_s: float,
    *,
    on_tick: Callable[[Any], None] | None = None,
) -> int:
    """Poll durable SQLite until enough segments commit or the worker stops."""
    deadline = time.monotonic() + timeout_s
    while True:
        count = _segment_count(db_path, job_id)
        if count >= threshold:
            return count
        returncode = process.poll()
        if returncode is not None:
            raise GateFailure(
                "WORKER_EXITED",
                f"worker exited with code {returncode} after {count} committed segments",
            )
        if time.monotonic() >= deadline:
            raise GateFailure(
                "WORKER_TIMEOUT",
                f"worker did not commit {threshold} segments within {timeout_s}s",
            )
        if on_tick is not None:
            on_tick(process)
        time.sleep(0.1)


def _wait_for_json(
    path: Path,
    process: Any,
    timeout_s: float,
    *,
    on_tick: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while not Path(path).is_file():
        returncode = process.poll()
        if returncode is not None:
            raise GateFailure("WORKER_EXITED", f"worker exited before writing {path.name}")
        if time.monotonic() >= deadline:
            raise GateFailure("WORKER_TIMEOUT", f"worker did not write {path.name}")
        if on_tick is not None:
            on_tick(process)
        time.sleep(0.1)
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateFailure("WORKER_OUTPUT_INVALID", f"invalid worker output: {path.name}") from exc
    if not isinstance(payload, dict):
        raise GateFailure("WORKER_OUTPUT_INVALID", f"worker output is not an object: {path.name}")
    return payload


def stop_worker(process: Any, grace_s: float = 5.0) -> None:
    """Terminate a worker and escalate to kill when it does not exit in time."""
    if process.poll() is not None:
        return
    process.terminate()
    if grace_s <= 0:
        process.kill()
    else:
        try:
            process.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            process.kill()
        if process.poll() is None:
            process.kill()
    try:
        process.wait(timeout=5)
    except Exception:
        pass


def recover_interrupted_job(db_path: Path, job_id: str | None = None) -> dict[str, Any]:
    """Reopen durable state and mark unfinished work interrupted without auto-resume."""
    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_store import JobStore

    store = JobStore(Path(db_path))
    try:
        control = JobControl(store, ProgressRegistry())
        control.recover_interrupted()
        if job_id is not None:
            snapshot = store.get_snapshot(job_id)
            if snapshot is None:
                raise GateFailure("JOB_SNAPSHOT_MISSING", "durable job snapshot is missing")
            return cast(dict[str, Any], snapshot)
        for row in store.list():
            if row.get("status") == "interrupted":
                return cast(dict[str, Any], row)
        raise GateFailure("JOB_SNAPSHOT_MISSING", "no interrupted durable job was found")
    finally:
        store.close()


def _normalize_text(text: str) -> str:
    return " ".join(str(text).split())


def _artifact_body(markdown: str) -> str:
    for heading in ("## 转录内容", "## Transcript"):
        if heading in markdown:
            return markdown.split(heading, 1)[1].strip()
    return markdown.strip()


def validate_resume_result(
    *,
    prefix_segments: list[dict[str, Any]],
    final_segments: list[dict[str, Any]],
    result_text: str,
    markdown: str,
    duration_seconds: float,
    resume_start: float,
    old_attempt_id: str,
    new_attempt_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fail closed unless the resumed artifact preserves the durable prefix and tail."""
    if len(prefix_segments) < 2:
        raise GateFailure(
            "RESUME_PREFIX_TOO_SHORT", "fewer than two segments committed before interruption"
        )
    if len(final_segments) <= len(prefix_segments):
        raise GateFailure("RESUME_SUFFIX_MISSING", "resume produced no new segments")
    if old_attempt_id == new_attempt_id:
        raise GateFailure("RESUME_ATTEMPT_UNCHANGED", "resume reused the interrupted attempt id")

    prefix_fields = ("segment_index", "start", "end", "text")
    for index, prefix in enumerate(prefix_segments):
        final = final_segments[index]
        if any(final.get(field) != prefix.get(field) for field in prefix_fields):
            raise GateFailure("RESUME_PREFIX_MISMATCH", "durable prefix changed after resume")

    previous_end = 0.0
    for expected_index, segment in enumerate(final_segments):
        if segment.get("segment_index") != expected_index:
            raise GateFailure("RESUME_INDEX_INVALID", "final segment indexes are not contiguous")
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GateFailure("RESUME_TIME_INVALID", "final segment timestamp is invalid") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise GateFailure("RESUME_TIME_INVALID", "final segment timestamp is invalid")
        if end <= previous_end + 0.05:
            raise GateFailure("RESUME_TIME_INVALID", "final segment timeline does not advance")
        previous_end = end

    prefix_text = _normalize_text(" ".join(str(segment["text"]) for segment in prefix_segments))
    if not _normalize_text(result_text).startswith(prefix_text):
        raise GateFailure("RESUME_PREFIX_MISMATCH", "resumed result text lost the durable prefix")
    if not _normalize_text(_artifact_body(markdown)).startswith(prefix_text):
        raise GateFailure("RESUME_PREFIX_MISMATCH", "resumed Markdown lost the durable prefix")

    expected_start = max(0.0, float(prefix_segments[-1]["end"]) - 1.0)
    if abs(float(resume_start) - expected_start) > 0.05:
        raise GateFailure(
            "RESUME_START_MISMATCH", "resume start does not match the committed cursor"
        )
    tail_coverage = float(duration_seconds) - previous_end
    if tail_coverage < -0.05:
        raise GateFailure("RESUME_TIME_INVALID", "final segment exceeds the audio duration")
    if tail_coverage > _TAIL_TOLERANCE_SECONDS:
        raise GateFailure("RESUME_TAIL_SHORT", "final segment does not cover the audio tail")

    event_types = {event.get("type") for event in events}
    required = {
        "job_interrupted",
        "resume_requested",
        "attempt_started",
        "segment_committed",
        "attempt_finished",
    }
    if not required <= event_types:
        raise GateFailure("RESUME_EVENT_MISSING", "durable resume event sequence is incomplete")
    if not any(
        event.get("type") == "job_interrupted" and event.get("attempt_id") == old_attempt_id
        for event in events
    ):
        raise GateFailure("RESUME_EVENT_MISSING", "old attempt interruption event is missing")
    if not any(
        event.get("type") == "resume_requested" and event.get("attempt_id") == new_attempt_id
        for event in events
    ):
        raise GateFailure("RESUME_EVENT_MISSING", "new attempt resume event is missing")

    return {
        "prefix_segments": len(prefix_segments),
        "final_segments": len(final_segments),
        "resume_start": float(resume_start),
        "final_segment_end": previous_end,
        "tail_coverage_seconds": tail_coverage,
        "attempt_changed": True,
        "event_sequence_valid": True,
    }


def _load_acceptance(corpus_dir: Path) -> dict[str, Any]:
    acceptance_path = Path(corpus_dir) / "acceptance.json"
    try:
        acceptance: dict[str, Any] = json.loads(acceptance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateFailure(
            "CORPUS_ACCEPTANCE_MISSING", "acceptance.json is missing or invalid"
        ) from exc
    expected = {"engine": "faster-whisper", "model": "small", "device": "cpu", "language": "zh"}
    if any(acceptance.get(key) != value for key, value in expected.items()):
        raise GateFailure(
            "ACCEPTANCE_MISMATCH", "resume harness requires faster-whisper/small/cpu/zh"
        )
    return acceptance


def _store_segments(store: Any, job_id: str) -> list[dict[str, Any]]:
    segments = store.get_segments(job_id)
    return [
        {
            "segment_index": int(segment["segment_index"]),
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "text": segment["text"],
        }
        for segment in segments
    ]


class _AttemptCallbacks:
    def __init__(
        self,
        *,
        control: Any,
        job: Any,
        engine: str,
        model: str,
        interrupt_after: int | None,
        ready_path: Path | None,
    ) -> None:
        self.control = control
        self.job = job
        self.engine = engine
        self.model = model
        self.interrupt_after = interrupt_after
        self.ready_path = ready_path
        self.audio_path: Path | None = None
        self.audio_hash: str | None = None
        self.audio_duration: float | None = None
        self.decode_payload: dict[str, Any] | None = None
        self.decode_hash: str | None = None
        self.identity_set = False
        self.committed = 0

    def on_audio_ready(self, path: Path) -> None:
        from mediascribe.audio_utils import probe_duration_seconds
        from mediascribe.web.job_recovery import audio_sha256

        self.audio_path = Path(path)
        self.audio_hash = audio_sha256(self.audio_path)
        self.audio_duration = probe_duration_seconds(self.audio_path)
        self._maybe_set_identity()

    def on_decode_params(self, payload: dict[str, Any]) -> None:
        from mediascribe.web.job_recovery import decode_params_hash

        self.decode_payload = dict(payload)
        self.decode_hash = decode_params_hash(payload)
        self._maybe_set_identity()

    def _maybe_set_identity(self) -> None:
        if (
            self.identity_set
            or self.audio_path is None
            or self.audio_hash is None
            or self.decode_hash is None
        ):
            return
        if (
            self.audio_duration is None
            or not math.isfinite(self.audio_duration)
            or self.audio_duration <= 0
        ):
            raise GateFailure("AUDIO_DURATION_INVALID", "audio duration is unavailable")
        from mediascribe.checkpoint import capability_for

        self.control.set_checkpoint_identity(
            self.job.job_id,
            audio_path=self.audio_path,
            audio_sha256=self.audio_hash,
            decode_params_hash=self.decode_hash,
            capability=capability_for(self.engine),
            audio_duration=float(self.audio_duration),
        )
        self.identity_set = True

    def on_segment(self, segment: dict[str, Any]) -> None:
        if not self.identity_set:
            raise GateFailure(
                "CHECKPOINT_IDENTITY_MISSING", "segment arrived before checkpoint identity"
            )
        snapshot = self.control.snapshot(self.job.job_id)
        if snapshot is None:
            raise GateFailure("JOB_SNAPSHOT_MISSING", "job snapshot disappeared")
        self.control.commit_segment(
            self.job.job_id,
            attempt_id=self.job.attempt_id,
            segment_index=int(snapshot["last_segment_index"]) + 1,
            start=float(segment["start"]),
            end=float(segment["end"]),
            text=str(segment["text"]),
            engine=self.engine,
            model=self.model,
            decode_params_hash=self.decode_hash or "",
            words=segment.get("words"),
        )
        self.committed += 1
        if self.interrupt_after is not None and self.committed >= self.interrupt_after:
            if self.ready_path is not None:
                _atomic_write_json(
                    self.ready_path,
                    {
                        "job_id": self.job.job_id,
                        "old_attempt_id": self.job.attempt_id,
                        "audio_path": str(self.audio_path),
                        "audio_sha256": self.audio_hash,
                        "audio_duration": self.audio_duration,
                        "decode_payload": self.decode_payload,
                        "committed_segments": self.committed,
                    },
                )
            threading.Event().wait()


def _pipeline_for_acceptance(workspace: Path, acceptance: dict[str, Any]) -> Any:
    from mediascribe.config import Settings
    from mediascribe.pipeline import Pipeline

    settings = Settings(
        workspace_root=workspace,
        engine=acceptance["engine"],
        model=acceptance["model"],
        device=acceptance["device"],
        language=acceptance["language"],
        allow_model_download=False,
    )
    return Pipeline(settings)


def run_original_worker(run_dir: Path, corpus_dir: Path, interrupt_after: int) -> None:
    """Run real ASR until the requested durable segment count, then wait for parent kill."""
    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_store import JobStore

    acceptance = _load_acceptance(corpus_dir)
    run_dir = Path(run_dir)
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "jobs.sqlite3"
    audio = Path(corpus_dir) / "us014_zh_3min.wav"
    output = workspace / "out" / "resume.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    ready_path = run_dir / "interrupt-ready.json"
    store = JobStore(db_path)
    try:
        control = JobControl(store, ProgressRegistry())
        job = control.submit(
            str(audio),
            request={"urls": [str(audio)]},
            selection={
                "engine": acceptance["engine"],
                "model": acceptance["model"],
                "device": acceptance["device"],
                "language": acceptance["language"],
            },
            out_path=output,
        )
        control.start_attempt(job.job_id)
        _atomic_write_json(
            run_dir / "original-job.json",
            {"job_id": job.job_id, "old_attempt_id": job.attempt_id, "output_path": str(output)},
        )
        callbacks = _AttemptCallbacks(
            control=control,
            job=job,
            engine=acceptance["engine"],
            model=acceptance["model"],
            interrupt_after=interrupt_after,
            ready_path=ready_path,
        )
        _pipeline_for_acceptance(workspace, acceptance).transcribe(
            str(audio),
            output=output,
            language=acceptance["language"],
            audio_path_cb=callbacks.on_audio_ready,
            decode_params_cb=callbacks.on_decode_params,
            segment_cb=callbacks.on_segment,
        )
        raise GateFailure(
            "WORKER_COMPLETED_EARLY", "original worker completed before controlled interruption"
        )
    finally:
        store.close()


def run_resume_worker(run_dir: Path, corpus_dir: Path) -> None:
    """Reopen durable state, explicitly resume, and write a validated terminal artifact."""
    from mediascribe.checkpoint import CheckpointCursor
    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_store import JobStore

    acceptance = _load_acceptance(corpus_dir)
    run_dir = Path(run_dir)
    ready = json.loads((run_dir / "interrupt-ready.json").read_text(encoding="utf-8"))
    workspace = run_dir / "workspace"
    db_path = workspace / "jobs.sqlite3"
    audio = Path(ready["audio_path"])
    output = Path(ready.get("output_path") or workspace / "out" / "resume.md")
    store = JobStore(db_path)
    try:
        control = JobControl(store, ProgressRegistry())
        snapshot = control.snapshot(ready["job_id"])
        if snapshot is None:
            raise GateFailure(
                "JOB_SNAPSHOT_MISSING", "resume worker cannot find the interrupted job"
            )
        plan = control.resume(ready["job_id"], decode_params=ready["decode_payload"])
        job = control.hydrate(ready["job_id"])
        if job is None:
            raise GateFailure(
                "JOB_SNAPSHOT_MISSING", "resume worker cannot hydrate the interrupted job"
            )
        control.start_attempt(job.job_id)
        previous = store.get_segment(ready["job_id"], int(snapshot["last_segment_index"]))
        cursor = CheckpointCursor(
            mode="segment",
            resume_start=plan.resume_start,
            last_segment_end=float(snapshot["last_segment_end"]),
            previous_text=(previous or {}).get("text", ""),
            detected_language=plan.detected_language,
        )
        callbacks = _AttemptCallbacks(
            control=control,
            job=job,
            engine=acceptance["engine"],
            model=acceptance["model"],
            interrupt_after=None,
            ready_path=None,
        )

        def prior_segments() -> list[dict[str, Any]]:
            return _store_segments(store, ready["job_id"])

        result = _pipeline_for_acceptance(workspace, acceptance).transcribe(
            str(audio),
            output=output,
            language=acceptance["language"],
            resume_audio_path=plan.audio_path,
            resume_start=plan.resume_start,
            checkpoint_cursor=cursor,
            prior_segments_cb=prior_segments,
            audio_path_cb=callbacks.on_audio_ready,
            decode_params_cb=callbacks.on_decode_params,
            segment_cb=callbacks.on_segment,
        )
        markdown = output.read_text(encoding="utf-8")
        if not result.text.strip() or not markdown.strip():
            raise GateFailure("ARTIFACT_EMPTY", "resume produced an empty artifact")
        control.finish(
            ready["job_id"],
            status="succeeded",
            result={"out_path": str(output)},
            engine=result.engine,
            model=result.model,
            out_path=output,
        )
        _atomic_write_json(
            run_dir / "resume-result.json",
            {
                "job_id": ready["job_id"],
                "old_attempt_id": ready["old_attempt_id"],
                "new_attempt_id": job.attempt_id,
                "resume_start": plan.resume_start,
                "result_text": result.text,
                "transcript_path": str(output),
                "audio_duration": callbacks.audio_duration,
            },
        )
    finally:
        store.close()


def _gpu_used_mb() -> int:
    if shutil.which("nvidia-smi") is None:
        return 0
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode == 0:
            return int(proc.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return 0


def _process_rss_mb(process: Any) -> int:
    try:
        import psutil

        root = psutil.Process(process.pid)
        values = [root.memory_info().rss]
        for child in root.children(recursive=True):
            try:
                values.append(child.memory_info().rss)
            except Exception:
                pass
        return int(max(values) / 1024 / 1024)
    except Exception:
        return 0


def _events_for_job(store: Any, job_id: str) -> list[dict[str, Any]]:
    with sqlite3.connect(store._db_path) as conn:
        rows = conn.execute(
            "SELECT seq, type, attempt_id, payload_json FROM job_events "
            "WHERE job_id = ? ORDER BY seq",
            (job_id,),
        ).fetchall()
    return [
        {
            "seq": int(row[0]),
            "type": row[1],
            "attempt_id": row[2],
            "payload": json.loads(row[3]),
        }
        for row in rows
    ]


def run_gate(
    corpus_dir: Path,
    out_dir: Path,
    *,
    interrupt_after: int = 2,
    timeout_s: float = 1800.0,
    min_available_mb: int = 2048,
    model_cache: Path | None = None,
    spawn_worker: Callable[[list[str], dict[str, str]], Any] | None = None,
) -> int:
    """Run original ASR, controlled kill, durable recovery, and explicit resume."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "resume-report.json"
    spawn = spawn_worker or _spawn_worker
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "status": "running",
        "worker_started": False,
        "corpus_dir": str(Path(corpus_dir).resolve()),
    }
    original_process = None
    resume_process = None
    try:
        if any(out_dir.iterdir()):
            raise GateFailure("OUTPUT_DIR_NOT_EMPTY", "resume output directory must be empty")
        resource = resource_status(min_available_mb=min_available_mb)
        report["resource"] = resource
        if resource.get("status") == "blocked":
            report.update(
                {
                    "status": "blocked",
                    "error_code": resource.get("code"),
                    "worker_started": False,
                }
            )
            _atomic_write_json(report_path, report)
            return 2

        manifest = verify_corpus(corpus_dir)
        acceptance = _load_acceptance(corpus_dir)
        report["corpus_sha256"] = {
            "audio": manifest.get("audio_sha256"),
            "reference": manifest.get("reference_sha256"),
        }
        report["acceptance"] = acceptance
        report["hardware"] = detect_hardware().to_dict()
        env = worker_environment(out_dir, model_cache)
        report["model_cache"] = {
            "path": env["HF_HUB_CACHE"],
            "offline": True,
        }
        db_path = out_dir / "workspace" / "jobs.sqlite3"
        gpu_baseline = _gpu_used_mb()
        peaks = {
            "original_rss_mb": 0,
            "resume_rss_mb": 0,
            "original_vram_mb": 0,
            "resume_vram_mb": 0,
        }

        def sample(process: Any, prefix: str) -> None:
            peaks[f"{prefix}_rss_mb"] = max(peaks[f"{prefix}_rss_mb"], _process_rss_mb(process))
            peaks[f"{prefix}_vram_mb"] = max(
                peaks[f"{prefix}_vram_mb"], max(0, _gpu_used_mb() - gpu_baseline)
            )

        report["worker_started"] = True
        original_started = time.perf_counter()
        original_process = spawn(
            build_worker_command("original", corpus_dir, out_dir, interrupt_after=interrupt_after),
            env,
        )
        original_job = _wait_for_json(out_dir / "original-job.json", original_process, timeout_s)
        wait_for_segments(
            db_path,
            original_job["job_id"],
            interrupt_after,
            original_process,
            timeout_s,
            on_tick=lambda process: sample(process, "original"),
        )
        ready = _wait_for_json(out_dir / "interrupt-ready.json", original_process, timeout_s=10)
        sample(original_process, "original")
        stop_worker(original_process)
        report["original_wall_clock_s"] = round(time.perf_counter() - original_started, 3)

        interrupted = recover_interrupted_job(db_path, ready["job_id"])
        if interrupted.get("status") != "interrupted" or not interrupted.get("resumable"):
            raise GateFailure(
                "RESUME_NOT_RESUMABLE", "restart recovery did not preserve a resumable checkpoint"
            )
        report["interrupted_snapshot"] = interrupted
        report["decode_payload"] = ready["decode_payload"]
        store = None
        try:
            from mediascribe.web.job_store import JobStore

            store = JobStore(db_path)
            prefix_segments = _store_segments(store, ready["job_id"])
        finally:
            if store is not None:
                store.close()
        if len(prefix_segments) < interrupt_after:
            raise GateFailure("RESUME_PREFIX_TOO_SHORT", "durable prefix is shorter than requested")

        resume_started = time.perf_counter()
        resume_process = spawn(build_worker_command("resume", corpus_dir, out_dir), env)
        result = _wait_for_json(
            out_dir / "resume-result.json",
            resume_process,
            timeout_s,
            on_tick=lambda process: sample(process, "resume"),
        )
        while resume_process.poll() is None:
            sample(resume_process, "resume")
            time.sleep(0.1)
        if resume_process.returncode != 0:
            raise GateFailure(
                "WORKER_EXITED", f"resume worker exited with code {resume_process.returncode}"
            )
        report["resume_wall_clock_s"] = round(time.perf_counter() - resume_started, 3)

        store = JobStore(db_path)
        try:
            final_segments = _store_segments(store, ready["job_id"])
            final_snapshot = store.get_snapshot(ready["job_id"])
            events = _events_for_job(store, ready["job_id"])
            _atomic_write_json(out_dir / "job-events.json", events)
            _atomic_write_json(out_dir / "final-segments.json", final_segments)
            _atomic_write_json(out_dir / "final-snapshot.json", final_snapshot)
        finally:
            store.close()
        if final_snapshot is None or final_snapshot.get("status") != "succeeded":
            raise GateFailure(
                "RESUME_NOT_SUCCEEDED", "resume worker did not commit durable success"
            )
        markdown = Path(result["transcript_path"]).read_text(encoding="utf-8")
        validation = validate_resume_result(
            prefix_segments=prefix_segments,
            final_segments=final_segments,
            result_text=result["result_text"],
            markdown=markdown,
            duration_seconds=float(result["audio_duration"]),
            resume_start=float(result["resume_start"]),
            old_attempt_id=ready["old_attempt_id"],
            new_attempt_id=result["new_attempt_id"],
            events=events,
        )
        duration = float(result["audio_duration"])
        report.update(
            {
                "status": "passed",
                "error_code": None,
                "job_id": ready["job_id"],
                "old_attempt_id": ready["old_attempt_id"],
                "new_attempt_id": result["new_attempt_id"],
                "resume_start": result["resume_start"],
                "validation": validation,
                "metrics": {
                    "duration_seconds": duration,
                    "original_rtf": report["original_wall_clock_s"] / duration,
                    "resume_rtf": report["resume_wall_clock_s"] / duration,
                    **peaks,
                },
                "artifacts": {
                    "markdown": result["transcript_path"],
                    "events": str(out_dir / "job-events.json"),
                    "segments": str(out_dir / "final-segments.json"),
                    "snapshot": str(out_dir / "final-snapshot.json"),
                },
            }
        )
        _atomic_write_json(report_path, report)
        return 0
    except GateFailure as exc:
        report.update({"status": "failed", "error_code": exc.code, "error": str(exc)})
        _atomic_write_json(report_path, report)
        return 1
    except Exception as exc:
        report.update({"status": "failed", "error_code": "INTERNAL_ERROR", "error": str(exc)})
        _atomic_write_json(report_path, report)
        return 1
    finally:
        for process in (original_process, resume_process):
            if process is not None and process.poll() is None:
                stop_worker(process)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=REPO_ROOT / "tests/fixtures/golden/us016")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--worker", choices=("original", "resume"), default=None)
    parser.add_argument("--interrupt-after", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--min-available-mb", type=int, default=2048)
    parser.add_argument("--model-cache", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.interrupt_after < 2:
        parser.error("--interrupt-after must be at least 2")
    if args.worker is not None:
        if args.run_dir is None:
            parser.error("--run-dir is required with --worker")
        if args.worker == "original":
            run_original_worker(args.run_dir, args.corpus, args.interrupt_after)
        else:
            run_resume_worker(args.run_dir, args.corpus)
        return 0
    out_dir = args.out or REPO_ROOT / "output/faster-whisper-resume" / time.strftime(
        "%Y%m%d-%H%M%S"
    )
    return run_gate(
        args.corpus,
        out_dir,
        interrupt_after=args.interrupt_after,
        timeout_s=args.timeout,
        min_available_mb=args.min_available_mb,
        model_cache=args.model_cache,
    )


if __name__ == "__main__":
    raise SystemExit(main())
