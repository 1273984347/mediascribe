from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gates" / "faster_whisper_resume_gate.py"
FIXTURE = ROOT / "tests" / "fixtures" / "golden" / "us016"


def _load_gate():
    spec = importlib.util.spec_from_file_location("mediascribe_faster_whisper_resume_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _segments():
    return [
        {"segment_index": 0, "start": 0.0, "end": 5.0, "text": "人工智能"},
        {"segment_index": 1, "start": 5.0, "end": 10.0, "text": "软件开发"},
        {"segment_index": 2, "start": 9.0, "end": 15.0, "text": "技术债需要评审"},
    ]


def _events():
    return [
        {"seq": 1, "type": "job_submitted", "attempt_id": "old"},
        {"seq": 2, "type": "attempt_started", "attempt_id": "old"},
        {"seq": 3, "type": "checkpoint_identity_set", "attempt_id": "old"},
        {"seq": 4, "type": "segment_committed", "attempt_id": "old"},
        {"seq": 5, "type": "segment_committed", "attempt_id": "old"},
        {"seq": 6, "type": "job_interrupted", "attempt_id": "old"},
        {"seq": 7, "type": "resume_requested", "attempt_id": "new"},
        {"seq": 8, "type": "attempt_started", "attempt_id": "new"},
        {"seq": 9, "type": "segment_committed", "attempt_id": "new"},
        {"seq": 10, "type": "attempt_finished", "attempt_id": "new"},
    ]


def test_validate_resume_result_accepts_complete_prefix_suffix_and_tail():
    gate = _load_gate()
    result = gate.validate_resume_result(
        prefix_segments=_segments()[:2],
        final_segments=_segments(),
        result_text="人工智能 软件开发 技术债需要评审",
        markdown="# Transcript\n\n## 转录内容\n\n人工智能 软件开发 技术债需要评审\n",
        duration_seconds=16.0,
        resume_start=9.0,
        old_attempt_id="old",
        new_attempt_id="new",
        events=_events(),
    )

    assert result == {
        "prefix_segments": 2,
        "final_segments": 3,
        "resume_start": 9.0,
        "final_segment_end": 15.0,
        "tail_coverage_seconds": 1.0,
        "attempt_changed": True,
        "event_sequence_valid": True,
    }


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"prefix_segments": _segments()[:1]}, "RESUME_PREFIX_TOO_SHORT"),
        (
            {
                "final_segments": [
                    {**_segments()[0], "text": "changed"},
                    *_segments()[1:],
                    {"segment_index": 3, "start": 15.0, "end": 16.0, "text": "完成"},
                ]
            },
            "RESUME_PREFIX_MISMATCH",
        ),
        ({"final_segments": _segments()[:2]}, "RESUME_SUFFIX_MISSING"),
        ({"duration_seconds": 25.0}, "RESUME_TAIL_SHORT"),
        ({"resume_start": 8.0}, "RESUME_START_MISMATCH"),
        ({"new_attempt_id": "old"}, "RESUME_ATTEMPT_UNCHANGED"),
        ({"events": _events()[:-1]}, "RESUME_EVENT_MISSING"),
    ],
)
def test_validate_resume_result_rejects_false_success(override, code):
    gate = _load_gate()
    payload = {
        "prefix_segments": _segments()[:2],
        "final_segments": _segments(),
        "result_text": "人工智能 软件开发 技术债需要评审",
        "markdown": "# Transcript\n\n## 转录内容\n\n人工智能 软件开发 技术债需要评审\n",
        "duration_seconds": 16.0,
        "resume_start": 9.0,
        "old_attempt_id": "old",
        "new_attempt_id": "new",
        "events": _events(),
    }
    payload.update(override)
    with pytest.raises(gate.GateFailure) as exc_info:
        gate.validate_resume_result(**payload)
    assert exc_info.value.code == code


def test_wait_for_segments_polls_real_sqlite_until_threshold(tmp_path: Path):
    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_store import JobStore

    gate = _load_gate()
    db = tmp_path / "jobs.sqlite3"
    store = JobStore(db)
    control = JobControl(store, ProgressRegistry())
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)

    def commit_two():
        time.sleep(0.05)
        for index in range(2):
            control.commit_segment(
                job.job_id,
                attempt_id=job.attempt_id,
                segment_index=index,
                start=float(index * 5),
                end=float(index * 5 + 5),
                text=f"segment {index}",
                engine="faster-whisper",
                model="small",
                decode_params_hash="params",
            )

    thread = threading.Thread(target=commit_two)
    thread.start()
    process = SimpleNamespace(poll=lambda: None)
    try:
        count = gate.wait_for_segments(db, job.job_id, 2, process, timeout_s=2.0)
    finally:
        thread.join(timeout=2)
    assert count == 2


def test_wait_for_segments_fails_when_worker_exits_before_threshold(tmp_path: Path):
    gate = _load_gate()
    db = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE job_segments (job_id TEXT, segment_index INTEGER)")
    process = SimpleNamespace(poll=lambda: 1)
    with pytest.raises(gate.GateFailure) as exc_info:
        gate.wait_for_segments(db, "missing", 2, process, timeout_s=0.2)
    assert exc_info.value.code == "WORKER_EXITED"


def test_recover_interrupted_job_marks_real_checkpoint_resumable(tmp_path: Path):
    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_store import JobStore

    gate = _load_gate()
    db = tmp_path / "jobs.sqlite3"
    store = JobStore(db)
    control = JobControl(store, ProgressRegistry())
    job = control.submit(
        "u",
        request={},
        selection={"engine": "faster-whisper", "model": "small"},
        out_path=tmp_path / "x.md",
    )
    control.start_attempt(job.job_id)
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=tmp_path / "audio.wav",
        audio_sha256="hash",
        decode_params_hash="params",
    )
    for index in range(2):
        control.commit_segment(
            job.job_id,
            attempt_id=job.attempt_id,
            segment_index=index,
            start=float(index * 5),
            end=float(index * 5 + 5),
            text=f"segment {index}",
            engine="faster-whisper",
            model="small",
            decode_params_hash="params",
        )

    snapshot = gate.recover_interrupted_job(db)
    assert snapshot["status"] == "interrupted"
    assert snapshot["resumable"] == 1
    assert snapshot["committed_segments"] == 2


def test_spawn_worker_writes_logs_to_file_instead_of_unread_pipe(tmp_path: Path, monkeypatch):
    gate = _load_gate()
    captured = {}

    class Process:
        returncode = None

        def poll(self):
            return None

    def popen(command, **kwargs):
        captured.update(kwargs)
        captured["command"] = command
        return Process()

    monkeypatch.setattr(gate.subprocess, "Popen", popen)
    command = gate.build_worker_command("original", FIXTURE, tmp_path)
    gate._spawn_worker(command, {})

    assert captured["stdout"] is not gate.subprocess.PIPE
    log_path = tmp_path / "original-worker.log"
    assert log_path.is_file()
    assert captured["stdout"].closed is True


def test_wait_for_json_samples_while_worker_is_running(tmp_path: Path):
    gate = _load_gate()
    output = tmp_path / "worker.json"
    ticks: list[object] = []
    process = SimpleNamespace(poll=lambda: None)

    def write_later():
        time.sleep(0.05)
        output.write_text('{"ok": true}', encoding="utf-8")

    thread = threading.Thread(target=write_later)
    thread.start()
    try:
        payload = gate._wait_for_json(
            output,
            process,
            timeout_s=1.0,
            on_tick=lambda current: ticks.append(current),
        )
    finally:
        thread.join(timeout=1)
    assert payload == {"ok": True}
    assert ticks


def test_stop_worker_escalates_to_kill_after_grace_period():
    gate = _load_gate()

    class Process:
        def __init__(self):
            self.terminated = False
            self.killed = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return None

        def kill(self):
            self.killed = True

    process = Process()
    gate.stop_worker(process, grace_s=0.0)
    assert process.terminated is True
    assert process.killed is True


def test_run_gate_rejects_nonempty_output_directory_without_spawning(tmp_path: Path, monkeypatch):
    gate = _load_gate()
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    (out_dir / "original-job.json").write_text('{"stale": true}', encoding="utf-8")
    monkeypatch.setattr(
        gate,
        "resource_status",
        lambda **_kwargs: {"status": "ready", "code": None, "available_mb": 4096},
    )

    def forbidden_spawn(*_args, **_kwargs):
        raise AssertionError("stale output must not start a worker")

    exit_code = gate.run_gate(FIXTURE, out_dir, spawn_worker=forbidden_spawn)
    report = json.loads((out_dir / "resume-report.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["error_code"] == "OUTPUT_DIR_NOT_EMPTY"
    assert report["worker_started"] is False


def test_blocked_resource_preflight_writes_report_without_spawning_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    gate = _load_gate()
    monkeypatch.setattr(
        gate,
        "resource_status",
        lambda **_kwargs: {
            "status": "blocked",
            "code": "RESOURCE_GUARD_BLOCKED",
            "available_mb": 1024,
            "min_available_mb": 2048,
        },
    )

    def forbidden_spawn(*_args, **_kwargs):
        raise AssertionError("worker must not start while resource guard is blocked")

    exit_code = gate.run_gate(
        FIXTURE,
        tmp_path / "run",
        spawn_worker=forbidden_spawn,
    )
    report = json.loads((tmp_path / "run" / "resume-report.json").read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["schema"] == "mediascribe-resume-gate/v1"
    assert report["status"] == "blocked"
    assert report["error_code"] == "RESOURCE_GUARD_BLOCKED"
    assert report["worker_started"] is False


def test_worker_environment_is_offline_and_isolated(tmp_path: Path):
    gate = _load_gate()
    model_cache = tmp_path / "model-cache"
    env = gate.worker_environment(tmp_path, model_cache)
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"
    assert env["PIP_NO_INDEX"] == "1"
    assert env["MEDIASCRIBE_LLM_ENABLED"] == "0"
    assert env["MEDIASCRIBE_INTERNAL_TEST"] == "1"
    assert env["MEDIASCRIBE_WORKSPACE"] == str(tmp_path / "workspace")
    assert env["HF_HUB_CACHE"] == str(model_cache)


def test_run_gate_controls_worker_lifecycle_with_real_sqlite(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from mediascribe.checkpoint import capability_for
    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_recovery import decode_params_hash
    from mediascribe.web.job_store import JobStore

    gate = _load_gate()
    monkeypatch.setattr(
        gate,
        "resource_status",
        lambda **_kwargs: {"status": "ready", "code": None, "available_mb": 4096},
    )
    monkeypatch.setattr(
        gate,
        "detect_hardware",
        lambda: SimpleNamespace(to_dict=lambda: {"python": "3.11", "ram_total_mb": 4096}),
    )
    run_dir = tmp_path / "run"
    workspace = run_dir / "workspace"
    audio = workspace / "audio.wav"
    output = workspace / "out" / "resume.md"
    decode_payload = {
        "engine": "faster-whisper",
        "model": "small",
        "device": "cpu",
        "language": "zh",
    }

    class Process:
        def __init__(self, returncode=None):
            self.returncode = returncode
            self.pid = 0
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    def spawn(command, _env):
        worker = command[command.index("--worker") + 1]
        if worker == "original":
            workspace.mkdir(parents=True, exist_ok=True)
            audio.write_bytes(b"audio")
            output.parent.mkdir(parents=True, exist_ok=True)
            store = JobStore(workspace / "jobs.sqlite3")
            control = JobControl(store, ProgressRegistry())
            job = control.submit(
                str(audio),
                request={"urls": [str(audio)]},
                selection={"engine": "faster-whisper", "model": "small"},
                out_path=output,
            )
            control.start_attempt(job.job_id)
            control.set_checkpoint_identity(
                job.job_id,
                audio_path=audio,
                audio_sha256=gate.sha256_file(audio),
                decode_params_hash=decode_params_hash(decode_payload),
                capability=capability_for("faster-whisper"),
                audio_duration=16.0,
            )
            for index, text in enumerate(("人工智能", "软件开发")):
                control.commit_segment(
                    job.job_id,
                    attempt_id=job.attempt_id,
                    segment_index=index,
                    start=float(index * 5),
                    end=float(index * 5 + 5),
                    text=text,
                    engine="faster-whisper",
                    model="small",
                    decode_params_hash=decode_params_hash(decode_payload),
                )
            gate._atomic_write_json(
                run_dir / "original-job.json",
                {"job_id": job.job_id, "old_attempt_id": job.attempt_id},
            )
            gate._atomic_write_json(
                run_dir / "interrupt-ready.json",
                {
                    "job_id": job.job_id,
                    "old_attempt_id": job.attempt_id,
                    "audio_path": str(audio),
                    "audio_sha256": gate.sha256_file(audio),
                    "audio_duration": 16.0,
                    "decode_payload": decode_payload,
                    "output_path": str(output),
                    "committed_segments": 2,
                },
            )
            store.close()
            return Process()

        ready = json.loads((run_dir / "interrupt-ready.json").read_text(encoding="utf-8"))
        store = JobStore(workspace / "jobs.sqlite3")
        control = JobControl(store, ProgressRegistry())
        plan = control.resume(ready["job_id"], decode_params=decode_payload)
        job = control.hydrate(ready["job_id"])
        control.start_attempt(job.job_id)
        control.commit_segment(
            ready["job_id"],
            attempt_id=job.attempt_id,
            segment_index=2,
            start=9.0,
            end=15.0,
            text="技术债需要评审",
            engine="faster-whisper",
            model="small",
            decode_params_hash=decode_params_hash(decode_payload),
        )
        output.write_text(
            "# Transcript\n\n## 转录内容\n\n人工智能 软件开发 技术债需要评审\n",
            encoding="utf-8",
        )
        control.finish(
            ready["job_id"],
            status="succeeded",
            result={"out_path": str(output)},
            engine="faster-whisper",
            model="small",
            out_path=output,
        )
        gate._atomic_write_json(
            run_dir / "resume-result.json",
            {
                "job_id": ready["job_id"],
                "old_attempt_id": ready["old_attempt_id"],
                "new_attempt_id": job.attempt_id,
                "resume_start": plan.resume_start,
                "result_text": "人工智能 软件开发 技术债需要评审",
                "transcript_path": str(output),
                "audio_duration": 16.0,
            },
        )
        store.close()
        return Process(0)

    exit_code = gate.run_gate(FIXTURE, run_dir, spawn_worker=spawn)
    report = json.loads((run_dir / "resume-report.json").read_text(encoding="utf-8"))
    assert exit_code == 0, report
    assert report["status"] == "passed"
    assert report["validation"]["prefix_segments"] == 2
    assert report["validation"]["final_segments"] == 3
    assert report["metrics"]["original_rtf"] >= 0
    assert report["metrics"]["resume_rtf"] >= 0
    assert report["hardware"]["python"]
    assert report["hardware"]["ram_total_mb"] > 0
    assert report["decode_payload"] == decode_payload
    assert report["model_cache"]["offline"] is True


def test_attempt_callbacks_commit_two_segments_before_signalling_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from mediascribe.checkpoint import capability_for
    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_recovery import decode_params_hash
    from mediascribe.web.job_store import JobStore

    gate = _load_gate()
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    ready = tmp_path / "interrupt-ready.json"
    store = JobStore(tmp_path / "jobs.sqlite3")
    control = JobControl(store, ProgressRegistry())
    job = control.submit(
        "u",
        request={},
        selection={"engine": "faster-whisper", "model": "small"},
        out_path=tmp_path / "resume.md",
    )
    control.start_attempt(job.job_id)
    payload = {"engine": "faster-whisper", "model": "small", "device": "cpu", "language": "zh"}
    callbacks = gate._AttemptCallbacks(
        control=control,
        job=job,
        engine="faster-whisper",
        model="small",
        interrupt_after=2,
        ready_path=ready,
    )

    class StopWait(Exception):
        pass

    class Event:
        def wait(self):
            raise StopWait

    monkeypatch.setattr("mediascribe.audio_utils.probe_duration_seconds", lambda _path: 16.0)
    monkeypatch.setattr(gate.threading, "Event", Event)
    callbacks.on_audio_ready(audio)
    callbacks.on_decode_params(payload)
    callbacks.on_segment({"text": "人工智能", "start": 0.0, "end": 5.0})
    with pytest.raises(StopWait):
        callbacks.on_segment({"text": "软件开发", "start": 5.0, "end": 10.0})

    evidence = json.loads(ready.read_text(encoding="utf-8"))
    assert evidence["committed_segments"] == 2
    assert evidence["decode_payload"] == payload
    assert store.get_snapshot(job.job_id)["committed_segments"] == 2
    assert capability_for("faster-whisper").mode == "segment"


def test_resume_worker_runs_pipeline_with_durable_prefix_and_commits_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from types import SimpleNamespace

    from mediascribe.checkpoint import CheckpointSession, capability_for
    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_recovery import decode_params_hash
    from mediascribe.web.job_store import JobStore

    gate = _load_gate()
    run_dir = tmp_path / "run"
    workspace = run_dir / "workspace"
    audio = workspace / "audio.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    output = workspace / "out" / "resume.md"
    output.parent.mkdir(parents=True)
    db = workspace / "jobs.sqlite3"
    payload = {"engine": "faster-whisper", "model": "small", "device": "cpu", "language": "zh"}
    params_hash = decode_params_hash(payload)
    store = JobStore(db)
    control = JobControl(store, ProgressRegistry())
    job = control.submit(
        str(audio),
        request={"urls": [str(audio)]},
        selection={"engine": "faster-whisper", "model": "small"},
        out_path=output,
    )
    control.start_attempt(job.job_id)
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=audio,
        audio_sha256=gate.sha256_file(audio),
        decode_params_hash=params_hash,
        capability=capability_for("faster-whisper"),
        audio_duration=16.0,
    )
    for index, text in enumerate(("人工智能", "软件开发")):
        control.commit_segment(
            job.job_id,
            attempt_id=job.attempt_id,
            segment_index=index,
            start=float(index * 5),
            end=float(index * 5 + 5),
            text=text,
            engine="faster-whisper",
            model="small",
            decode_params_hash=params_hash,
        )
    old_attempt = job.attempt_id
    store.close()
    gate.recover_interrupted_job(db, job.job_id)
    gate._atomic_write_json(
        run_dir / "interrupt-ready.json",
        {
            "job_id": job.job_id,
            "old_attempt_id": old_attempt,
            "audio_path": str(audio),
            "audio_sha256": gate.sha256_file(audio),
            "audio_duration": 16.0,
            "decode_payload": payload,
            "output_path": str(output),
            "committed_segments": 2,
        },
    )

    class FakePipeline:
        def transcribe(
            self,
            source,
            *,
            output,
            resume_audio_path,
            resume_start,
            checkpoint_cursor,
            prior_segments_cb,
            audio_path_cb,
            decode_params_cb,
            segment_cb,
            **_kwargs,
        ):
            assert source == str(audio)
            assert resume_audio_path == audio
            assert resume_start == 9.0
            assert checkpoint_cursor.previous_text == "软件开发"
            assert [segment["text"] for segment in prior_segments_cb()] == ["人工智能", "软件开发"]
            audio_path_cb(audio)
            decode_params_cb(payload)
            session = CheckpointSession(
                capability=capability_for("faster-whisper"),
                prior_segments=prior_segments_cb(),
                cursor=checkpoint_cursor,
                segment_cb=segment_cb,
            )
            session.accept({"text": "软件开发 技术债需要评审", "start": 9.0, "end": 15.0})
            text = " ".join(segment["text"] for segment in session.segments)
            Path(output).write_text(f"# Transcript\n\n## 转录内容\n\n{text}\n", encoding="utf-8")
            return SimpleNamespace(text=text, engine="faster-whisper", model="small")

    monkeypatch.setattr(gate, "_pipeline_for_acceptance", lambda *_args: FakePipeline())
    monkeypatch.setattr("mediascribe.audio_utils.probe_duration_seconds", lambda _path: 16.0)
    gate.run_resume_worker(run_dir, FIXTURE)

    result = json.loads((run_dir / "resume-result.json").read_text(encoding="utf-8"))
    final = JobStore(db).get_snapshot(job.job_id)
    assert result["new_attempt_id"] != old_attempt
    assert result["result_text"] == "人工智能 软件开发 技术债需要评审"
    assert final["status"] == "succeeded"
    assert final["committed_segments"] == 3
