"""Single writer for durable Web job state transitions."""

from __future__ import annotations

import hashlib
import json
import math
import queue
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from mediascribe.checkpoint import (
    CheckpointCapability,
    capability_for,
    checkpoint_window_bounds,
    validate_segment_timeline,
    validate_word_timelines,
)
from mediascribe.progress import (
    EVENTS_QUEUE_MAXSIZE,
    STAGES,
    JobProgress,
    ProgressRegistry,
)
from mediascribe.web.job_events import CheckpointConflict, JobEvent
from mediascribe.web.job_recovery import ResumePlan, build_resume_plan, validate_resume_decode
from mediascribe.web.job_store import JobStore

_REQUEST_KEYS = frozenset(
    {
        "urls",
        "engine",
        "model",
        "language",
        "ocr_engine",
        "save_images",
        "bilingual",
        "allow_model_download",
    }
)
_SELECTION_KEYS = frozenset(
    {"engine", "model", "device", "language", "vad_filter", "resume_auth_required"}
)
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "no_speech", "interrupted"})


class JobControl:
    """Coordinate live job progress with atomic durable state transitions."""

    def __init__(self, store: JobStore, registry: ProgressRegistry) -> None:
        self.store = store
        self.registry = registry
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, job_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(job_id, threading.RLock())

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        """Return the durable projection for a job, if it exists."""
        with self._lock_for(job_id):
            return self.store.get_snapshot(job_id)

    def hydrate(self, job_id: str) -> JobProgress | None:
        """Restore a normalized durable job without replaying historical events."""
        with self._lock_for(job_id):
            snapshot = self.store.get_snapshot(job_id)
            return (
                self.registry.upsert(self._normalize_snapshot(snapshot))
                if snapshot is not None
                else None
            )

    @staticmethod
    def _normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        """Convert SQLite storage values into the typed live projection contract."""
        normalized = dict(snapshot)
        for key in (
            "finished",
            "cancelled",
            "progress_known",
            "cancel_requested",
            "resumable",
        ):
            if key in normalized:
                normalized[key] = bool(normalized[key])
        for key in ("recovery", "result"):
            json_key = f"{key}_json"
            if json_key in normalized:
                raw = normalized[json_key]
                normalized[key] = json.loads(raw) if isinstance(raw, str) and raw else raw
        return normalized

    def recover_interrupted(self) -> int:
        """Commit restart interruption for unfinished jobs without enqueueing resumes."""
        count = 0
        for snapshot in self.store.list_unfinished():
            current = self.hydrate(snapshot["job_id"])
            if current is None:
                continue
            job = self._candidate(current)
            if not job.attempt_id:
                job.attempt_id = uuid.uuid4().hex
            selection = json.loads(snapshot.get("selection_json") or "{}")
            capability = capability_for(str(selection.get("engine") or ""))
            resumable = (
                capability is not None
                and (snapshot.get("checkpoint_mode") or capability.mode) == capability.mode
                and bool(snapshot.get("audio_path"))
                and bool(snapshot.get("audio_sha256"))
                and bool(snapshot.get("decode_params_hash"))
                and not selection.get("resume_auth_required")
            )
            job.status = "interrupted"
            job.finished = True
            job.cancelled = False
            job.resumable = resumable
            job.resume_block_reason = None if resumable else "RESUME_UNSUPPORTED"
            job.error = "interrupted: server restarted while the job was in flight"
            self._commit(job, "job_interrupted", {"resumable": resumable})
            count += 1
        return count

    def _job(self, job_id: str) -> JobProgress:
        job = self.registry.get(job_id) or self.hydrate(job_id)
        if job is None:
            raise KeyError(f"unknown job: {job_id}")
        return job

    @staticmethod
    def _require_status(job: JobProgress, *allowed: str) -> None:
        if job.status not in allowed:
            raise ValueError(f"invalid job transition for {job.job_id}: {job.status} -> {allowed}")

    @staticmethod
    def _candidate(job: JobProgress) -> JobProgress:
        return replace(job, events=queue.Queue(maxsize=EVENTS_QUEUE_MAXSIZE))

    @staticmethod
    def _publish(candidate: JobProgress, committed: JobProgress) -> None:
        while not candidate.events.empty():
            payload = candidate.events.get_nowait()
            event = payload.pop("event")
            payload.pop("ts", None)
            committed.emit(event, **payload)

    def _store_snapshot(
        self, job: JobProgress, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        snapshot = job.to_dict()
        snapshot["recovery_json"] = (
            json.dumps(job.recovery, ensure_ascii=False, sort_keys=True) if job.recovery else None
        )
        snapshot["result_json"] = (
            json.dumps(job.result, ensure_ascii=False, sort_keys=True) if job.result else None
        )
        snapshot.pop("recovery", None)
        snapshot.pop("result", None)
        snapshot.pop("checkpoint", None)
        snapshot.update(extra or {})
        return snapshot

    def _commit(
        self,
        job: JobProgress,
        event_type: str,
        payload: dict[str, Any],
        *,
        snapshot: dict[str, Any] | None = None,
        segment: dict[str, Any] | None = None,
    ) -> JobProgress:
        with self._lock_for(job.job_id):
            event = JobEvent(
                event_id=uuid.uuid4().hex,
                job_id=job.job_id,
                attempt_id=job.attempt_id,
                seq=self.store.next_event_seq(job.job_id),
                type=event_type,
                payload=payload,
                created_at=time.time(),
            )
            merged = self._store_snapshot(job, snapshot)
            self.store.commit_event(event, snapshot=merged, segment=segment)
            live_snapshot = {**merged, "recovery": job.recovery, "result": job.result}
            return self.registry.upsert(live_snapshot)

    def submit(
        self,
        url: str,
        *,
        request: dict[str, Any],
        selection: dict[str, Any],
        out_path: Path,
        retry_of: str | None = None,
    ) -> JobProgress:
        """Create a queued job and its initial durable event in one transaction."""
        job = JobProgress(
            job_id=uuid.uuid4().hex,
            url=url,
            attempt_id=uuid.uuid4().hex,
            retry_of=retry_of,
        )
        safe_request = {key: value for key, value in request.items() if key in _REQUEST_KEYS}
        safe_selection = {key: value for key, value in selection.items() if key in _SELECTION_KEYS}
        request_json = json.dumps(safe_request, ensure_ascii=False, sort_keys=True)
        selection_json = json.dumps(safe_selection, ensure_ascii=False, sort_keys=True)
        output = str(out_path)
        with self._lock_for(job.job_id):
            return self._commit(
                job,
                "job_submitted",
                {
                    "url": url,
                    "out_path": output,
                    "request_json": request_json,
                    "selection_json": selection_json,
                },
                snapshot={
                    "out_path": output,
                    "request_json": request_json,
                    "selection_json": selection_json,
                },
            )

    def start_attempt(self, job_id: str) -> JobProgress:
        """Mark the current attempt as running."""
        with self._lock_for(job_id):
            current = self._job(job_id)
            self._require_status(current, "queued")
            if current.cancelled or current.cancel_requested or current.finished:
                raise ValueError(f"invalid job transition for {job_id}: cancelled or finished")
            job = self._candidate(current)
            job.status = "running"
            job.finished = False
            job.cancelled = False
            job.cancel_requested = False
            return self._commit(job, "attempt_started", {})

    def update_stage(
        self,
        job_id: str,
        *,
        stage: str,
        current: int,
        total: int,
        known: bool,
    ) -> JobProgress:
        """Update stage progress in the live queue and durable projection."""
        with self._lock_for(job_id):
            if stage not in STAGES:
                raise ValueError(f"unknown stage {stage!r}")
            current_job = self._job(job_id)
            self._require_status(current_job, "running")
            job = self._candidate(current_job)
            stage_changed = job.stage != stage
            if stage_changed:
                job.start_stage(stage, total=total, known=known)
            else:
                job.stage_total = total
                job.progress_known = known
            if current >= total:
                job.finish_stage()
                event_type = "stage_started" if stage_changed else "stage_finished"
            else:
                job.advance(current)
                event_type = "stage_started" if stage_changed else "stage_progress"
            committed = self._commit(
                job,
                event_type,
                {
                    "stage": stage,
                    "current": job.stage_current,
                    "total": job.stage_total,
                    "known": job.progress_known,
                },
            )
            self._publish(job, committed)
            return committed

    def set_checkpoint_identity(
        self,
        job_id: str,
        *,
        audio_path: Path,
        audio_sha256: str,
        decode_params_hash: str,
        capability: CheckpointCapability | None = None,
        audio_duration: float | None = None,
        runtime_auth_required: bool = False,
    ) -> JobProgress:
        """Persist the audio and decode identity used to validate later segments."""
        with self._lock_for(job_id):
            current = self._job(job_id)
            self._require_status(current, "running")
            job = self._candidate(current)
            identity: dict[str, Any] = {
                "audio_path": str(audio_path),
                "audio_sha256": audio_sha256,
                "decode_params_hash": decode_params_hash,
            }
            if capability is not None:
                identity.update(
                    {
                        "checkpoint_mode": capability.mode,
                        "checkpoint_window_seconds": capability.window_seconds,
                        "checkpoint_overlap_seconds": capability.overlap_seconds,
                        "runtime_auth_required": int(
                            capability.requires_runtime_auth or runtime_auth_required
                        ),
                    }
                )
            if audio_duration is not None:
                identity["audio_duration"] = audio_duration
            return self._commit(job, "checkpoint_identity_set", identity, snapshot=identity)

    @staticmethod
    def _validate_window_bounds(
        snapshot: dict[str, Any], window_index: int, start: float, end: float
    ) -> None:
        """Reject windows that do not match the stored deterministic timeline."""
        duration = snapshot.get("audio_duration")
        window_seconds = snapshot.get("checkpoint_window_seconds")
        overlap_seconds = snapshot.get("checkpoint_overlap_seconds")
        if (
            snapshot.get("checkpoint_mode") != "window"
            or duration is None
            or window_seconds is None
            or overlap_seconds is None
            or isinstance(window_index, bool)
            or not isinstance(window_index, int)
        ):
            raise CheckpointConflict("checkpoint window capability is missing or invalid")
        try:
            bounds = checkpoint_window_bounds(
                float(duration), int(window_seconds), int(overlap_seconds)
            )
        except (TypeError, ValueError) as exc:
            raise CheckpointConflict("checkpoint window capability is invalid") from exc
        if (
            not 0 <= window_index < len(bounds)
            or (
                window_index,
                start,
                end,
            )
            != bounds[window_index]
        ):
            raise CheckpointConflict(f"checkpoint window bounds conflict at index {window_index}")

    def start_checkpoint_window(
        self, job_id: str, *, window_index: int, start: float, end: float
    ) -> JobProgress:
        """Mark a deterministic checkpoint window as active exactly once."""
        with self._lock_for(job_id):
            current = self._job(job_id)
            self._require_status(current, "running")
            snapshot = self.store.get_snapshot(job_id)
            if snapshot is None:
                raise KeyError(f"unknown job: {job_id}")
            self._validate_window_bounds(snapshot, window_index, start, end)
            expected_active = snapshot.get("active_checkpoint_window")
            expected_next = int(snapshot["checkpoint_window_index"]) + 1
            if expected_active is not None:
                if int(expected_active) != window_index:
                    raise CheckpointConflict(f"window sequence conflict for {job_id}")
                return current
            if window_index != expected_next:
                raise CheckpointConflict(f"window sequence conflict for {job_id}")
            payload = {"window_index": window_index, "start": start, "end": end}
            return self._commit(
                current,
                "checkpoint_window_started",
                payload,
                snapshot={"active_checkpoint_window": window_index},
            )

    def commit_checkpoint_window(
        self,
        job_id: str,
        *,
        window_index: int,
        start: float,
        end: float,
        detected_language: str | None = None,
    ) -> JobProgress:
        """Advance the durable cursor even when a window contains no segments."""
        with self._lock_for(job_id):
            current = self._job(job_id)
            self._require_status(current, "running")
            snapshot = self.store.get_snapshot(job_id)
            if snapshot is None:
                raise KeyError(f"unknown job: {job_id}")
            self._validate_window_bounds(snapshot, window_index, start, end)
            if snapshot.get("active_checkpoint_window") != window_index:
                raise CheckpointConflict(f"window is not active for {job_id}")
            if end <= float(snapshot["checkpoint_cursor"]):
                raise CheckpointConflict(f"window cursor conflict for {job_id}")
            payload = {
                "window_index": window_index,
                "start": start,
                "end": end,
                "detected_language": detected_language,
            }
            return self._commit(
                current,
                "checkpoint_window_committed",
                payload,
                snapshot={
                    "active_checkpoint_window": None,
                    "checkpoint_window_index": window_index,
                    "checkpoint_cursor": end,
                    "detected_language": detected_language or snapshot.get("detected_language"),
                },
            )

    def commit_segment(
        self,
        job_id: str,
        *,
        attempt_id: str,
        segment_index: int,
        start: float,
        end: float,
        text: str,
        engine: str,
        model: str,
        decode_params_hash: str,
        words: list[dict[str, Any]] | None = None,
    ) -> JobProgress:
        """Commit one normalized segment idempotently and advance its cursor."""
        with self._lock_for(job_id):
            normalized = " ".join(text.split())
            if not normalized:
                raise ValueError("segment text must not be empty")
            current_job = self._job(job_id)
            job = self._candidate(current_job)
            snapshot = self.store.get_snapshot(job.job_id)
            if snapshot is None:
                raise KeyError(f"unknown job: {job.job_id}")
            maximum: float | None = None
            if snapshot.get("audio_duration") is not None:
                try:
                    maximum = float(snapshot["audio_duration"])
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError("invalid audio duration for segment boundary") from exc
                if not math.isfinite(maximum) or maximum < 0:
                    raise ValueError("invalid audio duration for segment boundary")
            current = self.store.get_segment(job.job_id, segment_index)
            if current is not None:
                start, end = validate_segment_timeline(start, end, maximum=maximum)
                normalized_words = (
                    validate_word_timelines(words, segment_start=start, segment_end=end)
                    if words is not None
                    else None
                )
            else:
                start, end = validate_segment_timeline(start, end, maximum=maximum)
                normalized_words = (
                    validate_word_timelines(words, segment_start=start, segment_end=end)
                    if words is not None
                    else None
                )
            words_json = (
                json.dumps(
                    normalized_words, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                if normalized_words is not None
                else None
            )
            incoming_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            segment = {
                "job_id": job.job_id,
                "segment_index": segment_index,
                "attempt_id": attempt_id,
                "start": start,
                "end": end,
                "text": normalized,
                "text_sha256": incoming_hash,
                "engine": engine,
                "model": model,
                "decode_params_hash": decode_params_hash,
            }
            if words_json is not None:
                segment["words_json"] = words_json
            if current is not None:
                content_fields: tuple[str, ...] = (
                    "text",
                    "start",
                    "end",
                    "text_sha256",
                    "engine",
                    "model",
                    "decode_params_hash",
                )
                if words_json is not None:
                    content_fields += ("words_json",)
                if any(current[field] != segment[field] for field in content_fields):
                    raise CheckpointConflict(
                        f"segment conflict for {job.job_id} index {segment_index}"
                    )
                return current_job
            self._require_status(current_job, "running")
            if attempt_id != job.attempt_id:
                raise CheckpointConflict(f"attempt conflict for {job.job_id}: {attempt_id}")
            if segment_index != job.last_segment_index + 1:
                raise CheckpointConflict(
                    f"non-contiguous segment index for {job.job_id}: {segment_index}"
                )
            start, end = validate_segment_timeline(
                start,
                end,
                maximum=maximum,
                cursor_end=float(snapshot["last_segment_end"]),
            )

            job.committed_segments += 1
            job.last_segment_index = segment_index
            job.last_segment_end = end
            payload = {
                "segment_index": segment_index,
                "start": start,
                "end": end,
                "text": normalized,
                "text_sha256": incoming_hash,
            }
            if words_json is not None:
                payload["words_json"] = words_json
            return self._commit(
                job,
                "segment_committed",
                payload,
                segment=segment,
            )

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Request cooperative cancellation without blocking the worker."""
        with self._lock_for(job_id):
            try:
                job = self._job(job_id)
            except KeyError:
                return {
                    "job_id": job_id,
                    "accepted": False,
                    "status": "unknown",
                    "finished": False,
                    "cancelled": False,
                }
            if (
                job.status not in {"queued", "running"}
                or job.finished
                or job.cancelled
                or job.cancel_requested
            ):
                return {
                    "job_id": job.job_id,
                    "accepted": False,
                    "status": job.status,
                    "finished": job.finished,
                    "cancelled": job.cancelled,
                }
            candidate = self._candidate(job)
            candidate.cancel_requested = True
            candidate.cancelled = True
            candidate.status = "cancelling"
            candidate.emit("cancel_requested")
            committed = self._commit(candidate, "cancel_requested", {"requested_at": time.time()})
            self._publish(candidate, committed)
            return {
                "job_id": job.job_id,
                "accepted": True,
                "status": "cancelling",
                "finished": False,
                "cancelled": True,
            }

    def resume(self, job_id: str, *, decode_params: dict[str, Any] | None = None) -> ResumePlan:
        """Create a new queued attempt from a validated interrupted checkpoint."""
        with self._lock_for(job_id):
            current = self._job(job_id)
            if current.status != "interrupted" or not current.resumable:
                raise ValueError("RESUME_UNSUPPORTED")
            attempt_id = uuid.uuid4().hex
            snapshot = self.store.get_snapshot(job_id)
            if snapshot is None:
                raise KeyError(f"unknown job: {job_id}")
            plan = build_resume_plan(
                snapshot,
                self.store.get_segments(job_id),
                attempt_id=attempt_id,
            )
            if decode_params is not None:
                validate_resume_decode(plan, decode_params)
            job = self._candidate(current)
            job.attempt_id = attempt_id
            job.status = "queued"
            job.finished = False
            job.cancelled = False
            job.cancel_requested = False
            job.resumable = False
            job.resume_block_reason = None
            job.resume_count += 1
            job.error = None
            job.error_code = None
            job.recovery = None
            job.result = None
            self._commit(
                job,
                "resume_requested",
                {
                    "attempt_id": attempt_id,
                    "resume_start": plan.resume_start,
                    "next_segment_index": plan.next_segment_index,
                },
            )
            return plan

    def finish(
        self,
        job_id: str,
        *,
        status: str,
        error: str | None = None,
        error_code: str | None = None,
        recovery: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        engine: str | None = None,
        model: str | None = None,
        title: str | None = None,
        out_path: Path | None = None,
    ) -> JobProgress:
        """Commit a terminal state before publishing its live terminal event."""
        with self._lock_for(job_id):
            if status not in _TERMINAL_STATUSES:
                raise ValueError(f"unknown terminal status {status!r}")
            current = self._job(job_id)
            if (current.cancelled or current.cancel_requested) and status != "cancelled":
                raise ValueError(f"invalid job transition for {job_id}: cancelled -> {status}")
            output = str(out_path) if out_path is not None else None
            normalized_error_code = error_code
            if status == "no_speech" and normalized_error_code is None:
                normalized_error_code = "NO_SPEECH"
            payload: dict[str, Any] = {
                "status": status,
                "error": error,
                "error_code": normalized_error_code,
                "recovery": recovery,
                "result": result,
                "engine": engine,
                "model": model,
                "title": title,
                "out_path": output,
            }
            if current.status in _TERMINAL_STATUSES:
                previous = self.store.get_latest_event_payload(job_id, "attempt_finished")
                if previous == payload:
                    return current
                raise CheckpointConflict(f"terminal state conflict for {job_id}")
            if current.status == "cancelling":
                if status != "cancelled":
                    raise ValueError(f"invalid job transition for {job_id}: cancelling -> {status}")
            else:
                self._require_status(current, "running")
            job = self._candidate(current)
            job.status = status
            job.finished = True
            job.cancelled = status == "cancelled"
            job.error = error
            job.error_code = normalized_error_code
            job.recovery = recovery
            job.result = result
            extra = {
                key: value
                for key, value in {
                    "engine": engine,
                    "title": title,
                    "out_path": output,
                }.items()
                if value is not None
            }
            live_event = "failed" if status == "no_speech" else status
            job.emit(
                live_event,
                error=(recovery or {}).get("reason") or error,
                error_code=normalized_error_code,
                recovery=recovery,
                result=result,
                status=status,
            )
            committed = self._commit(job, "attempt_finished", payload, snapshot=extra)
            self._publish(job, committed)
            return committed
