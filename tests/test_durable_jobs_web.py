"""Integration contracts for durable Web job snapshots and restart recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

_SECRET_KEYS = ("runtime-secret", "hf_token", "wechat_cookies", "api_token")
_CHECKPOINT_INTERNALS = (
    "audio_path",
    "audio_sha256",
    "decode_params_hash",
    "words",
    "next_window_index",
)
_PUBLIC_CHECKPOINT_KEYS = {"committed_segments", "last_segment_index", "last_segment_end", "mode"}


class _ResumeIdentityMixin:
    def __init__(self):
        self.transcriber.effective_decode_params = self.effective_decode_params

    def effective_decode_params(self, *, language, initial_prompt):
        assert language == "zh"
        assert initial_prompt == self.effective_payload["initial_prompt"]
        return self.effective_payload


def _assert_public_job_contract(payload: dict, *, resume: bool = False) -> None:
    """Reject checkpoint internals outside the documented resume response."""
    forbidden = {
        key for key in _CHECKPOINT_INTERNALS if not (resume and key == "next_window_index")
    }
    if not resume:
        forbidden.add("checkpoint_mode")
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            assert not forbidden.intersection(value), (forbidden.intersection(value), value)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    checkpoint = payload.get("checkpoint")
    if checkpoint is not None:
        assert set(checkpoint) == _PUBLIC_CHECKPOINT_KEYS
    if resume:
        assert set(payload) == {
            "job_id",
            "attempt_id",
            "status",
            "resume_start",
            "next_segment_index",
            "checkpoint_mode",
            "next_window_index",
        }


def _assert_durable_json_has_no_secrets(db_path: Path, *, canaries: tuple[str, ...]) -> None:
    """Inspect every event payload and every JSON-valued snapshot column."""
    import sqlite3

    event_documents: list[tuple[str, str]] = []
    snapshot_documents: list[tuple[str, str]] = []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT payload_json FROM job_events ORDER BY seq").fetchall()
        assert rows, "secret assertion inspected no durable events"
        event_documents.extend(("job_events.payload_json", row[0]) for row in rows)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(jobs)")]
        json_columns = [column for column in columns if column.endswith("_json")]
        assert json_columns, "secret assertion found no snapshot JSON columns"
        for column in json_columns:
            values = conn.execute(f"SELECT {column} FROM jobs").fetchall()
            snapshot_documents.extend((f"jobs.{column}", row[0]) for row in values if row[0])
    assert snapshot_documents, "secret assertion inspected no snapshot JSON"
    for location, raw in (*event_documents, *snapshot_documents):
        lowered = raw.lower()
        for secret in (*_SECRET_KEYS, *canaries):
            assert secret.lower() not in lowered, (location, secret)


def test_secret_scan_rejects_event_only_durable_documents(tmp_path: Path):
    """Event payloads must not make a secret scan pass with zero snapshots."""
    import sqlite3

    import pytest

    db_path = tmp_path / "events-only.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE job_events (seq INTEGER, payload_json TEXT)")
        conn.execute(
            "CREATE TABLE jobs (request_json TEXT, selection_json TEXT, "
            "recovery_json TEXT, result_json TEXT)"
        )
        conn.executemany(
            "INSERT INTO job_events VALUES (?, ?)",
            [(seq, '{"status":"running"}') for seq in range(32)],
        )
        conn.execute("INSERT INTO jobs VALUES (NULL, '', NULL, '')")

    with pytest.raises(AssertionError, match="no snapshot JSON"):
        _assert_durable_json_has_no_secrets(db_path, canaries=("extra-secret",))


def _interrupted_window_job(
    app,
    workspace: Path,
    *,
    engine: str = "whisper",
    runtime_auth_required: bool = False,
    secret_request: bool = False,
):
    import hashlib

    from mediascribe.checkpoint import capability_for
    from mediascribe.models import SourceRef
    from mediascribe.pipeline_stages import TranscribeStage
    from mediascribe.web.job_recovery import decode_params_hash
    from mediascribe.zh_normalize import simplified_initial_prompt

    audio = workspace / "audio.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"audio")
    out_path = workspace / "out" / "resume.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    url = "https://example.com/resume"
    source = SourceRef(raw_input=url, kind="video", url=url)
    effective_prompt = simplified_initial_prompt(
        TranscribeStage.auto_prompt_for_source(source), "zh"
    )
    decode_payload = {
        "engine": engine,
        "model": "small",
        "device": "cpu",
        "language": "zh",
        "initial_prompt": effective_prompt,
        "vad_filter": False,
    }
    request = {"urls": [url], "engine": engine, "model": "small", "language": "zh"}
    if secret_request:
        request.update(
            {
                "runtime-secret": "runtime-secret-canary",
                "hf_token": "hf-token-canary",
                "wechat_cookies": "sessionid=wechat-cookie-canary",
                "api_token": "api-token-canary",
            }
        )
    params_hash = decode_params_hash(decode_payload)
    control = app.state.job_control
    job = control.submit(
        url,
        request=request,
        selection={"engine": engine, "model": "small", "device": "cpu", "language": "zh"},
        out_path=out_path,
    )
    control.start_attempt(job.job_id)
    capability = capability_for(engine)
    assert capability is not None and capability.mode == "window"
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=audio,
        audio_sha256=hashlib.sha256(b"audio").hexdigest(),
        decode_params_hash=params_hash,
        capability=capability,
        audio_duration=118.0,
        runtime_auth_required=runtime_auth_required,
    )
    control.commit_segment(
        job.job_id,
        attempt_id=job.attempt_id,
        segment_index=0,
        start=0.0,
        end=10.0,
        text="old prefix",
        engine=engine,
        model="small",
        decode_params_hash=params_hash,
        words=[{"word": "old", "start": 0.0, "end": 1.0}],
    )
    control.start_checkpoint_window(job.job_id, window_index=0, start=0.0, end=60.0)
    control.commit_checkpoint_window(
        job.job_id,
        window_index=0,
        start=0.0,
        end=60.0,
        detected_language="zh",
    )
    control.recover_interrupted()
    return job, audio, out_path, decode_payload, params_hash


def test_status_and_ws_use_persisted_snapshot_after_restart(tmp_path: Path):
    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app
    from mediascribe.web.job_store import JobStore

    workspace = tmp_path / "workspace"
    store = JobStore(workspace / "jobs.sqlite3")
    store.upsert("persisted", "https://example.test/a")
    store.mark_terminal("persisted", finished=True)
    store.close()

    app = create_app(workspace=workspace)
    with TestClient(app) as client:
        response = client.get("/api/jobs/persisted")
        assert response.status_code == 200
        assert response.json()["job_id"] == "persisted"
        with client.websocket_connect("/ws/progress/persisted") as ws:
            message = ws.receive_json()
            assert message["event"] == "snapshot"
            assert message["finished"] is True
        assert app.state.ws_bridges == {}


def test_restart_does_not_enqueue_resume_without_user_action(tmp_path: Path):
    from fastapi.testclient import TestClient

    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.app import create_app
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_store import JobStore

    workspace = tmp_path / "workspace"
    store = JobStore(workspace / "jobs.sqlite3")
    control = JobControl(store, ProgressRegistry())
    job = control.submit(
        "u", request={}, selection={"engine": "faster-whisper"}, out_path=workspace / "x.md"
    )
    control.start_attempt(job.job_id)
    attempt_id = job.attempt_id
    store.close()

    app = create_app(workspace=workspace)
    assert app.state.job_executor._threads == [] or all(
        not thread.is_alive() for thread in app.state.job_executor._threads
    )
    snapshot = app.state.job_control.snapshot(job.job_id)
    assert snapshot["status"] == "interrupted"
    assert snapshot["resumable"] == 0
    assert job.job_id not in app.state.async_pipelines
    assert snapshot["attempt_id"] == attempt_id


def test_purge_removes_expired_interrupted_events_and_segments(tmp_path: Path):
    from mediascribe.web.job_events import JobEvent
    from mediascribe.web.job_store import JobStore

    store = JobStore(tmp_path / "jobs.sqlite3")
    store.upsert("j1", "u")
    store.commit_event(
        JobEvent("e1", "j1", "a1", 1, "job_interrupted", {}, 1.0),
        snapshot={"status": "interrupted", "finished": 1},
    )
    store.commit_event(
        JobEvent("e2", "j1", "a1", 2, "segment_committed", {}, 1.0),
        segment={
            "job_id": "j1",
            "segment_index": 0,
            "attempt_id": "a1",
            "start": 0.0,
            "end": 1.0,
            "text": "x",
            "text_sha256": "x",
            "engine": "faster-whisper",
            "model": "small",
            "decode_params_hash": "p",
        },
    )
    assert store.purge_older_than(seconds=0) == 1
    assert store.get_snapshot("j1") is None
    assert store.get_segments("j1") == []
    assert store.next_event_seq("j1") == 0


def test_hydrated_status_decodes_json_and_normalizes_sqlite_booleans(tmp_path: Path):
    from fastapi.testclient import TestClient

    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.app import create_app
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_store import JobStore

    workspace = tmp_path / "workspace"
    store = JobStore(workspace / "jobs.sqlite3")
    control = JobControl(store, ProgressRegistry())
    job = control.submit("u", request={}, selection={}, out_path=workspace / "x.md")
    control.start_attempt(job.job_id)
    recovery = {"code": "MODEL_MISSING", "reason": "safe reason", "retryable": True}
    result = {"out_path": str(workspace / "x.md")}
    control.finish(
        job.job_id,
        status="failed",
        error="internal detail",
        error_code="MODEL_MISSING",
        recovery=recovery,
        result=result,
    )
    attempt_id = job.attempt_id
    store.close()

    app = create_app(workspace=workspace)
    with TestClient(app) as client:
        response = client.get(f"/api/jobs/{job.job_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["finished"] is True
        assert body["cancelled"] is False
        assert body["cancel_requested"] is False
        assert body["progress_known"] is False
        assert body["resumable"] is False
        assert body["recovery"] == recovery
        assert body["result"] == result
        assert body["attempt_id"] == attempt_id


def test_list_jobs_exposes_persisted_resume_and_checkpoint_fields(tmp_path: Path):
    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app
    from mediascribe.web.job_events import JobEvent
    from mediascribe.web.job_store import JobStore

    workspace = tmp_path / "workspace"
    store = JobStore(workspace / "jobs.sqlite3")
    store.upsert("persisted", "u")
    store.commit_event(
        JobEvent("e1", "persisted", "a1", 1, "job_interrupted", {"resumable": True}, 1.0),
        snapshot={
            "status": "interrupted",
            "attempt_id": "a1",
            "finished": 1,
            "resumable": 1,
            "audio_path": "audio/a.wav",
            "audio_sha256": "hash",
            "decode_params_hash": "params",
            "last_segment_index": 2,
            "last_segment_end": 3.5,
            "committed_segments": 3,
            "recovery_json": '{"code":"RESTART","reason":"safe reason"}',
        },
    )
    store.close()

    app = create_app(workspace=workspace)
    with TestClient(app) as client:
        jobs = client.get("/api/jobs").json()["jobs"]
    entry = next(job for job in jobs if job["job_id"] == "persisted")
    assert entry["status"] == "interrupted"
    assert entry["attempt_id"] == "a1"
    assert entry["resumable"] is True
    assert entry["resume_block_reason"] is None
    assert "audio_path" not in entry
    assert "audio_sha256" not in entry
    assert "decode_params_hash" not in entry
    assert entry["last_segment_index"] == 2
    assert entry["last_segment_end"] == 3.5
    assert entry["committed_segments"] == 3
    assert entry["checkpoint"]["mode"] is None
    assert entry["recovery"] == {"code": "RESTART", "reason": "safe reason"}


def test_store_job_result_does_not_publish_success_before_durable_finish(tmp_path: Path):
    import threading
    from types import SimpleNamespace

    import pytest

    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_runner import _store_job_result

    class FailingControl:
        def snapshot(self, _job_id):
            return {}

        def finish(self, *args, **kwargs):
            raise RuntimeError("durable finish failed")

    out_path = tmp_path / "result.md"
    out_path.write_text("# Transcript\n\nvalid\n", encoding="utf-8")
    result = SimpleNamespace(
        engine="faster-whisper",
        model="small",
        transcript_path=out_path,
        duration_seconds=1.0,
        source=SimpleNamespace(display_name="u"),
        metadata={"engine": "faster-whisper", "model": "small"},
    )
    registry = ProgressRegistry()
    job = registry.create("u")
    results = {}
    with pytest.raises(RuntimeError, match="durable finish failed"):
        _store_job_result(
            lambda: result,
            job.job_id,
            out_path,
            "u",
            results,
            threading.Lock(),
            registry,
            FailingControl(),
        )
    assert results == {}


def test_store_job_result_deletes_segments_only_after_durable_success(tmp_path: Path):
    import threading
    from types import SimpleNamespace

    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_runner import _store_job_result
    from mediascribe.web.job_store import JobStore

    store = JobStore(tmp_path / "jobs.sqlite3")
    registry = ProgressRegistry()
    control = JobControl(store, registry)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=tmp_path / "a.wav",
        audio_sha256="hash",
        decode_params_hash="params",
    )
    control.commit_segment(
        job.job_id,
        attempt_id=job.attempt_id,
        segment_index=0,
        start=0.0,
        end=1.0,
        text="hello",
        engine="faster-whisper",
        model="small",
        decode_params_hash="params",
    )
    out_path = tmp_path / "x.md"
    out_path.write_text("# Transcript\n\nhello\n", encoding="utf-8")
    result = SimpleNamespace(
        engine="faster-whisper",
        model="small",
        transcript_path=out_path,
        duration_seconds=1.0,
        source=SimpleNamespace(display_name="u"),
        metadata={"engine": "faster-whisper", "model": "small"},
    )
    results = {}
    _store_job_result(
        lambda: result,
        job.job_id,
        out_path,
        "u",
        results,
        threading.Lock(),
        registry,
        control=control,
    )
    assert store.get_latest_event_payload(job.job_id, "attempt_finished")["status"] == "succeeded"
    assert store.get_segments(job.job_id) == []
    assert results[job.job_id]["markdown"] == "# Transcript\n\nhello\n"


def test_store_job_result_deletes_segments_after_control_finish(tmp_path: Path):
    import threading
    from types import SimpleNamespace

    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_runner import _store_job_result
    from mediascribe.web.job_store import JobStore

    store = JobStore(tmp_path / "jobs.sqlite3")
    registry = ProgressRegistry()
    control = JobControl(store, registry)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    control.commit_segment(
        job.job_id,
        attempt_id=job.attempt_id,
        segment_index=0,
        start=0.0,
        end=1.0,
        text="hello",
        engine="faster-whisper",
        model="small",
        decode_params_hash="params",
    )
    out_path = tmp_path / "x.md"
    out_path.write_text("# Transcript\n\nhello\n", encoding="utf-8")
    result = SimpleNamespace(
        engine="faster-whisper",
        model="small",
        transcript_path=out_path,
        duration_seconds=1.0,
        source=SimpleNamespace(display_name="u"),
        metadata={"engine": "faster-whisper", "model": "small"},
    )
    _store_job_result(
        lambda: result,
        job.job_id,
        out_path,
        "u",
        {},
        threading.Lock(),
        registry,
        control=control,
    )
    assert store.get_snapshot(job.job_id)["status"] == "succeeded"
    assert store.get_latest_event_payload(job.job_id, "attempt_finished")["status"] == "succeeded"
    assert store.get_segments(job.job_id) == []


def test_store_job_result_keeps_segments_when_artifact_validation_fails(tmp_path: Path):
    import threading
    from types import SimpleNamespace

    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_runner import _store_job_result
    from mediascribe.web.job_store import JobStore

    store = JobStore(tmp_path / "jobs.sqlite3")
    registry = ProgressRegistry()
    control = JobControl(store, registry)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=tmp_path / "a.wav",
        audio_sha256="hash",
        decode_params_hash="params",
    )
    control.commit_segment(
        job.job_id,
        attempt_id=job.attempt_id,
        segment_index=0,
        start=0.0,
        end=1.0,
        text="hello",
        engine="faster-whisper",
        model="small",
        decode_params_hash="params",
    )
    out_path = tmp_path / "x.md"
    out_path.write_text("  \n", encoding="utf-8")
    result = SimpleNamespace(
        engine="faster-whisper",
        model="small",
        transcript_path=out_path,
        duration_seconds=1.0,
        source=SimpleNamespace(display_name="u"),
        metadata={"engine": "faster-whisper", "model": "small"},
    )
    _store_job_result(
        lambda: result,
        job.job_id,
        out_path,
        "u",
        {},
        threading.Lock(),
        registry,
        control=control,
    )
    assert store.get_latest_event_payload(job.job_id, "attempt_finished")["status"] == "failed"
    assert len(store.get_segments(job.job_id)) == 1


def test_web_runner_commits_segments_and_durable_terminal_state(tmp_path: Path):
    import sqlite3
    import time
    from types import SimpleNamespace
    from unittest import mock

    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app

    class SegmentPipeline:
        engine = "faster-whisper"
        model = "small"
        settings = SimpleNamespace(engine=engine, model=model, device="cpu", language="zh")
        transcriber = SimpleNamespace(vad_filter=False)

        def transcribe(
            self,
            url,
            *,
            output,
            progress_cb,
            segment_cb,
            audio_path_cb,
            decode_params_cb,
            **kwargs,
        ):
            audio = Path(output).with_suffix(".wav")
            audio.write_bytes(b"audio")
            audio_path_cb(audio)
            decode_params_cb(
                {
                    "engine": self.engine,
                    "model": self.model,
                    "device": "cpu",
                    "language": "zh",
                    "initial_prompt": None,
                    "vad_filter": False,
                }
            )
            segment_cb({"text": "one", "start": 0.0, "end": 1.0})
            segment_cb({"text": "two", "start": 1.0, "end": 2.0})
            progress_cb("transcribe", 100.0)
            Path(output).write_text("# Transcript\n\none two\n", encoding="utf-8")
            return SimpleNamespace(
                engine=self.engine,
                model=self.model,
                transcript_path=Path(output),
                duration_seconds=2.0,
                source=SimpleNamespace(display_name="u"),
                metadata={"engine": self.engine, "model": self.model},
            )

    workspace = tmp_path / "workspace"
    with (
        mock.patch("mediascribe.web.app._build_pipeline", return_value=SegmentPipeline()),
        mock.patch("mediascribe.audio_utils.probe_duration_seconds", return_value=2.0),
    ):
        app = create_app(workspace=workspace)
        with TestClient(app) as client:
            response = client.post(
                "/api/jobs", json={"urls": ["https://example.com/x"], "language": "zh"}
            )
            assert response.status_code == 200, response.text
            job_id = response.json()["jobs"][0]["job_id"]
            deadline = time.time() + 5.0
            while time.time() < deadline:
                snapshot = app.state.job_store.get_snapshot(job_id) or {}
                if snapshot.get("status") == "succeeded":
                    break
                time.sleep(0.05)
            assert snapshot["status"] == "succeeded"
            assert snapshot["committed_segments"] == 2
            assert snapshot["last_segment_index"] == 1
            assert snapshot["stage_current"] > 0
            assert snapshot["progress_known"] == 1
            app.state.job_executor.shutdown(wait=True, cancel_futures=True)

    with sqlite3.connect(workspace / "jobs.sqlite3") as conn:
        payloads = conn.execute(
            "SELECT payload_json FROM job_events WHERE job_id = ? AND type = 'segment_committed' "
            "ORDER BY seq",
            (job_id,),
        ).fetchall()
    assert len(payloads) == 2


def test_old_response_fields_remain_present(tmp_path: Path):
    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app

    app = create_app(workspace=tmp_path / "workspace")
    job = app.state.job_control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    app.state.job_control.start_attempt(job.job_id)
    with TestClient(app) as client:
        body = client.get(f"/api/jobs/{job.job_id}").json()
    for field in (
        "job_id",
        "attempt_id",
        "stage",
        "stage_current",
        "stage_total",
        "progress_known",
        "finished",
        "cancelled",
    ):
        assert field in body
    assert body["status"] == "running"
    assert body["resumable"] is False
    assert body["checkpoint"] == {
        "committed_segments": 0,
        "last_segment_index": -1,
        "last_segment_end": 0.0,
        "mode": None,
    }


def test_explicit_resume_preserves_job_and_continues_segment_indexes(tmp_path: Path):
    import sqlite3
    import time
    from types import SimpleNamespace
    from unittest import mock

    from fastapi.testclient import TestClient

    from mediascribe.checkpoint import CheckpointSession
    from mediascribe.models import SourceRef
    from mediascribe.pipeline_stages import TranscribeStage
    from mediascribe.web.app import create_app
    from mediascribe.web.job_recovery import decode_params_hash
    from mediascribe.zh_normalize import simplified_initial_prompt

    workspace = tmp_path / "workspace"
    audio = workspace / "audio.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    resume_url = "https://example.com/resume"
    effective_prompt = simplified_initial_prompt(
        TranscribeStage.auto_prompt_for_source(
            SourceRef(raw_input=resume_url, kind="video", url=resume_url)
        ),
        "zh",
    )
    decode_payload = {
        "engine": "faster-whisper",
        "model": "small",
        "device": "cpu",
        "language": "zh",
        "initial_prompt": effective_prompt,
        "vad_filter": False,
    }

    class ResumePipeline(_ResumeIdentityMixin):
        engine = "faster-whisper"
        model = "small"
        effective_payload = decode_payload
        settings = SimpleNamespace(engine=engine, model=model, device="cpu", language="zh")
        transcriber = SimpleNamespace(vad_filter=False)

        def transcribe(
            self,
            url,
            *,
            output,
            progress_cb,
            audio_path_cb,
            decode_params_cb,
            resume_start,
            resume_source,
            checkpoint_capability,
            checkpoint_cursor,
            prior_segments_cb,
            **kwargs,
        ):
            assert resume_start == 9.0
            assert resume_source.raw_input == "https://example.com/resume"
            assert resume_source.kind == "video"
            assert resume_source.url == "https://example.com/resume"
            checkpoint_session = CheckpointSession(
                capability=checkpoint_capability,
                prior_segments=prior_segments_cb(),
                cursor=checkpoint_cursor,
                segment_cb=kwargs["segment_cb"],
            )
            audio_path_cb(audio)
            decode_params_cb(decode_payload)
            checkpoint_session.accept({"text": "covered", "start": 8.0, "end": 9.5})
            checkpoint_session.accept({"text": "two next", "start": 9.0, "end": 12.0})
            checkpoint_session.accept({"text": "last", "start": 12.0, "end": 15.0})
            progress_cb("transcribe", 100.0)
            Path(output).write_text("# Transcript\n\none two next last\n", encoding="utf-8")
            return SimpleNamespace(
                engine=self.engine,
                model=self.model,
                transcript_path=Path(output),
                duration_seconds=15.0,
                source=SimpleNamespace(display_name="u"),
                metadata={"engine": self.engine, "model": self.model},
            )

    app = create_app(workspace=workspace)
    control = app.state.job_control
    out_path = workspace / "out" / "resume.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    job = control.submit(
        "https://example.com/resume",
        request={"urls": ["https://example.com/resume"], "language": "zh"},
        selection={"engine": "faster-whisper", "model": "small", "device": "cpu"},
        out_path=out_path,
    )
    control.start_attempt(job.job_id)
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=audio,
        audio_sha256=__import__("hashlib").sha256(b"audio").hexdigest(),
        decode_params_hash=decode_params_hash(decode_payload),
    )
    for index, text, end in ((0, "one", 5.0), (1, "two", 10.0)):
        control.commit_segment(
            job.job_id,
            attempt_id=job.attempt_id,
            segment_index=index,
            start=0.0 if index == 0 else 5.0,
            end=end,
            text=text,
            engine="faster-whisper",
            model="small",
            decode_params_hash=decode_params_hash(decode_payload),
        )
    original_attempt_id = job.attempt_id
    control.recover_interrupted()
    interrupted = control.snapshot(job.job_id)
    assert interrupted["status"] == "interrupted"
    assert interrupted["resumable"] == 1
    assert interrupted["attempt_id"] == original_attempt_id

    with (
        mock.patch("mediascribe.web.app._build_pipeline", return_value=ResumePipeline()),
        mock.patch("mediascribe.audio_utils.probe_duration_seconds", return_value=15.0),
        mock.patch(
            "mediascribe.web.route_jobs._validate_submitted_urls",
            side_effect=AssertionError("resume must not revalidate the source"),
        ),
        mock.patch(
            "mediascribe.inputs.resolve_short_url",
            side_effect=AssertionError("resume must not resolve short links"),
        ),
        mock.patch(
            "mediascribe.inputs.parse_source",
            side_effect=AssertionError("resume must not parse the source online"),
        ),
        mock.patch(
            "socket.getaddrinfo",
            side_effect=AssertionError("resume must not perform DNS resolution"),
        ),
        mock.patch(
            "mediascribe.pipeline.preflight_source",
            side_effect=AssertionError("resume must not preflight the source"),
        ),
    ):
        with TestClient(app) as client:
            response = client.post(f"/api/jobs/{job.job_id}/resume")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["job_id"] == job.job_id
            assert body["attempt_id"] != original_attempt_id
            assert body["status"] == "queued"
            assert body["resume_start"] == 9.0
            assert body["next_segment_index"] == 2
            deadline = time.time() + 5.0
            while time.time() < deadline:
                snapshot = app.state.job_store.get_snapshot(job.job_id) or {}
                if snapshot.get("status") == "succeeded":
                    break
                time.sleep(0.05)
            assert snapshot["status"] == "succeeded"
            assert snapshot["committed_segments"] == 4
            app.state.job_executor.shutdown(wait=True, cancel_futures=True)

    with sqlite3.connect(workspace / "jobs.sqlite3") as conn:
        rows = conn.execute(
            "SELECT payload_json, attempt_id FROM job_events WHERE job_id = ? "
            "AND type = 'segment_committed' ORDER BY seq",
            (job.job_id,),
        ).fetchall()
    assert [__import__("json").loads(payload)["text"] for payload, _ in rows] == [
        "one",
        "two",
        "next",
        "last",
    ]
    assert [attempt for _, attempt in rows] == [
        original_attempt_id,
        original_attempt_id,
        body["attempt_id"],
        body["attempt_id"],
    ]


def test_window_engine_resume_uses_durable_cursor_and_prefix(tmp_path: Path):
    import os
    import time
    from types import SimpleNamespace
    from unittest import mock

    from fastapi.testclient import TestClient

    from mediascribe.checkpoint import CheckpointCursor, CheckpointSession, capability_for
    from mediascribe.web.app import create_app

    class WindowResumePipeline(_ResumeIdentityMixin):
        engine = "whisper"
        model = "small"
        settings = SimpleNamespace(engine=engine, model=model, device="cpu", language="zh")
        transcriber = SimpleNamespace(
            vad_filter=False,
            diarization=False,
            hf_token="hf-token-canary",
            runtime_secret="runtime-secret-canary",
            wechat_cookies="sessionid=wechat-cookie-canary",
            api_token="api-token-canary",
        )

        def checkpoint_capability(self):
            return capability_for(self.engine)

        def transcribe(
            self,
            url,
            *,
            output,
            audio_path_cb,
            decode_params_cb,
            checkpoint_capability,
            checkpoint_cursor,
            prior_segments_cb,
            checkpoint_window_started_cb,
            checkpoint_window_committed_cb,
            **kwargs,
        ):
            assert isinstance(checkpoint_cursor, CheckpointCursor)
            assert checkpoint_cursor.mode == "window"
            assert checkpoint_cursor.resume_start == 58.0
            assert checkpoint_cursor.last_segment_end == 10.0
            assert checkpoint_cursor.previous_text == "old prefix"
            assert checkpoint_cursor.next_window_index == 1
            assert checkpoint_cursor.detected_language == "zh"
            assert prior_segments_cb() == [
                {
                    "text": "old prefix",
                    "start": 0.0,
                    "end": 10.0,
                    "words": [{"word": "old", "start": 0.0, "end": 1.0}],
                }
            ]
            checkpoint_session = CheckpointSession(
                capability=checkpoint_capability,
                prior_segments=prior_segments_cb(),
                cursor=checkpoint_cursor,
                segment_cb=kwargs["segment_cb"],
                window_started_cb=checkpoint_window_started_cb,
                window_committed_cb=checkpoint_window_committed_cb,
            )
            audio_path_cb(audio)
            decode_params_cb(decode_payload)
            checkpoint_session.start_window(1, 58.0, 118.0)
            checkpoint_session.accept({"text": "new suffix", "start": 60.0, "end": 62.0})
            checkpoint_session.commit_window(1, 58.0, 118.0, "zh")
            text = " ".join(segment["text"] for segment in checkpoint_session.segments)
            Path(output).write_text(f"# Transcript\n\n{text}\n", encoding="utf-8")
            return SimpleNamespace(
                engine=self.engine,
                model=self.model,
                transcript_path=Path(output),
                duration_seconds=118.0,
                source=SimpleNamespace(display_name="u"),
                metadata={"engine": self.engine, "model": self.model},
            )

    workspace = tmp_path / "workspace"
    api_token = "api-token-canary"
    auth_headers = {"Authorization": f"Bearer {api_token}"}
    canaries = (
        "runtime-secret-canary",
        "hf-token-canary",
        "wechat-cookie-canary",
        api_token,
    )
    with mock.patch.dict(os.environ, {"MEDIASCRIBE_API_TOKEN": api_token}):
        app = create_app(workspace=workspace)
        job, audio, _, decode_payload, _ = _interrupted_window_job(
            app, workspace, secret_request=True
        )
        WindowResumePipeline.effective_payload = decode_payload
        interrupted = app.state.job_control.snapshot(job.job_id)
        assert interrupted["checkpoint_cursor"] == 60.0
        assert interrupted["last_segment_end"] == 10.0

        with (
            mock.patch("mediascribe.web.app._build_pipeline", return_value=WindowResumePipeline()),
            mock.patch("mediascribe.audio_utils.probe_duration_seconds", return_value=118.0),
            TestClient(app, headers=auth_headers) as client,
        ):
            with client.websocket_connect(
                f"/ws/progress/{job.job_id}", subprotocols=[api_token]
            ) as ws:
                interrupted_snapshot = ws.receive_json()
            assert interrupted_snapshot["event"] == "snapshot"
            _assert_public_job_contract(interrupted_snapshot)
            status_before = client.get(f"/api/jobs/{job.job_id}")
            assert status_before.status_code == 200
            _assert_public_job_contract(status_before.json())
            for secret in (*_SECRET_KEYS, *canaries):
                assert secret.lower() not in status_before.text.lower()

            response = client.post(f"/api/jobs/{job.job_id}/resume")
            assert response.status_code == 200, response.text
            body = response.json()
            _assert_public_job_contract(body, resume=True)
            assert body["job_id"] == job.job_id
            assert body["attempt_id"] != interrupted["attempt_id"]
            assert body["status"] == "queued"
            assert body["checkpoint_mode"] == "window"
            assert body["next_window_index"] == 1
            assert body["resume_start"] == 58.0
            assert body["next_segment_index"] == 1
            for secret in (*_SECRET_KEYS, *canaries):
                assert secret.lower() not in response.text.lower()
            with client.websocket_connect(
                f"/ws/progress/{job.job_id}", subprotocols=[api_token]
            ) as ws:
                resumed_snapshot = ws.receive_json()
            _assert_public_job_contract(resumed_snapshot)
            deadline = time.time() + 5.0
            while time.time() < deadline:
                snapshot = app.state.job_store.get_snapshot(job.job_id) or {}
                if snapshot.get("status") in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(0.05)
            assert snapshot["status"] == "succeeded", snapshot
            assert snapshot["checkpoint_cursor"] == 118.0
            assert snapshot["last_segment_end"] == 62.0
            result = client.get(f"/api/jobs/{job.job_id}/result")
            assert result.status_code == 200
            _assert_public_job_contract(result.json())
            listed = client.get("/api/jobs")
            assert listed.status_code == 200
            listed_job = next(
                item for item in listed.json()["jobs"] if item["job_id"] == job.job_id
            )
            _assert_public_job_contract(listed_job)
            assert listed_job["checkpoint"]["mode"] == "window"
            assert "# Transcript" in result.json()["markdown"]
            assert "old prefix" in result.json()["markdown"]
            assert "new suffix" in result.json()["markdown"]
            app.state.job_executor.shutdown(wait=True, cancel_futures=True)

    _assert_durable_json_has_no_secrets(workspace / "jobs.sqlite3", canaries=canaries)


def test_silent_window_commits_advance_durable_transcribe_progress(tmp_path: Path):
    import time
    from types import SimpleNamespace
    from unittest import mock

    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app

    workspace = tmp_path / "workspace"
    app = create_app(workspace=workspace)
    control = app.state.job_control
    observed_progress = []
    decode_payload = {
        "engine": "whisper",
        "model": "small",
        "device": "cpu",
        "language": "zh",
        "initial_prompt": None,
        "vad_filter": False,
    }

    class SilentWindowPipeline:
        engine = "whisper"
        model = "small"
        settings = SimpleNamespace(engine=engine, model=model, device="cpu", language="zh")
        transcriber = SimpleNamespace(vad_filter=False)

        def transcribe(
            self,
            url,
            *,
            output,
            audio_path_cb,
            decode_params_cb,
            progress_cb,
            checkpoint_window_started_cb,
            checkpoint_window_committed_cb,
            **kwargs,
        ):
            job_id = next(row["job_id"] for row in control.store.list() if row["url"] == url)
            audio = Path(output).with_suffix(".wav")
            audio.write_bytes(b"audio")
            audio_path_cb(audio)
            decode_params_cb(decode_payload)
            for index, (start, end) in enumerate(((0.0, 60.0), (58.0, 118.0), (116.0, 123.0))):
                checkpoint_window_started_cb(index, start, end)
                if index == 1:
                    progress_cb("transcribe", 1.0)
                    observed_progress.append(control.store.get_snapshot(job_id)["stage_current"])
                checkpoint_window_committed_cb(index, start, end, "zh")
                snapshot = control.store.get_snapshot(job_id)
                observed_progress.append(snapshot["stage_current"])
            Path(output).write_text("# Transcript\n\nNo speech detected.\n", encoding="utf-8")
            return SimpleNamespace(
                engine=self.engine,
                model=self.model,
                transcript_path=Path(output),
                duration_seconds=123.0,
                source=SimpleNamespace(display_name="u"),
                metadata={"engine": self.engine, "model": self.model},
            )

    with (
        mock.patch("mediascribe.web.app._build_pipeline", return_value=SilentWindowPipeline()),
        mock.patch("mediascribe.audio_utils.probe_duration_seconds", return_value=123.0),
        TestClient(app) as client,
    ):
        response = client.post(
            "/api/jobs", json={"urls": ["https://example.com/silent"], "language": "zh"}
        )
        assert response.status_code == 200, response.text
        current_job_id = response.json()["jobs"][0]["job_id"]
        deadline = time.time() + 5.0
        while time.time() < deadline:
            snapshot = control.store.get_snapshot(current_job_id) or {}
            if snapshot.get("status") in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert snapshot["status"] == "succeeded", snapshot
        assert snapshot["committed_segments"] == 0
        assert snapshot["checkpoint_cursor"] == 123.0
        app.state.job_executor.shutdown(wait=True, cancel_futures=True)

    assert observed_progress == [45, 45, 95, 99]


def test_resume_runtime_auth_preflight_rejects_missing_token_before_attempt(tmp_path: Path):
    from types import SimpleNamespace
    from unittest import mock

    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app

    workspace = tmp_path / "workspace"
    app = create_app(workspace=workspace)
    job, _, _, _, _ = _interrupted_window_job(
        app, workspace, engine="whisperx", runtime_auth_required=True
    )
    before = app.state.job_control.snapshot(job.job_id)
    pipeline = SimpleNamespace(
        settings=SimpleNamespace(engine="whisperx", model="small", device="cpu", language="zh"),
        transcriber=SimpleNamespace(vad_filter=False, diarization=True, hf_token=None),
    )
    with (
        mock.patch("mediascribe.web.app._build_pipeline", return_value=pipeline),
        TestClient(app) as client,
    ):
        response = client.post(f"/api/jobs/{job.job_id}/resume")
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "error_code": "RESUME_AUTH_REQUIRED",
        "resume_block_reason": "RESUME_AUTH_REQUIRED",
    }
    after = app.state.job_control.snapshot(job.job_id)
    assert after["status"] == "interrupted"
    assert after["attempt_id"] == before["attempt_id"]
    assert after["resume_count"] == before["resume_count"]
    assert app.state.job_store.get_latest_event_payload(job.job_id, "resume_requested") is None


def test_tokenless_diarization_resume_does_not_require_runtime_auth(tmp_path: Path):
    import time
    from types import SimpleNamespace
    from unittest import mock

    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app

    workspace = tmp_path / "workspace"
    app = create_app(workspace=workspace)
    job, audio, _, decode_payload, _ = _interrupted_window_job(
        app, workspace, engine="whisperx", runtime_auth_required=False
    )
    observed_identity = []

    class TokenlessDiarizationPipeline(_ResumeIdentityMixin):
        engine = "whisperx"
        model = "small"
        effective_payload = decode_payload
        settings = SimpleNamespace(engine=engine, model=model, device="cpu", language="zh")
        transcriber = SimpleNamespace(vad_filter=False, diarization=True, hf_token=None)

        def transcribe(
            self,
            url,
            *,
            output,
            audio_path_cb,
            decode_params_cb,
            **kwargs,
        ):
            audio_path_cb(audio)
            decode_params_cb(decode_payload)
            observed_identity.append(app.state.job_store.get_snapshot(job.job_id))
            Path(output).write_text("# Transcript\n\nold prefix\n", encoding="utf-8")
            return SimpleNamespace(
                engine=self.engine,
                model=self.model,
                transcript_path=Path(output),
                duration_seconds=118.0,
                source=SimpleNamespace(display_name="u"),
                metadata={"engine": self.engine, "model": self.model},
            )

    with (
        mock.patch(
            "mediascribe.web.app._build_pipeline", return_value=TokenlessDiarizationPipeline()
        ),
        mock.patch("mediascribe.audio_utils.probe_duration_seconds", return_value=118.0),
        TestClient(app) as client,
    ):
        response = client.post(f"/api/jobs/{job.job_id}/resume")
        assert response.status_code == 200, response.text
        assert "hf_token" not in response.text
        deadline = time.time() + 5.0
        while time.time() < deadline:
            snapshot = app.state.job_store.get_snapshot(job.job_id) or {}
            if snapshot.get("status") in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert snapshot["status"] == "succeeded", snapshot
        app.state.job_executor.shutdown(wait=True, cancel_futures=True)

    assert observed_identity[0]["runtime_auth_required"] == 0
    assert "hf_token" not in snapshot


def test_web_runner_rejects_segment_when_audio_duration_is_unavailable(tmp_path: Path):
    import time
    from types import SimpleNamespace
    from unittest import mock

    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app

    class MissingDurationPipeline:
        engine = "whisper"
        model = "small"
        settings = SimpleNamespace(engine=engine, model=model, device="cpu", language="zh")
        transcriber = SimpleNamespace(vad_filter=False)

        def transcribe(
            self,
            url,
            *,
            output,
            audio_path_cb,
            decode_params_cb,
            segment_cb,
            **kwargs,
        ):
            audio = Path(output).with_suffix(".wav")
            audio.write_bytes(b"audio")
            audio_path_cb(audio)
            decode_params_cb(
                {
                    "engine": self.engine,
                    "model": self.model,
                    "device": "cpu",
                    "language": "zh",
                    "initial_prompt": None,
                    "vad_filter": False,
                }
            )
            segment_cb({"text": "must not commit", "start": 0.0, "end": 1.0})
            Path(output).write_text("# Transcript\n\nmust not commit\n", encoding="utf-8")
            return SimpleNamespace(
                engine=self.engine,
                model=self.model,
                transcript_path=Path(output),
                duration_seconds=1.0,
                source=SimpleNamespace(display_name="u"),
                metadata={"engine": self.engine, "model": self.model},
            )

    workspace = tmp_path / "workspace"
    with (
        mock.patch("mediascribe.web.app._build_pipeline", return_value=MissingDurationPipeline()),
        mock.patch("mediascribe.audio_utils.probe_duration_seconds", return_value=None),
        TestClient(create_app(workspace=workspace)) as client,
    ):
        response = client.post(
            "/api/jobs", json={"urls": ["https://example.com/x"], "engine": "whisper"}
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["jobs"][0]["job_id"]
        deadline = time.time() + 5.0
        while time.time() < deadline:
            snapshot = client.app.state.job_store.get_snapshot(job_id) or {}
            if snapshot.get("status") in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert snapshot["status"] == "failed", snapshot
        assert snapshot["committed_segments"] == 0
        client.app.state.job_executor.shutdown(wait=True, cancel_futures=True)


def test_rest_cancel_terminal_job_returns_noop_snapshot(tmp_path: Path):
    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app

    app = create_app(workspace=tmp_path / "workspace")
    control = app.state.job_control
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    control.finish(job.job_id, status="succeeded")

    with TestClient(app) as client:
        response = client.post(f"/api/jobs/{job.job_id}/cancel")
        assert response.status_code == 200
        assert response.json() == {
            "job_id": job.job_id,
            "accepted": False,
            "status": "succeeded",
            "finished": True,
            "cancelled": False,
        }


def test_resume_source_ref_builds_bilibili_identity_offline():
    from mediascribe.web.route_jobs import _resume_source_ref

    source = _resume_source_ref("BV1234567890")

    assert source.kind == "bilibili"
    assert source.bv == "BV1234567890"
    assert source.url == "https://www.bilibili.com/video/BV1234567890"
    assert source.path is None


@pytest.mark.parametrize(
    "override",
    [
        {"language": "en"},
        {"initial_prompt": "changed automatic prompt"},
        {"engine": "whisperx"},
        {"model": "large-v3"},
        {"device": "cuda"},
        {"package_version": "different-runtime"},
    ],
)
def test_resume_decode_mismatch_returns_409_without_mutating_attempt(
    tmp_path: Path, override: dict[str, object]
):
    from types import SimpleNamespace
    from unittest import mock

    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app

    workspace = tmp_path / "workspace"
    app = create_app(workspace=workspace)
    job, _, _, decode_payload, _ = _interrupted_window_job(app, workspace)
    before = app.state.job_control.snapshot(job.job_id)
    events_before = app.state.job_store.next_event_seq(job.job_id)
    pipeline = SimpleNamespace(
        settings=SimpleNamespace(engine="whisper", model="small", device="cpu", language="zh"),
        transcriber=SimpleNamespace(vad_filter=False),
        decode_payload={**decode_payload, **override},
    )
    pipeline.transcriber.effective_decode_params = lambda **_kwargs: pipeline.decode_payload

    with (
        mock.patch("mediascribe.web.app._build_pipeline", return_value=pipeline),
        TestClient(app) as client,
    ):
        response = client.post(f"/api/jobs/{job.job_id}/resume")

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error_code"] == "RESUME_INPUT_MISMATCH"
    assert app.state.job_control.snapshot(job.job_id) == before
    assert app.state.job_store.next_event_seq(job.job_id) == events_before
    assert app.state.job_store.get_latest_event_payload(job.job_id, "resume_requested") is None


def test_runner_terminalizes_cancelled_job_when_worker_raises_plain_error(tmp_path: Path):
    import threading

    from mediascribe.progress import ProgressRegistry
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_runner import _store_job_result
    from mediascribe.web.job_store import JobStore

    store = JobStore(tmp_path / "jobs.sqlite3")
    registry = ProgressRegistry()
    control = JobControl(store, registry)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    control.cancel(job.job_id)

    def fail():
        raise RuntimeError("worker failed after cancellation")

    _store_job_result(
        fail,
        job.job_id,
        tmp_path / "x.md",
        "u",
        {},
        threading.Lock(),
        registry,
        control,
    )

    snapshot = store.get_snapshot(job.job_id)
    assert snapshot["status"] == "cancelled"
    assert snapshot["finished"] == 1
    assert snapshot["cancelled"] == 1
    assert store.get_latest_event_payload(job.job_id, "attempt_finished")["status"] == "cancelled"


def test_api_cancel_between_windows_has_durable_cancelled_terminal_event(tmp_path: Path):
    import threading
    import time
    from types import SimpleNamespace
    from unittest import mock

    from fastapi.testclient import TestClient

    from mediascribe.checkpoint import CheckpointSession, capability_for
    from mediascribe.web.app import create_app

    workspace = tmp_path / "workspace"
    first_window_committed = threading.Event()
    release_worker = threading.Event()

    class CancelWindowPipeline:
        engine = "whisper"
        model = "small"
        settings = SimpleNamespace(engine=engine, model=model, device="cpu", language="zh")
        transcriber = SimpleNamespace(vad_filter=False)

        def checkpoint_capability(self):
            return capability_for(self.engine)

        def transcribe(self, url, *, output, audio_path_cb, decode_params_cb, **kwargs):
            audio = Path(output).with_suffix(".wav")
            audio.write_bytes(b"audio")
            audio_path_cb(audio)
            decode_params_cb(
                {"engine": self.engine, "model": self.model, "device": "cpu", "language": "zh"}
            )
            session = CheckpointSession(
                capability=capability_for(self.engine),
                prior_segments=[],
                cursor=None,
                segment_cb=kwargs["segment_cb"],
                window_started_cb=kwargs["checkpoint_window_started_cb"],
                window_committed_cb=kwargs["checkpoint_window_committed_cb"],
            )
            session.start_window(0, 0.0, 60.0)
            session.commit_window(0, 0.0, 60.0, "zh")
            first_window_committed.set()
            if not release_worker.wait(5):
                raise TimeoutError("worker was not released")
            raise RuntimeError("cancelled between windows")

    with (
        mock.patch("mediascribe.web.app._build_pipeline", return_value=CancelWindowPipeline()),
        mock.patch("mediascribe.audio_utils.probe_duration_seconds", return_value=118.0),
    ):
        app = create_app(workspace=workspace)
        with TestClient(app) as client:
            response = client.post(
                "/api/jobs", json={"urls": ["https://example.com/cancel"], "language": "zh"}
            )
            assert response.status_code == 200, response.text
            job_id = response.json()["jobs"][0]["job_id"]
            assert first_window_committed.wait(5)
            cancelled = client.post(f"/api/jobs/{job_id}/cancel")
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelling"
            release_worker.set()
            deadline = time.time() + 5
            while time.time() < deadline:
                snapshot = app.state.job_store.get_snapshot(job_id)
                if snapshot["status"] == "cancelled":
                    break
                time.sleep(0.02)
            assert snapshot["status"] == "cancelled", snapshot
            assert snapshot["finished"] == 1
            assert (
                app.state.job_store.get_latest_event_payload(job_id, "attempt_finished")["status"]
                == "cancelled"
            )
            app.state.job_executor.shutdown(wait=True, cancel_futures=True)


def test_ws_cancel_uses_same_snapshot_response(tmp_path: Path):
    from fastapi.testclient import TestClient

    from mediascribe.web.app import create_app

    app = create_app(workspace=tmp_path / "workspace")
    job = app.state.job_control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    app.state.job_control.start_attempt(job.job_id)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/progress/{job.job_id}") as ws:
            ws.receive_json()
            ws.send_text('{"event":"cancel"}')
            seen = []
            for _ in range(4):
                message = ws.receive_json()
                seen.append(message)
                if message.get("event") == "snapshot" and message.get("status") == "cancelling":
                    break
            assert any(
                message.get("event") == "snapshot" and message.get("status") == "cancelling"
                for message in seen
            )
