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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .checkpoint import CheckpointCapability, CheckpointCursor
from .models import SourceRef

STAGES = ("preflight", "model_prepare", "download", "extract_audio", "transcribe", "assemble")

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
    attempt_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    retry_of: Optional[str] = None
    cancelled: bool = False
    finished: bool = False
    stage: Optional[str] = None
    stage_index: int = 0
    stage_current: int = 0
    stage_total: int = 0
    progress_known: bool = False
    error: Optional[str] = None
    error_code: Optional[str] = None
    recovery: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    status: str = "queued"
    resumable: bool = False
    resume_block_reason: Optional[str] = None
    cancel_requested: bool = False
    last_segment_index: int = -1
    last_segment_end: float = 0.0
    committed_segments: int = 0
    resume_count: int = 0
    checkpoint_mode: Optional[str] = None
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
            "progress_known": self.progress_known,
            "cancelled": self.cancelled,
            "finished": self.finished,
        }
        payload.update(extra)
        if _put_bounded(self.events, payload):
            self.dropped_events += 1

    def start_stage(self, stage: str, total: int, *, known: bool = True) -> None:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        self.stage = stage
        self.stage_index = STAGES.index(stage)
        self.stage_current = 0
        self.stage_total = total
        self.progress_known = known
        self.emit("stage_start", stage=stage, current=0, total=total)

    def advance(self, current: int) -> None:
        self.stage_current = current
        self.emit("stage_progress", current=current, total=self.stage_total)

    def finish_stage(self) -> None:
        self.stage_current = self.stage_total
        self.emit("stage_done", current=self.stage_total, total=self.stage_total)

    def cancel(self) -> None:
        if self.finished or self.cancelled:
            return
        self.cancelled = True
        self.emit("cancel_requested")

    def cancel_done(self) -> None:
        self.cancelled = True
        if not self.finished:
            self.finished = True
            self.emit("cancelled")

    def fail(
        self,
        error: str,
        *,
        error_code: Optional[str] = None,
        recovery: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self.cancelled:
            self.cancel_done()
            return
        self.error = error
        self.error_code = error_code
        self.recovery = recovery
        self.finished = True
        safe_error = recovery.get("reason") if recovery else None
        self.emit("failed", error=safe_error or error, error_code=error_code, recovery=recovery)

    def succeed(self, result: Dict[str, Any]) -> None:
        if self.cancelled:
            self.cancel_done()
            return
        self.result = result
        self.finished = True
        self.emit("succeeded")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
            "retry_of": self.retry_of,
            "url": self.url,
            "status": self.status,
            "stage": self.stage,
            "stage_index": self.stage_index,
            "stage_current": self.stage_current,
            "stage_total": self.stage_total,
            "progress_known": self.progress_known,
            "cancelled": self.cancelled,
            "finished": self.finished,
            "error": (self.recovery or {}).get("reason") or self.error,
            "error_code": self.error_code,
            "recovery": self.recovery,
            "result": self.result,
            "resumable": self.resumable,
            "resume_block_reason": self.resume_block_reason,
            "cancel_requested": self.cancel_requested,
            "last_segment_index": self.last_segment_index,
            "last_segment_end": self.last_segment_end,
            "committed_segments": self.committed_segments,
            "resume_count": self.resume_count,
            "checkpoint": {
                "committed_segments": self.committed_segments,
                "last_segment_index": self.last_segment_index,
                "last_segment_end": self.last_segment_end,
                "mode": self.checkpoint_mode,
            },
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

    def upsert(self, snapshot: Dict[str, Any]) -> JobProgress:
        """Restore a durable snapshot without replaying historical events."""
        with self._lock:
            job = self._jobs.get(snapshot["job_id"])
            if job is None:
                job = JobProgress(job_id=snapshot["job_id"], url=snapshot["url"])
                self._jobs[job.job_id] = job
        for key in (
            "attempt_id",
            "retry_of",
            "status",
            "stage",
            "stage_index",
            "stage_current",
            "stage_total",
            "progress_known",
            "cancelled",
            "finished",
            "error",
            "error_code",
            "recovery",
            "result",
            "resumable",
            "resume_block_reason",
            "cancel_requested",
            "last_segment_index",
            "last_segment_end",
            "committed_segments",
            "resume_count",
            "checkpoint_mode",
        ):
            if key in snapshot:
                setattr(job, key, snapshot[key])
        if "created_at" in snapshot:
            job.created_at = float(snapshot["created_at"])
        return job

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
                j.job_id for j in self._jobs.values() if j.finished and j.created_at < cutoff
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
    cancel_event: Optional[threading.Event] = None,
    segment_cb: Optional[Callable[[dict[str, Any]], None]] = None,
    resume_start: Optional[float] = None,
    resume_source: Optional[SourceRef] = None,
    resume_audio_path: Optional[Path] = None,
    audio_path_cb: Optional[Callable[[Path], None]] = None,
    decode_params_cb: Optional[Callable[[dict[str, Any]], None]] = None,
    progress_sink: Optional[Callable[[str, float], None]] = None,
    checkpoint_capability: Optional[CheckpointCapability] = None,
    checkpoint_cursor: Optional[CheckpointCursor] = None,
    prior_segments_cb: Optional[Callable[[], list[dict[str, Any]]]] = None,
    checkpoint_window_started_cb: Optional[Callable[[int, float, float], None]] = None,
    checkpoint_window_committed_cb: Optional[
        Callable[[int, float, float, str | None], None]
    ] = None,
) -> Callable[[], Any]:
    """Run ``pipeline.transcribe(url, ...)`` while emitting stage events.

    Returns a thunk that the caller invokes from a worker thread.
    The function checks :attr:`JobProgress.cancelled` between stages
    and raises :class:`JobCancelled` if the user cancelled.

    R-1(2026-11-21 架构评审): ``cancel_event``(与 ``AsyncPipeline.cancel_one``
    共享同一 per-URL Event)在两处生效 —— ① stage 边界预检;② 作为
    ``cancel_event`` 透传给 ``pipeline.transcribe``,让转录 stage 内部的
    GPU 账本等待点与 stage 边界都能协作退出,取消不再空转到自然结束。
    """

    def _check_cancel() -> None:
        if job.cancelled or (cancel_event is not None and cancel_event.is_set()):
            raise JobCancelled(f"job {job.job_id} cancelled")

    def _on_progress(stage: str, percent: float) -> None:
        if stage not in STAGES:
            return
        if progress_sink is not None:
            progress_sink(stage, percent)
        if percent <= 0:
            if job.stage != stage or job.stage_current > 0:
                job.start_stage(stage, total=1, known=False)
            return
        if percent >= 100:
            if job.stage != stage:
                job.start_stage(stage, total=1, known=False)
            if job.stage_current < job.stage_total:
                job.advance(1)
                job.finish_stage()
            return
        if job.stage != stage or not job.progress_known or job.stage_total != 100:
            job.start_stage(stage, total=100, known=True)
        job.advance(max(1, min(100, round(percent))))

    def runner() -> Any:
        try:
            _check_cancel()
            kwargs: Dict[str, Any] = {"progress_cb": _on_progress}
            if out_path is not None:
                kwargs["output"] = out_path
            if cancel_event is not None:
                kwargs["cancel_event"] = cancel_event
            if segment_cb is not None:
                kwargs["segment_cb"] = segment_cb
            if resume_start is not None:
                kwargs["resume_start"] = resume_start
            if resume_source is not None:
                kwargs["resume_source"] = resume_source
            if resume_audio_path is not None:
                kwargs["resume_audio_path"] = resume_audio_path
            if audio_path_cb is not None:
                kwargs["audio_path_cb"] = audio_path_cb
            if decode_params_cb is not None:
                kwargs["decode_params_cb"] = decode_params_cb
            if checkpoint_capability is not None:
                kwargs["checkpoint_capability"] = checkpoint_capability
            if checkpoint_cursor is not None:
                kwargs["checkpoint_cursor"] = checkpoint_cursor
            if prior_segments_cb is not None:
                kwargs["prior_segments_cb"] = prior_segments_cb
            if checkpoint_window_started_cb is not None:
                kwargs["checkpoint_window_started_cb"] = checkpoint_window_started_cb
            if checkpoint_window_committed_cb is not None:
                kwargs["checkpoint_window_committed_cb"] = checkpoint_window_committed_cb
            result = pipeline.transcribe(url, **kwargs)
            _check_cancel()
            from mediascribe.pipeline_stages import (
                ResultWriteError,
                validate_transcript_result,
            )

            artifact_path = Path(out_path or result.transcript_path)
            try:
                markdown = artifact_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise ResultWriteError(str(exc)) from exc
            validate_transcript_result(result, markdown)
        except JobCancelled:
            raise
        except Exception:
            raise
        return result

    return runner


class JobCancelled(Exception):
    """Raised by :func:`with_progress` when the job was cancelled mid-run."""


# ---------------------------------------------------------------------------
# 批处理进度条 + ETA(原 ``douyin_batch/progress.py``, 2026-10-03 审查收编;
# 2026-10-24 全量整理时旧转发壳已删除。与上面的 ProgressRegistry 服务
# 不同场景: 前者面向 CLI 批处理的终端渲染, 后者面向 web/WS 的结构化事件。)
# ---------------------------------------------------------------------------


class ProgressTracker:
    """进度跟踪器 - 带ETA估计"""

    def __init__(self, total: int, desc: str = "处理中"):
        self.total = total
        self.desc = desc
        self.completed = 0
        self.failed = 0
        self.start_time = time.time()
        self.task_times: list[float] = []  # 每个任务的耗时

    def update(self, success: bool = True, task_time: Optional[float] = None):
        """更新进度"""
        self.completed += 1
        if not success:
            self.failed += 1
        if task_time is not None:
            self.task_times.append(task_time)

    def get_eta(self) -> float:
        """估算剩余时间（秒）"""
        if not self.task_times:
            return 0.0

        avg_time = sum(self.task_times) / len(self.task_times)
        remaining = self.total - self.completed
        return avg_time * remaining

    def get_elapsed(self) -> float:
        """已用时间（秒）"""
        return time.time() - self.start_time

    def render(self, current_task: str = "") -> str:
        """渲染进度条"""
        percent = (self.completed / self.total) * 100 if self.total > 0 else 0
        filled = int(percent / 2)  # 50字符宽度
        bar = "█" * filled + "░" * (50 - filled)

        elapsed = self.get_elapsed()
        eta = self.get_eta()

        elapsed_str = format_duration(elapsed)
        eta_str = format_duration(eta) if eta > 0 else "--:--"

        current = f" | {current_task}" if current_task else ""

        return (
            f"\r{self.desc}: |{bar}| "
            f"{self.completed}/{self.total} ({percent:.0f}%) "
            f"[{elapsed_str}<{eta_str}]"
            f" ✅{self.completed - self.failed} ❌{self.failed}{current}"
        )


def format_duration(seconds: float) -> str:
    """格式化时长为 HH:MM:SS 或 MM:SS"""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds:02d}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"
    else:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


def format_size(bytes_size: float) -> str:
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_size < 1024:
            return f"{bytes_size:.1f}{unit}"
        bytes_size /= 1024
    return f"{bytes_size:.1f}TB"
