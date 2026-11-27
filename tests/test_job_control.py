from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mediascribe.progress import ProgressRegistry
from mediascribe.web.job_control import JobControl
from mediascribe.web.job_events import CheckpointConflict
from mediascribe.web.job_store import JobStore


def _control(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    registry = ProgressRegistry()
    return JobControl(store, registry), store, registry


def _event_types(store: JobStore, job_id: str) -> list[str]:
    with sqlite3.connect(store._db_path) as conn:
        rows = conn.execute(
            "SELECT type FROM job_events WHERE job_id = ? ORDER BY seq", (job_id,)
        ).fetchall()
    return [row[0] for row in rows]


def test_submit_start_progress_and_finish_share_one_snapshot(tmp_path: Path):
    control, store, registry = _control(tmp_path)
    job = control.submit(
        "https://example.test/a",
        request={"urls": ["https://example.test/a"]},
        selection={"engine": "faster-whisper", "model": "small"},
        out_path=tmp_path / "a.md",
    )
    assert job.status == "queued"
    assert registry.get(job.job_id) is not None

    control.start_attempt(job.job_id)
    control.update_stage(job.job_id, stage="download", current=50, total=100, known=True)
    live = registry.get(job.job_id)
    assert live.status == "running"
    assert live.stage == "download"
    assert live.progress_known is True
    assert store.get_snapshot(job.job_id)["stage_current"] == 50

    control.finish(job.job_id, status="succeeded", result={"out_path": str(tmp_path / "a.md")})
    assert registry.get(job.job_id).finished is True
    assert store.get_snapshot(job.job_id)["status"] == "succeeded"


def test_cancel_is_idempotent_and_terminal_cancel_is_not_accepted(tmp_path: Path):
    control, _, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)

    first = control.cancel(job.job_id)
    second = control.cancel(job.job_id)
    assert first == {
        "job_id": job.job_id,
        "accepted": True,
        "status": "cancelling",
        "finished": False,
        "cancelled": True,
    }
    assert second["accepted"] is False
    assert second["status"] == "cancelling"

    control.finish(job.job_id, status="cancelled")
    terminal = control.cancel(job.job_id)
    assert terminal["accepted"] is False
    assert terminal["status"] == "cancelled"
    assert terminal["finished"] is True


@pytest.mark.parametrize("status", ["queued", "running"])
@pytest.mark.parametrize("flag", ["finished", "cancelled", "cancel_requested"])
def test_cancel_rejects_stale_live_terminal_or_cancellation_flags(
    tmp_path: Path, status: str, flag: str
):
    control, store, registry = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    if status == "running":
        control.start_attempt(job.job_id)
    live = registry.get(job.job_id)
    setattr(live, flag, True)
    before = store.get_snapshot(job.job_id)
    events_before = _event_types(store, job.job_id)

    result = control.cancel(job.job_id)

    assert result == {
        "job_id": job.job_id,
        "accepted": False,
        "status": status,
        "finished": flag == "finished",
        "cancelled": flag == "cancelled",
    }
    assert store.get_snapshot(job.job_id) == before
    assert _event_types(store, job.job_id) == events_before
    assert live.events.empty()


@pytest.mark.parametrize("flag", ["cancelled", "cancel_requested"])
@pytest.mark.parametrize("status", ["succeeded", "failed", "no_speech", "interrupted"])
def test_finish_rejects_non_cancelled_outcome_with_stale_live_cancellation(
    tmp_path: Path, flag: str, status: str
):
    control, _, registry = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    live = registry.get(job.job_id)
    setattr(live, flag, True)

    _rejected_transition(control, job.job_id, lambda: control.finish(job.job_id, status=status))
    assert getattr(live, flag) is True


@pytest.mark.parametrize("flag", ["cancelled", "cancel_requested"])
def test_finish_accepts_cancelled_with_stale_live_cancellation_flags(tmp_path: Path, flag: str):
    control, store, registry = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    live = registry.get(job.job_id)
    if flag == "cancelled":
        registry.cancel(job.job_id)
        live.events.get_nowait()
    else:
        live.cancel_requested = True

    finished = control.finish(job.job_id, status="cancelled")

    assert finished.status == "cancelled"
    assert finished.finished is True
    assert finished.cancelled is True
    assert getattr(finished, flag) is True
    assert store.get_snapshot(job.job_id)["status"] == "cancelled"
    assert store.get_snapshot(job.job_id)["cancelled"] == 1
    assert _event_types(store, job.job_id)[-1] == "attempt_finished"
    assert live.events.get_nowait()["event"] == "cancelled"
    assert live.events.empty()


def test_unknown_cancel_returns_accepted_false_for_service_and_404_is_route_owned(tmp_path: Path):
    control, _, _ = _control(tmp_path)
    result = control.cancel("missing")
    assert result["job_id"] == "missing"
    assert result["accepted"] is False
    assert result["status"] == "unknown"


def test_submit_persists_request_selection_and_output(tmp_path: Path):
    control, store, _ = _control(tmp_path)
    request = {"urls": ["u"], "language": "zh"}
    selection = {"engine": "faster-whisper", "model": "small"}
    out_path = tmp_path / "nested" / "x.md"

    job = control.submit("u", request=request, selection=selection, out_path=out_path)
    snapshot = store.get_snapshot(job.job_id)

    assert job.job_id
    assert job.attempt_id
    assert job.progress_known is False
    assert snapshot["status"] == "queued"
    assert snapshot["out_path"] == str(out_path)
    assert json.loads(snapshot["request_json"]) == request
    assert json.loads(snapshot["selection_json"]) == selection
    assert store.next_event_seq(job.job_id) == 1
    assert _event_types(store, job.job_id) == ["job_submitted"]


def test_submit_allowlists_request_and_selection_before_persistence(tmp_path: Path):
    control, store, _ = _control(tmp_path)
    request = {
        "urls": ["u"],
        "engine": "faster-whisper",
        "model": "small",
        "language": "zh",
        "ocr_engine": "paddle",
        "save_images": True,
        "bilingual": False,
        "allow_model_download": False,
        "wechat_cookies": {"session": "wechat-cookie-sentinel"},
        "hf_token": "hf-token-sentinel",
        "api_token": "api-token-sentinel",
        "metadata": {"nested": {"token": "nested-secret-sentinel"}},
    }
    selection = {
        "engine": "faster-whisper",
        "model": "small",
        "device": "cpu",
        "language": "zh",
        "vad_filter": True,
        "resume_auth_required": False,
        "wechat_cookies": {"session": "selection-cookie-sentinel"},
        "hf_token": "selection-hf-sentinel",
        "api_token": "selection-api-sentinel",
        "metadata": {"nested": {"token": "selection-nested-sentinel"}},
    }

    job = control.submit("u", request=request, selection=selection, out_path=tmp_path / "x.md")
    snapshot = store.get_snapshot(job.job_id)
    with sqlite3.connect(store._db_path) as conn:
        payload_json = conn.execute(
            "SELECT payload_json FROM job_events WHERE job_id = ?", (job.job_id,)
        ).fetchone()[0]

    assert json.loads(snapshot["request_json"]) == {
        "urls": ["u"],
        "engine": "faster-whisper",
        "model": "small",
        "language": "zh",
        "ocr_engine": "paddle",
        "save_images": True,
        "bilingual": False,
        "allow_model_download": False,
    }
    assert json.loads(snapshot["selection_json"]) == {
        key: value
        for key, value in selection.items()
        if key not in {"wechat_cookies", "hf_token", "api_token", "metadata"}
    }
    serialized = payload_json + snapshot["request_json"] + snapshot["selection_json"]
    for sentinel in (
        "wechat-cookie-sentinel",
        "hf-token-sentinel",
        "api-token-sentinel",
        "nested-secret-sentinel",
        "selection-cookie-sentinel",
        "selection-hf-sentinel",
        "selection-api-sentinel",
        "selection-nested-sentinel",
    ):
        assert sentinel not in serialized


def test_update_stage_emits_live_and_durable_stage_events(tmp_path: Path):
    control, store, registry = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)

    control.update_stage(job.job_id, stage="download", current=50, total=100, known=True)
    control.update_stage(job.job_id, stage="download", current=75, total=100, known=True)
    control.update_stage(job.job_id, stage="download", current=100, total=100, known=True)

    snapshot = store.get_snapshot(job.job_id)
    assert snapshot["stage"] == "download"
    assert snapshot["stage_current"] == 100
    assert snapshot["stage_total"] == 100
    assert snapshot["progress_known"] == 1
    assert _event_types(store, job.job_id) == [
        "job_submitted",
        "attempt_started",
        "stage_started",
        "stage_progress",
        "stage_finished",
    ]
    live_events = []
    while not job.events.empty():
        live_events.append(job.events.get_nowait()["event"])
    assert live_events == ["stage_start", "stage_progress", "stage_progress", "stage_done"]
    assert registry.get(job.job_id).stage_current == 100


def test_update_stage_rejects_unknown_stage_without_writing(tmp_path: Path):
    control, store, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    before = store.get_snapshot(job.job_id)

    with pytest.raises(ValueError, match="unknown stage"):
        control.update_stage(job.job_id, stage="bogus", current=1, total=2, known=True)

    assert store.get_snapshot(job.job_id) == before
    assert job.events.empty()


def test_checkpoint_identity_is_persisted_before_segments(tmp_path: Path):
    control, store, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    audio_path = tmp_path / "audio.wav"

    control.set_checkpoint_identity(
        job.job_id,
        audio_path=audio_path,
        audio_sha256="audio-hash",
        decode_params_hash="params-hash",
    )

    snapshot = store.get_snapshot(job.job_id)
    assert snapshot["audio_path"] == str(audio_path)
    assert snapshot["audio_sha256"] == "audio-hash"
    assert snapshot["decode_params_hash"] == "params-hash"
    assert _event_types(store, job.job_id) == [
        "job_submitted",
        "attempt_started",
        "checkpoint_identity_set",
    ]


def test_commit_segment_is_idempotent_and_advances_cursor(tmp_path: Path):
    control, store, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    kwargs = {
        "attempt_id": job.attempt_id,
        "segment_index": 0,
        "start": 0.0,
        "end": 1.5,
        "text": "hello",
        "engine": "faster-whisper",
        "model": "small",
        "decode_params_hash": "params",
    }
    control.commit_segment(job.job_id, **kwargs)
    committed = control.commit_segment(job.job_id, **kwargs)
    snapshot = store.get_snapshot(job.job_id)
    assert committed.committed_segments == 1
    assert snapshot["committed_segments"] == 1
    assert snapshot["last_segment_index"] == 0
    assert snapshot["last_segment_end"] == 1.5
    assert len(store.get_segments(job.job_id)) == 1
    assert store.next_event_seq(job.job_id) == 3


def test_commit_segment_normalizes_text_and_rejects_conflicts(tmp_path: Path):
    control, store, registry = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    kwargs = {
        "attempt_id": job.attempt_id,
        "segment_index": 0,
        "start": 0.0,
        "end": 1.5,
        "text": "  hello\n world  ",
        "engine": "faster-whisper",
        "model": "small",
        "decode_params_hash": "params",
    }

    control.commit_segment(job.job_id, **kwargs)
    segment = store.get_segments(job.job_id)[0]
    assert segment["text"] == "hello world"
    assert segment["text_sha256"] == hashlib.sha256(b"hello world").hexdigest()

    with pytest.raises(CheckpointConflict, match="segment conflict"):
        control.commit_segment(job.job_id, **{**kwargs, "end": 2.0})
    with pytest.raises(CheckpointConflict, match="non-contiguous"):
        control.commit_segment(job.job_id, **{**kwargs, "segment_index": 2})

    retried = control.commit_segment(job.job_id, **{**kwargs, "attempt_id": "other-attempt"})
    snapshot = store.get_snapshot(job.job_id)
    assert retried is registry.get(job.job_id)
    assert retried.committed_segments == 1
    assert snapshot["committed_segments"] == 1
    assert snapshot["last_segment_index"] == 0
    assert snapshot["last_segment_end"] == 1.5
    assert len(store.get_segments(job.job_id)) == 1
    assert store.get_segments(job.job_id)[0]["attempt_id"] == job.attempt_id
    assert store.next_event_seq(job.job_id) == 3


def test_finish_persists_terminal_metadata_and_emits_live_event(tmp_path: Path):
    control, store, registry = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    out_path = tmp_path / "result.md"
    result = {"out_path": str(out_path), "duration": 1.5}

    finished = control.finish(
        job.job_id,
        status="succeeded",
        result=result,
        engine="faster-whisper",
        model="small",
        title="A title",
        out_path=out_path,
    )

    snapshot = store.get_snapshot(job.job_id)
    assert finished.status == "succeeded"
    assert finished.finished is True
    assert snapshot["status"] == "succeeded"
    assert snapshot["finished"] == 1
    assert snapshot["engine"] == "faster-whisper"
    assert snapshot["title"] == "A title"
    assert snapshot["out_path"] == str(out_path)
    assert json.loads(snapshot["result_json"]) == result
    assert registry.get(job.job_id).result == result
    assert _event_types(store, job.job_id)[-1] == "attempt_finished"
    assert job.events.get_nowait()["event"] == "succeeded"


def test_finish_persists_failure_and_no_speech_code(tmp_path: Path):
    control, store, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    recovery = {"code": "MODEL_MISSING", "reason": "safe reason"}

    control.start_attempt(job.job_id)
    control.finish(
        job.job_id,
        status="failed",
        error="internal detail",
        error_code="MODEL_MISSING",
        recovery=recovery,
    )
    snapshot = store.get_snapshot(job.job_id)
    assert snapshot["status"] == "failed"
    assert snapshot["error"] == "safe reason"
    assert snapshot["error_code"] == "MODEL_MISSING"
    assert json.loads(snapshot["recovery_json"]) == recovery

    other = control.submit("v", request={}, selection={}, out_path=tmp_path / "v.md")
    control.start_attempt(other.job_id)
    control.finish(other.job_id, status="no_speech", error="no speech")
    no_speech = store.get_snapshot(other.job_id)
    assert no_speech["status"] == "no_speech"
    assert no_speech["error_code"] == "NO_SPEECH"
    assert no_speech["finished"] == 1


def _rejected_transition(control: JobControl, job_id: str, operation) -> None:
    before = control.snapshot(job_id)
    events_before = _event_types(control.store, job_id)
    with pytest.raises(ValueError, match="invalid job transition"):
        operation()
    assert control.snapshot(job_id) == before
    assert _event_types(control.store, job_id) == events_before
    live = control.registry.get(job_id)
    assert live is not None
    assert live.events.empty()


def test_only_queued_jobs_can_start_an_attempt(tmp_path: Path):
    control, _, _ = _control(tmp_path)
    queued = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.cancel(queued.job_id)
    control.registry.get(queued.job_id).events.get_nowait()
    _rejected_transition(control, queued.job_id, lambda: control.start_attempt(queued.job_id))

    running = control.submit("v", request={}, selection={}, out_path=tmp_path / "v.md")
    control.start_attempt(running.job_id)
    _rejected_transition(control, running.job_id, lambda: control.start_attempt(running.job_id))

    terminal = control.submit("w", request={}, selection={}, out_path=tmp_path / "w.md")
    control.start_attempt(terminal.job_id)
    control.finish(terminal.job_id, status="succeeded")
    terminal_job = control.registry.get(terminal.job_id)
    terminal_job.events.get_nowait()
    _rejected_transition(control, terminal.job_id, lambda: control.start_attempt(terminal.job_id))


def test_start_attempt_never_clears_a_live_cancellation_flag(tmp_path: Path):
    control, _, registry = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    registry.cancel(job.job_id)
    registry.get(job.job_id).events.get_nowait()

    _rejected_transition(control, job.job_id, lambda: control.start_attempt(job.job_id))
    assert registry.get(job.job_id).cancelled is True


def test_progress_and_checkpoint_writes_require_running_state(tmp_path: Path):
    control, _, _ = _control(tmp_path)
    queued = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    _rejected_transition(
        control,
        queued.job_id,
        lambda: control.update_stage(
            queued.job_id, stage="download", current=1, total=2, known=True
        ),
    )
    _rejected_transition(
        control,
        queued.job_id,
        lambda: control.set_checkpoint_identity(
            queued.job_id,
            audio_path=tmp_path / "audio.wav",
            audio_sha256="audio",
            decode_params_hash="params",
        ),
    )

    cancelling = control.submit("v", request={}, selection={}, out_path=tmp_path / "v.md")
    control.start_attempt(cancelling.job_id)
    control.cancel(cancelling.job_id)
    control.registry.get(cancelling.job_id).events.get_nowait()
    _rejected_transition(
        control,
        cancelling.job_id,
        lambda: control.update_stage(
            cancelling.job_id, stage="download", current=1, total=2, known=True
        ),
    )


def test_cancelling_jobs_can_only_finish_as_cancelled(tmp_path: Path):
    control, _, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    control.cancel(job.job_id)
    control.registry.get(job.job_id).events.get_nowait()

    for status in ("succeeded", "failed", "no_speech", "interrupted"):
        _rejected_transition(
            control, job.job_id, lambda status=status: control.finish(job.job_id, status=status)
        )

    finished = control.finish(job.job_id, status="cancelled")
    assert finished.status == "cancelled"
    assert finished.finished is True
    assert finished.cancelled is True


def test_finish_rejects_unknown_status_and_queued_jobs(tmp_path: Path):
    control, _, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    _rejected_transition(
        control, job.job_id, lambda: control.finish(job.job_id, status="succeeded")
    )

    control.start_attempt(job.job_id)
    before = control.snapshot(job.job_id)
    with pytest.raises(ValueError, match="unknown terminal status"):
        control.finish(job.job_id, status="bogus")
    assert control.snapshot(job.job_id) == before
    assert control.registry.get(job.job_id).events.empty()


def test_identical_terminal_finish_is_idempotent_but_different_writes_fail(tmp_path: Path):
    control, store, registry = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    kwargs = {
        "status": "succeeded",
        "result": {"out_path": "x.md"},
        "engine": "faster-whisper",
        "model": "small",
        "title": "Title",
        "out_path": tmp_path / "x.md",
    }
    control.finish(job.job_id, **kwargs)
    registry.get(job.job_id).events.get_nowait()
    before = store.get_snapshot(job.job_id)
    events_before = _event_types(store, job.job_id)

    repeated = control.finish(job.job_id, **kwargs)
    assert repeated.status == "succeeded"
    assert store.get_snapshot(job.job_id) == before
    assert _event_types(store, job.job_id) == events_before
    assert registry.get(job.job_id).events.empty()

    for changed in (
        {**kwargs, "status": "failed"},
        {**kwargs, "result": {"out_path": "different.md"}},
        {**kwargs, "model": "large-v3"},
    ):
        with pytest.raises(CheckpointConflict, match="terminal state conflict"):
            control.finish(job.job_id, **changed)
        assert store.get_snapshot(job.job_id) == before
        assert _event_types(store, job.job_id) == events_before
        assert registry.get(job.job_id).events.empty()


@pytest.mark.parametrize(
    ("method", "kwargs", "needs_running"),
    [
        ("start_attempt", {}, False),
        ("update_stage", {"stage": "download", "current": 50, "total": 100, "known": True}, True),
        ("cancel", {}, True),
        ("finish", {"status": "succeeded", "result": {"out_path": "x.md"}}, True),
    ],
)
def test_commit_failure_does_not_publish_live_state_or_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    kwargs: dict[str, object],
    needs_running: bool,
):
    control, store, registry = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    if needs_running:
        control.start_attempt(job.job_id)
    live_before = registry.get(job.job_id).to_dict()
    durable_before = store.get_snapshot(job.job_id)
    events_before = _event_types(store, job.job_id)

    def fail_commit(*_args, **_kwargs):
        raise sqlite3.OperationalError("injected commit failure")

    monkeypatch.setattr(store, "commit_event", fail_commit)
    with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
        getattr(control, method)(job.job_id, **kwargs)

    assert registry.get(job.job_id).to_dict() == live_before
    assert store.get_snapshot(job.job_id) == durable_before
    assert _event_types(store, job.job_id) == events_before
    assert job.events.empty()


def test_resume_rejects_decode_mismatch_before_creating_attempt(tmp_path: Path):
    import hashlib

    from mediascribe.checkpoint import capability_for
    from mediascribe.web.job_recovery import decode_params_hash

    control, store, _ = _control(tmp_path)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    payload = {"engine": "whisper", "model": "small", "device": "cpu", "language": "zh"}
    job = control.submit(
        "https://example.com/v",
        request={},
        selection={"engine": "whisper"},
        out_path=tmp_path / "x.md",
    )
    control.start_attempt(job.job_id)
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=audio,
        audio_sha256=hashlib.sha256(b"audio").hexdigest(),
        decode_params_hash=decode_params_hash(payload),
        capability=capability_for("whisper"),
        audio_duration=60.0,
    )
    control.recover_interrupted()
    before = store.get_snapshot(job.job_id)
    events_before = _event_types(store, job.job_id)

    with pytest.raises(ValueError, match="RESUME_INPUT_MISMATCH"):
        control.resume(job.job_id, decode_params={**payload, "language": "en"})

    assert store.get_snapshot(job.job_id) == before
    assert _event_types(store, job.job_id) == events_before


def test_hydrate_restores_projection_without_historical_live_events(tmp_path: Path):
    control, store, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    control.update_stage(job.job_id, stage="transcribe", current=4, total=10, known=True)

    restarted_registry = ProgressRegistry()
    restarted = JobControl(store, restarted_registry)
    hydrated = restarted.hydrate(job.job_id)

    assert hydrated is not None
    assert hydrated.status == "running"
    assert hydrated.stage == "transcribe"
    assert hydrated.stage_current == 4
    assert hydrated.progress_known
    assert hydrated.events.empty()
    assert restarted.snapshot("missing") is None
    assert restarted.hydrate("missing") is None


def test_concurrent_same_index_segments_commit_once(tmp_path: Path):
    control, store, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    start = threading.Barrier(2)
    kwargs = {
        "attempt_id": job.attempt_id,
        "segment_index": 0,
        "start": 0.0,
        "end": 1.5,
        "text": "hello",
        "engine": "faster-whisper",
        "model": "small",
        "decode_params_hash": "params",
    }

    def commit_segment():
        start.wait(timeout=5)
        return control.commit_segment(job.job_id, **kwargs)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: commit_segment(), range(2)))

    snapshot = store.get_snapshot(job.job_id)
    assert [result.committed_segments for result in results] == [1, 1]
    assert snapshot["committed_segments"] == 1
    assert snapshot["last_segment_index"] == 0
    assert snapshot["last_segment_end"] == 1.5
    assert len(store.get_segments(job.job_id)) == 1
    assert _event_types(store, job.job_id).count("segment_committed") == 1
    assert store.next_event_seq(job.job_id) == 3


def test_concurrent_cancel_accepts_exactly_one_request(tmp_path: Path):
    control, store, registry = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    start = threading.Barrier(2)

    def cancel():
        start.wait(timeout=5)
        return control.cancel(job.job_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: cancel(), range(2)))

    assert sorted(result["accepted"] for result in results) == [False, True]
    live = registry.get(job.job_id)
    assert live.status == "cancelling"
    assert live.cancel_requested is True
    assert live.cancelled is True
    assert live.finished is False
    assert _event_types(store, job.job_id).count("cancel_requested") == 1
    assert store.get_snapshot(job.job_id)["cancel_requested"] == 1
    assert job.events.qsize() == 1
    assert job.events.get_nowait()["event"] == "cancel_requested"


def test_concurrent_updates_allocate_unique_event_sequences(tmp_path: Path):
    control, store, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    before = store.next_event_seq(job.job_id)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda current: control.update_stage(
                    job.job_id, stage="download", current=current, total=100, known=True
                ),
                range(20),
            )
        )

    assert store.next_event_seq(job.job_id) == before + 20
    assert len(_event_types(store, job.job_id)) == before + 20


@pytest.mark.parametrize("engine", ["whisper", "whisperx", "whisper-cpp"])
def test_restart_marks_window_engine_resumable_with_complete_identity(tmp_path: Path, engine: str):
    control, store, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={"engine": engine}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=tmp_path / "audio.wav",
        audio_sha256="audio-hash",
        decode_params_hash="params-hash",
    )

    assert control.recover_interrupted() == 1
    snapshot = store.get_snapshot(job.job_id)
    assert snapshot["status"] == "interrupted"
    assert snapshot["resumable"] == 1
    assert snapshot["resume_block_reason"] is None
    assert store.get_latest_event_payload(job.job_id, "job_interrupted") == {"resumable": True}


@pytest.mark.parametrize("engine", ["whisper", "whisperx", "whisper-cpp"])
@pytest.mark.parametrize("missing_identity", ["audio_path", "audio_sha256", "decode_params_hash"])
def test_restart_marks_window_engine_unsupported_with_incomplete_identity(
    tmp_path: Path, engine: str, missing_identity: str
):
    control, store, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={"engine": engine}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=tmp_path / "audio.wav",
        audio_sha256="audio-hash",
        decode_params_hash="params-hash",
    )
    with sqlite3.connect(store._db_path) as conn:
        conn.execute(
            f"UPDATE jobs SET {missing_identity} = NULL WHERE job_id = ?",
            (job.job_id,),
        )
        conn.commit()

    assert control.recover_interrupted() == 1
    snapshot = store.get_snapshot(job.job_id)
    assert snapshot["status"] == "interrupted"
    assert snapshot["resumable"] == 0
    assert snapshot["resume_block_reason"] == "RESUME_UNSUPPORTED"
    assert store.get_latest_event_payload(job.job_id, "job_interrupted") == {"resumable": False}


def test_checkpoint_window_marker_advances_through_silence(tmp_path: Path):
    from mediascribe.checkpoint import CheckpointCapability

    control, store, _ = _control(tmp_path)
    job = control.submit(
        "u", request={}, selection={"engine": "whisper"}, out_path=tmp_path / "x.md"
    )
    control.start_attempt(job.job_id)
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=tmp_path / "audio.wav",
        audio_sha256="hash",
        decode_params_hash="params",
        capability=CheckpointCapability("whisper", "window", 60, 2),
        audio_duration=119.0,
    )

    control.start_checkpoint_window(job.job_id, window_index=0, start=0.0, end=60.0)
    active = store.get_snapshot(job.job_id)
    assert active["active_checkpoint_window"] == 0
    assert active["checkpoint_cursor"] == 0.0
    assert active["last_segment_end"] == 0.0

    control.commit_checkpoint_window(
        job.job_id,
        window_index=0,
        start=0.0,
        end=60.0,
        detected_language="zh",
    )
    completed = store.get_snapshot(job.job_id)
    assert completed["active_checkpoint_window"] is None
    assert completed["checkpoint_window_index"] == 0
    assert completed["checkpoint_cursor"] == 60.0
    assert completed["last_segment_end"] == 0.0
    assert completed["detected_language"] == "zh"
    assert _event_types(store, job.job_id)[-2:] == [
        "checkpoint_window_started",
        "checkpoint_window_committed",
    ]


def test_checkpoint_windows_finish_exact_118_seconds_without_redundant_tail(tmp_path: Path):
    from mediascribe.checkpoint import (
        CheckpointCapability,
        checkpoint_window_bounds,
    )

    control, store, _ = _control(tmp_path)
    job = control.submit(
        "u", request={}, selection={"engine": "whisper"}, out_path=tmp_path / "x.md"
    )
    control.start_attempt(job.job_id)
    capability = CheckpointCapability("whisper", "window", 60, 2)
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=tmp_path / "audio.wav",
        audio_sha256="hash",
        decode_params_hash="params",
        capability=capability,
        audio_duration=118.0,
    )

    segments = {
        0: (59.0, 60.0, "prefix suffix"),
        1: (114.0, 118.0, "tail"),
    }
    for window_index, start, end in checkpoint_window_bounds(118.0, 60, 2):
        control.start_checkpoint_window(job.job_id, window_index=window_index, start=start, end=end)
        if window_index in segments:
            segment_index = window_index
            segment_start, segment_end, text = segments[window_index]
            control.commit_segment(
                job.job_id,
                attempt_id=job.attempt_id,
                segment_index=segment_index,
                start=segment_start,
                end=segment_end,
                text=text,
                engine="whisper",
                model="small",
                decode_params_hash="params",
            )
        control.commit_checkpoint_window(
            job.job_id,
            window_index=window_index,
            start=start,
            end=end,
            detected_language="zh",
        )

    snapshot = store.get_snapshot(job.job_id)
    assert snapshot["active_checkpoint_window"] is None
    assert snapshot["checkpoint_window_index"] == 1
    assert snapshot["checkpoint_cursor"] == 118.0
    assert snapshot["committed_segments"] == 2
    assert snapshot["last_segment_end"] == 118.0
    assert [segment["text"] for segment in store.get_segments(job.job_id)] == [
        "prefix suffix",
        "tail",
    ]
    assert _event_types(store, job.job_id).count("checkpoint_window_committed") == 2


def test_checkpoint_window_rejects_skip_or_backwards_cursor(tmp_path: Path):
    from mediascribe.checkpoint import CheckpointCapability

    control, store, _ = _control(tmp_path)
    job = control.submit(
        "u", request={}, selection={"engine": "whisper"}, out_path=tmp_path / "x.md"
    )
    control.start_attempt(job.job_id)
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=tmp_path / "audio.wav",
        audio_sha256="hash",
        decode_params_hash="params",
        capability=CheckpointCapability("whisper", "window", 60, 2),
        audio_duration=119.0,
    )
    with pytest.raises(CheckpointConflict, match="window sequence"):
        control.start_checkpoint_window(job.job_id, window_index=1, start=58.0, end=118.0)
    control.start_checkpoint_window(job.job_id, window_index=0, start=0.0, end=60.0)
    control.commit_checkpoint_window(job.job_id, window_index=0, start=0.0, end=60.0)
    with sqlite3.connect(store._db_path) as conn:
        conn.execute(
            "UPDATE jobs SET active_checkpoint_window = 0 WHERE job_id = ?",
            (job.job_id,),
        )
        conn.commit()
    with pytest.raises(CheckpointConflict, match="window cursor"):
        control.commit_checkpoint_window(job.job_id, window_index=0, start=0.0, end=60.0)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"start": -0.01, "end": 1.0}, "timestamp"),
        ({"start": 9.0, "end": 10.06}, "duration"),
        ({"start": 4.0, "end": 4.9}, "advance"),
        ({"end": 6.0, "words": {}}, "words"),
        ({"end": 6.0, "words": ["bad"]}, "words"),
        ({"end": 6.0, "words": [{"start": -0.01, "end": 0.5}]}, "word"),
        ({"end": 6.0, "words": [{"start": 0.5, "end": 0.4}]}, "word"),
        ({"end": 6.0, "words": [{"start": 0.0, "end": 6.06}]}, "word"),
        ({"end": 6.0, "words": [{"start": float("nan"), "end": 0.5}]}, "word"),
    ],
)
def test_commit_segment_rejects_malformed_durable_timeline(
    tmp_path: Path, overrides: dict[str, object], match: str
):
    control, store, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    control.set_checkpoint_identity(
        job.job_id,
        audio_path=tmp_path / "audio.wav",
        audio_sha256="hash",
        decode_params_hash="params",
        audio_duration=10.0,
    )
    base = {
        "attempt_id": job.attempt_id,
        "segment_index": 0,
        "start": 0.0,
        "end": 5.0,
        "text": "prefix",
        "engine": "faster-whisper",
        "model": "small",
        "decode_params_hash": "params",
    }
    control.commit_segment(job.job_id, **base)
    before = store.get_snapshot(job.job_id)
    events_before = _event_types(store, job.job_id)

    with pytest.raises(ValueError, match=match):
        control.commit_segment(
            job.job_id,
            **{**base, "segment_index": 1, "text": "candidate", **overrides},
        )

    assert store.get_snapshot(job.job_id) == before
    assert _event_types(store, job.job_id) == events_before
    assert len(store.get_segments(job.job_id)) == 1


def test_commit_segment_preserves_valid_resumed_overlap(tmp_path: Path):
    control, store, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    base = {
        "attempt_id": job.attempt_id,
        "start": 0.0,
        "end": 5.0,
        "text": "prefix",
        "engine": "faster-whisper",
        "model": "small",
        "decode_params_hash": "params",
        "words": [{"word": "prefix", "start": 0.0, "end": 5.0}],
    }
    control.commit_segment(job.job_id, **{**base, "segment_index": 0})
    committed = control.commit_segment(
        job.job_id,
        **{
            **base,
            "segment_index": 1,
            "start": 4.0,
            "end": 6.0,
            "text": "suffix",
            "words": [{"word": "suffix", "start": 4.0, "end": 6.0}],
        },
    )
    assert committed.last_segment_end == 6.0
    assert store.get_snapshot(job.job_id)["last_segment_end"] == 6.0


def test_commit_segment_rejects_nonfinite_or_reversed_time_and_persists_words(tmp_path: Path):
    control, store, _ = _control(tmp_path)
    job = control.submit("u", request={}, selection={}, out_path=tmp_path / "x.md")
    control.start_attempt(job.job_id)
    base = {
        "attempt_id": job.attempt_id,
        "segment_index": 0,
        "start": 0.0,
        "end": 1.0,
        "text": "hello",
        "engine": "whisperx",
        "model": "small",
        "decode_params_hash": "params",
        "words": [{"word": "hello", "start": 0.0, "end": 1.0, "score": 0.9}],
    }
    with pytest.raises(ValueError, match="timestamp"):
        control.commit_segment(job.job_id, **{**base, "start": float("nan")})
    with pytest.raises(ValueError, match="order"):
        control.commit_segment(job.job_id, **{**base, "start": 2.0, "end": 1.0})

    control.commit_segment(job.job_id, **base)
    segment = store.get_segments(job.job_id)[0]
    assert json.loads(segment["words_json"]) == base["words"]
