"""
Job progress tracking — WebSocket-friendly stage progress for the
mediascribe pipeline.

This module is deliberately decoupled from any framework: it just
provides a :class:`JobProgress` dataclass, a :class:`ProgressRegistry`
to look jobs up by id, and a few helpers used by the FastAPI layer
in :mod:`mediascribe.web.app`.

A job has four stages, run in order::

    download → extract_audio → transcribe → merge

Each stage carries a tuple of (current, total).  The pipeline advances
through them; the WebSocket layer reads :attr:`JobProgress.events` (a
thread-safe queue) and pushes the events to the browser.

Cancellation is cooperative: clients call
:func:`JobRegistry.cancel`, which sets :attr:`JobProgress.cancelled`
to ``True``.  The pipeline checks this flag between stages and bails
out cleanly with a status of ``"cancelled"``.
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

STAGES = ("download", "extract_audio", "transcribe", "merge")

# P2-9: events 队列上限 — 旧实现是无界 ``queue.Queue``, 慢/无消费者时
# 事件无限堆积 (内存泄漏)。满时丢弃**最旧**事件, 保证最新事件(尤其是
# ``succeeded`` / ``failed`` / ``cancelled`` 等终态事件)永不丢失。
EVENTS_QUEUE_MAXSIZE = 1000


def _put_bounded(q: "queue.Queue[dict]", payload: Dict[str, Any]) -> bool:
    """把事件放入有界队列; 队列满时丢弃最旧事件再入队。

    返回 ``True`` 表示发生了丢弃(丢弃计数由调用方累加)。丢最旧策略
    下终态事件总是最后 emit, 因此天然不会被丢弃。
    """
    dropped = False
    while True:
        try:
            q.put_nowait(payload)
            return dropped
        except queue.Full:
            dropped = True
            try:
                q.get_nowait()
            except queue.Empty:  # pragma: no cover - 并发竞争下的防御
                pass


@dataclass
class JobProgress:
    """A single in-flight transcription job's progress state."""

    job_id: str
    url: str
    created_at: float = field(default_factory=time.time)
    cancelled: bool = False
    finished: bool = False
    stage: Optional[str] = None
    stage_index: int = 0
    stage_current: int = 0
    stage_total: int = 0
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    # The events queue is consumed by the WebSocket layer.  New
    # listeners are not auto-attached; use ProgressRegistry.subscribe()
    # to obtain a fresh iterator over a snapshot.
    # P2-9: bounded queue — see ``EVENTS_QUEUE_MAXSIZE``.
    events: "queue.Queue[dict]" = field(
        default_factory=lambda: queue.Queue(maxsize=EVENTS_QUEUE_MAXSIZE)
    )
    # P2-9: 因队列满而被丢弃的事件数(丢最旧策略), 仅供观测。
    dropped_events: int = 0

    def emit(
        self,
        event: str,
        *,
        stage: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
        **extra: Any,
    ) -> None:
        payload: Dict[str, Any] = {
            "event": event,
            "ts": time.time(),
            "stage": stage or self.stage,
            "current": current if current is not None else self.stage_current,
            "total": total if total is not None else self.stage_total,
            "stage_index": self.stage_index,
            "cancelled": self.cancelled,
            "finished": self.finished,
        }
        payload.update(extra)
        if _put_bounded(self.events, payload):
            self.dropped_events += 1

    def start_stage(self, stage: str, total: int) -> None:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        self.stage = stage
        self.stage_index = STAGES.index(stage)
        self.stage_current = 0
        self.stage_total = total
        self.emit("stage_start", stage=stage, current=0, total=total)

    def advance(self, current: int) -> None:
        self.stage_current = current
        self.emit("stage_progress", current=current, total=self.stage_total)

    def finish_stage(self) -> None:
        self.stage_current = self.stage_total
        self.emit("stage_done", current=self.stage_total, total=self.stage_total)

    def cancel(self) -> None:
        self.cancelled = True
        # v3.2.0c-fix: cancelled is a terminal state — finished must be
        # True so the WS loop (``while not job.finished``), REST
        # ``/api/jobs/{id}/result`` (``finished`` field) and
        # ``ProgressRegistry.purge`` (filters on ``j.finished``) all
        # observe the job as done. Previously ``finished`` stayed
        # False forever for cancelled jobs, causing the WS loop to
        # spin forever on non-browser clients and the registry to
        # leak memory.
        self.finished = True
        self.emit("cancelled")

    def fail(self, error: str) -> None:
        self.error = error
        self.finished = True
        self.emit("failed", error=error)

    def succeed(self, result: Dict[str, Any]) -> None:
        self.result = result
        self.finished = True
        self.emit("succeeded")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "url": self.url,
            "stage": self.stage,
            "stage_index": self.stage_index,
            "stage_current": self.stage_current,
            "stage_total": self.stage_total,
            "cancelled": self.cancelled,
            "finished": self.finished,
            "error": self.error,
        }


class ProgressRegistry:
    """A thread-safe registry of :class:`JobProgress` instances.

    Single-process only — restart wipes the in-memory state.  The
    WebSocket layer in ``mediascribe.web.app`` wires this onto
    ``app.state.jobs``.
    """

    def __init__(self) -> None:
        self._jobs: Dict[str, JobProgress] = {}
        self._lock = threading.Lock()

    def create(self, url: str) -> JobProgress:
        job = JobProgress(job_id=uuid.uuid4().hex, url=url)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Optional[JobProgress]:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job.cancel()
        return True

    def list(self) -> List[JobProgress]:
        with self._lock:
            return list(self._jobs.values())

    def purge(self, older_than_seconds: float = 3600) -> int:
        cutoff = time.time() - older_than_seconds
        with self._lock:
            victims = [
                j.job_id for j in self._jobs.values()
                if j.finished and j.created_at < cutoff
            ]
            for k in victims:
                self._jobs.pop(k, None)
        return len(victims)


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


def with_progress(
    pipeline: Any,
    job: JobProgress,
    url: str,
    *,
    out_path: Any = None,
) -> Callable[[], Any]:
    """Run ``pipeline.transcribe(url, ...)`` while emitting stage events.

    Returns a thunk that the caller invokes from a worker thread.
    The function checks :attr:`JobProgress.cancelled` between stages
    and raises :class:`JobCancelled` if the user cancelled.
    """
    def _check_cancel() -> None:
        if job.cancelled:
            raise JobCancelled(f"job {job.job_id} cancelled")

    def runner() -> Any:
        try:
            # 1. download
            job.start_stage("download", total=1)
            _check_cancel()
            # We can't introspect real download progress from the
            # pipeline today, so we report 0→1 with the pipeline
            # doing the actual work inside the call.
            job.advance(1)
            # 2. extract_audio + 3. transcribe happen together in
            # the pipeline.transcribe() call.  We model them as a
            # single "transcribe" stage with progress emitted
            # before/after.
            job.start_stage("transcribe", total=1)
            _check_cancel()
            kwargs: Dict[str, Any] = {}
            if out_path is not None:
                kwargs["output"] = out_path
            result = pipeline.transcribe(url, **kwargs)
            job.advance(1)
            job.finish_stage()
            # 4. merge (a no-op today; emit a stage for forward-compat)
            job.start_stage("merge", total=1)
            job.advance(1)
            job.finish_stage()
            job.succeed({
                "engine": getattr(result, "engine", None),
                "out_path": str(out_path) if out_path else None,
            })
        except JobCancelled:
            # v3.2.0c-note: ``cancelled_done`` is emitted for direct
            # ``job.events`` subscribers (e.g. tests, in-process
            # consumers) as the "worker has acknowledged the cancel"
            # signal.  Note that WS clients (``/ws/progress/{id}``)
            # will NOT receive this event — ``cancel()`` now sets
            # ``finished=True`` synchronously, so the WS loop exits
            # right after delivering ``cancelled`` and never drains
            # the subsequent ``cancelled_done``.  This is intentional;
            # the ``cancelled`` event already conveys terminal state.
            job.emit("cancelled_done")
            raise
        except Exception as exc:  # noqa: BLE001
            job.fail(f"{exc.__class__.__name__}: {exc}")
            raise
        return result

    return runner


class JobCancelled(Exception):
    """Raised by :func:`with_progress` when the job was cancelled mid-run."""
