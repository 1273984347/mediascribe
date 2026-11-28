"""后台任务路由 —— 提交 / 取消 / 结果, 及其共享小工具。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import functools
import json
import logging
import math
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, cast

try:
    from fastapi import Depends, HTTPException, Request
except ImportError:  # pragma: no cover - optional
    # fastapi 缺失时本模块仍要能导入(见 _depends 的说明)。
    Depends = HTTPException = Request = None  # type: ignore[assignment,misc]


def _depends(dependency):
    """惰性 ``Depends`` —— fastapi 缺失时原样返回依赖。

    ``Depends(...)`` 出现在**装饰器参数位置**, 会在模块导入期求值 ⇒
    直接 ``from fastapi import Depends`` 会让 fastapi 缺失时整个模块
    ImportError。改走这里, 本模块就能在无 fastapi 时独立导入
    (fastapi 缺失时 ``create_app()`` 会先抛 RuntimeError)。
    """
    if Depends is None:
        return dependency
    return Depends(dependency)


from mediascribe.i18n import t
from mediascribe.models import SourceRef
from mediascribe.pipeline_stages import TranscribeStage
from mediascribe.url_utils import match_platform
from mediascribe.web.job_runner import _run_batch_job, _store_job_result
from mediascribe.web.models import ArtifactEventRequest, TranscribeRequest
from mediascribe.web.ratelimit import _enforce_rate_limit
from mediascribe.web.security import _require_api_token, _require_json_content_type
from mediascribe.web.validation import _validate_submitted_urls

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查用
    from fastapi import FastAPI

    from mediascribe.progress import ProgressRegistry

logger = logging.getLogger(__name__)


def _job_registry(app: "FastAPI") -> "ProgressRegistry":
    """取当前 app 的任务注册表(app.state.jobs 的类型化访问)。

    函数内 :func:`importlib.import_module` 兼作可导入性探测 —
    ``mediascribe.progress`` 缺失时抛 ImportError, ws_progress 会
    把它转成 WS 错误事件后关闭连接。
    """
    import importlib

    importlib.import_module("mediascribe.progress")
    return cast("ProgressRegistry", app.state.jobs)


def _checkpoint_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Select checkpoint projection fields for list responses."""
    return {
        key: row.get(key)
        for key in (
            "last_segment_index",
            "last_segment_end",
            "committed_segments",
        )
    }


def _checkpoint_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "committed_segments": row.get("committed_segments", 0),
        "last_segment_index": row.get("last_segment_index", -1),
        "last_segment_end": row.get("last_segment_end", 0.0),
        "mode": row.get("checkpoint_mode"),
    }


def _resume_source_ref(raw_input: str) -> SourceRef:
    """Build a source reference from stored input without network resolution."""
    raw = raw_input.strip()
    if raw.startswith(("http://", "https://")):
        platform = match_platform(raw)
        kind = platform or "video"
        if kind == "wechat_mp" and not ("/s?" in raw or "/s/" in raw):
            kind = "video"
        return SourceRef(raw_input=raw, kind=kind, url=raw)
    path = Path(raw)
    suffix = path.suffix.lower()
    if path.exists() and suffix in {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}:
        return SourceRef(raw_input=raw, kind="audio", path=path)
    if path.exists() and suffix in {".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm", ".m4v"}:
        return SourceRef(raw_input=raw, kind="video", path=path)
    bv_match = re.search(r"BV[a-zA-Z0-9]{10}", raw)
    if bv_match:
        bv = bv_match.group(0)
        return SourceRef(
            raw_input=raw,
            kind="bilibili",
            bv=bv,
            url=f"https://www.bilibili.com/video/{bv}",
        )
    kind = "audio" if suffix in {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"} else "video"
    return SourceRef(raw_input=raw, kind=kind, path=path)


def _effective_resume_decode(pipeline: Any, request: Any, source: SourceRef) -> dict[str, Any]:
    """Compute the exact effective decode identity before creating a resume attempt."""
    from mediascribe.zh_normalize import simplified_initial_prompt

    language = request.language or pipeline.settings.language
    prompt = TranscribeStage.auto_prompt_for_source(source)
    effective_prompt = simplified_initial_prompt(prompt, language)
    return cast(
        dict[str, Any],
        pipeline.transcriber.effective_decode_params(
            language=language, initial_prompt=effective_prompt
        ),
    )


def _decoded_json_column(row: Dict[str, Any], key: str) -> Any:
    """Decode one optional JSON projection column for REST consumers."""
    raw = row.get(key)
    if isinstance(raw, str):
        return json.loads(raw) if raw else None
    return raw


def _pipeline_init_error(exc: Exception, *, extra_reason: str | None = None) -> HTTPException:
    """pipeline 初始化失败的统一错误响应。"""
    from mediascribe.recovery import format_recovery, recovery_for_exception

    recovery = recovery_for_exception(exc)
    message = format_recovery(exc)
    if extra_reason:
        message = f"{message}\n{extra_reason}"
    detail = {
        "error_code": recovery["code"],
        "error": message,
        "recovery": recovery,
    }
    if recovery["code"] in {
        "INPUT_INVALID",
        "FFMPEG_MISSING",
        "DEVICE_UNAVAILABLE",
        "OUTPUT_WRITE_FAILED",
        "MODEL_NOT_READY",
    }:
        return HTTPException(status_code=400, detail=detail)
    if recovery["code"] == "ENGINE_UNAVAILABLE":
        return HTTPException(status_code=503, detail=detail)
    return HTTPException(status_code=500, detail=detail)


def _register_job_control_routes(app: "FastAPI") -> None:
    """Route registrar — create_app 装配时调用(2026-10-03 审查拆分, 路由逐字搬移)。"""

    @app.post("/api/jobs/{job_id}/cancel", dependencies=[_depends(_require_api_token)])
    def cancel_job(job_id: str) -> Dict[str, Any]:
        """Cancel a running job (no-op if it doesn't exist).

        v3.2.0c Tier 1 — also cancels the registered :class:`AsyncPipeline`
        so in-flight ``asyncio.Task``s are torn down, not just the
        cooperative ``job.cancelled`` flag.

        P0-2(2026-11-20 评审修复):取消粒度收窄为**单任务** — API 契约
        (``POST /api/jobs/{id}/cancel`` 单数语义)此前被整批连坐破坏。
        现在只 ``cancel_one`` 本 job 对应的 URL:排队任务当场打断,
        运行中任务由 ``registry.cancel`` 置位的 job 标志在下一个 stage
        边界协作退出;兄弟任务不受任何影响。
        """
        result = cast(dict[str, Any], app.state.job_control.cancel(job_id))
        if result["status"] == "unknown":
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        if result["accepted"]:
            ap = app.state.async_pipelines.get(job_id)
            if ap is not None and hasattr(ap, "cancel_one"):
                try:
                    ap.cancel_one(app.state.job_sources.get(job_id, ""))
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("AsyncPipeline.cancel_one 异常: %r", exc)
        return result

    @app.get("/api/jobs/{job_id}", dependencies=[_depends(_require_api_token)])
    def job_status(job_id: str) -> Dict[str, Any]:
        """Return the current snapshot of a job's progress."""
        registry = _job_registry(app)
        job = registry.get(job_id)
        if job is None:
            job = app.state.job_control.hydrate(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return job.to_dict()

    @app.get("/api/jobs", dependencies=[_depends(_require_api_token)])
    def list_jobs(limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """List live and persisted jobs newest first (paged).

        The Web UI persists ``job_id`` in ``localStorage`` and calls this on
        page load to restore the queue. Durable history supplies terminal and
        interrupted snapshots after restart; live entries take precedence for
        active jobs. Each entry includes status, resume eligibility, and the
        checkpoint cursor needed to rebuild a card.

        Args:
            limit: Page size, clamped to ``1..200`` (out-of-range values
                are pinned to the nearest bound instead of raising). Defaults
                to 50 so a long history cannot stretch the first page load.
            offset: number of already-consumed entries to skip. Used by the
                UI's "load more history" button.

        Returns:
            ``{"jobs": [...], "total": int, "truncated": bool}`` — ``total``
            counts every job visible to this query (independent of
            ``limit``) and ``truncated`` reports whether entries remain
            beyond this page.

            Note: the durable history window caps the visible set at 200
            entries (``JobStore.list``'s own limit, unchanged by this
            parameter), so ``total`` never exceeds 200 for persisted jobs.
            Live jobs are not subject to that cap.
        """
        limit = max(1, min(200, limit))
        offset = max(0, offset)
        registry = _job_registry(app)
        # ``ProgressRegistry`` 用 dict 存储 ⇒ ``list()`` 返回的就是**插入
        # 顺序**(= 创建顺序)。倒过来即「最新在前」。
        # 不用 created_at 排序: Windows 时钟分辨率约 15ms, 同一批提交的
        # 多个 job 时间戳会相同 ⇒ 排序结果不确定。
        jobs = list(reversed(registry.list()))
        entries = [
            {
                "job_id": job.job_id,
                "attempt_id": job.attempt_id,
                "retry_of": job.retry_of,
                "url": job.url,
                "status": job.status,
                "stage": job.stage,
                "stage_index": job.stage_index,
                "stage_current": job.stage_current,
                "stage_total": job.stage_total,
                "progress_known": job.progress_known,
                "finished": job.finished,
                "cancelled": job.cancelled,
                "cancel_requested": job.cancel_requested,
                "error": (job.recovery or {}).get("reason") or job.error,
                "error_code": job.error_code,
                "recovery": job.recovery,
                "result": job.result,
                "resumable": job.resumable,
                "resume_block_reason": job.resume_block_reason,
                "last_segment_index": job.last_segment_index,
                "last_segment_end": job.last_segment_end,
                "committed_segments": job.committed_segments,
                "resume_count": job.resume_count,
                "checkpoint": {
                    "committed_segments": job.committed_segments,
                    "last_segment_index": job.last_segment_index,
                    "last_segment_end": job.last_segment_end,
                    "mode": job.checkpoint_mode,
                },
                "created_at": job.created_at,
            }
            for job in jobs
        ]
        # P1-3(2026-11-20 评审): 合并 SQLite 历史 — 重启后内存注册表为空,
        # 历史任务(含上次进程遗留、已标 interrupted 的)从任务表补出,UI
        # 刷新后卡片不再蒸发。live 态优先,store 里同 job_id 的旧行跳过。
        store = getattr(app.state, "job_store", None)
        if store is not None:
            try:
                history_rows = store.list()
            except sqlite3.OperationalError:
                logger.debug("job store gone before list")
                history_rows = []
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("job store list failed: %r", exc)
                history_rows = []
            rows_by_id = {row["job_id"]: row for row in history_rows}
            for entry in entries:
                row = rows_by_id.get(entry["job_id"])
                if row is not None:
                    entry.update(_checkpoint_fields(row))
                    if entry.get("recovery") is None:
                        entry["recovery"] = _decoded_json_column(row, "recovery_json")
                    if entry.get("result") is None:
                        entry["result"] = _decoded_json_column(row, "result_json")
            live_ids = {e["job_id"] for e in entries}
            for row in history_rows:
                if row["job_id"] in live_ids:
                    continue
                entries.append(
                    {
                        "job_id": row["job_id"],
                        "attempt_id": row.get("attempt_id"),
                        "retry_of": row.get("retry_of"),
                        "url": row["url"],
                        "status": row.get("status"),
                        "stage": row.get("stage"),
                        "stage_index": row.get("stage_index"),
                        "stage_current": row.get("stage_current"),
                        "stage_total": row.get("stage_total"),
                        "progress_known": bool(row.get("progress_known")),
                        "finished": bool(row.get("finished")),
                        "cancelled": bool(row.get("cancelled")),
                        "cancel_requested": bool(row.get("cancel_requested")),
                        "error": row.get("error"),
                        "error_code": row.get("error_code"),
                        "recovery": _decoded_json_column(row, "recovery_json"),
                        "result": _decoded_json_column(row, "result_json"),
                        "resumable": bool(row.get("resumable")),
                        "resume_block_reason": row.get("resume_block_reason"),
                        "resume_count": row.get("resume_count", 0),
                        "checkpoint": _checkpoint_payload(row),
                        "created_at": row.get("created_at"),
                        **_checkpoint_fields(row),
                    }
                )
        # 先把两份列表合并去重后的**完整**集合算出来, 再切片 ——
        # total 描述"一共匹配到多少条", 与 limit/offset 无关。
        total = len(entries)
        page = entries[offset : offset + limit]
        return {
            "jobs": page,
            "total": total,
            "truncated": total > len(page),
        }


def _register_submit_jobs_route(app: "FastAPI", workspace: Path, pipeline_factory) -> None:
    """Route registrar — create_app 装配时调用(2026-10-03 审查拆分, 路由逐字搬移)。"""

    def _submit_jobs(
        req: TranscribeRequest,
        *,
        retry_of: str | None = None,
        retry_output: Path | None = None,
        resume_of: str | None = None,
    ) -> Dict[str, Any]:
        """Submit URLs as background jobs; returns immediately with job_ids.

        Each URL is run via :func:`mediascribe.progress.with_progress` on
        the app's :class:`ThreadPoolExecutor`.  The caller should:

        1. ``GET /ws/progress/{job_id}`` (WebSocket) to receive
           ``stage_start`` / ``stage_progress`` / ``stage_done`` / ``succeeded``
           / ``failed`` / ``cancelled`` events.
        2. ``POST /api/jobs/{job_id}/cancel`` to abort a running job.
        3. ``GET /api/jobs/{job_id}/result`` to fetch the Markdown once
           the job reaches ``finished``.

        P2-6: 重复 URL 会去重(保留首次出现顺序)— 每个唯一 URL 只创建
        一个 job, 避免孤儿 job 与并发写同一 out_path。响应中的
        ``requested`` / ``unique`` 计数可用于感知是否发生了去重。

        The synchronous ``POST /api/transcribe`` endpoint is preserved
        for backwards compatibility with the Chrome extension and MCP.
        """
        control = app.state.job_control
        resume_snapshot = None
        if resume_of is not None:
            resume_snapshot = control.snapshot(resume_of)
            if resume_snapshot is None:
                raise HTTPException(status_code=404, detail=f"unknown job: {resume_of}")
            if resume_snapshot.get("status") != "interrupted" or not resume_snapshot.get(
                "resumable"
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "RECOVERY_RESUME_UNAVAILABLE",
                        "resume_block_reason": resume_snapshot.get("resume_block_reason")
                        or "RESUME_UNSUPPORTED",
                    },
                )
            urls = [resume_snapshot["url"]]
        else:
            if not req.urls:
                raise HTTPException(status_code=400, detail="urls must be non-empty")
            urls = list(dict.fromkeys(req.urls))
            # P1-2 + P1-3: SSRF / local-path validation applies only to new
            # submissions. Resume uses the already-validated durable input.
            try:
                _validate_submitted_urls(urls, workspace)
            except ValueError as exc:
                from mediascribe.inputs import PreflightError

                raise _pipeline_init_error(
                    PreflightError("INPUT_INVALID", str(exc)),
                    extra_reason=str(exc),
                ) from exc
        # P2-6(2026-11-20 评审): 磁盘空间守卫 —— 剩余不足 2×单文件下载上限
        # 即拒绝(507 Insufficient Storage),避免批量任务跑到一半盘满
        from mediascribe.platform_compat import free_disk_mb, min_free_disk_mb

        free_mb = free_disk_mb(workspace)
        needed_mb = min_free_disk_mb()
        if free_mb is not None and free_mb < needed_mb:
            raise HTTPException(
                status_code=507,
                detail=(
                    f"insufficient disk space on workspace volume: "
                    f"need ~{needed_mb}MB, free {free_mb}MB "
                    f"(tune MEDIASCRIBE_MIN_FREE_MB to override)"
                ),
            )
        try:
            pipeline = pipeline_factory(req, workspace)
        except Exception as exc:
            # P2-8: 内部异常细节不回显客户端, 落服务端日志。
            logger.exception("pipeline init failed")
            raise _pipeline_init_error(exc) from exc
        if resume_of is not None:
            assert resume_snapshot is not None
            if resume_snapshot.get("runtime_auth_required"):
                token = getattr(getattr(pipeline, "transcriber", None), "hf_token", None)
                if not token:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error_code": "RESUME_AUTH_REQUIRED",
                            "resume_block_reason": "RESUME_AUTH_REQUIRED",
                        },
                    )
        resume_plan = None
        resume_job = None
        resume_source = None
        if resume_of is not None:
            assert resume_snapshot is not None
            resume_source = _resume_source_ref(str(resume_snapshot["url"]))
            try:
                decode_params = _effective_resume_decode(pipeline, req, resume_source)
                resume_plan = control.resume(resume_of, decode_params=decode_params)
            except ValueError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"error_code": str(exc), "resume_block_reason": str(exc)},
                ) from exc
            except Exception as exc:
                logger.exception("resume decode preflight failed")
                raise _pipeline_init_error(exc) from exc
            resume_job = control.hydrate(resume_of)
        from mediascribe.pipeline_async import AsyncPipeline
        from mediascribe.progress import with_progress
        from mediascribe.web.route_events import _event_recorder

        registry = _job_registry(app)
        events = _event_recorder(app)
        out_dir = workspace / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        ap = AsyncPipeline(pipeline)
        runners: Dict[str, Callable[[], Any]] = {}
        jobs_out: List[Dict[str, Any]] = []
        job_ids: List[str] = []
        job_ids_by_src: Dict[str, str] = {}
        sources_by_jid: Dict[str, str] = {}
        cancel_events: Dict[str, threading.Event] = {}
        safe_request = {
            key: value for key, value in req.model_dump().items() if key != "wechat_cookies"
        }
        selection = {
            "engine": pipeline.settings.engine,
            "model": pipeline.settings.model,
            "device": pipeline.settings.device,
            "language": req.language,
            "vad_filter": getattr(pipeline.transcriber, "vad_filter", True),
            "resume_auth_required": bool(req.wechat_cookies),
        }

        from mediascribe.audio_utils import probe_duration_seconds
        from mediascribe.checkpoint import CheckpointCursor, capability_for
        from mediascribe.web.job_recovery import audio_sha256, decode_params_hash

        def _make_runner(
            job,
            out_path: Path,
            cancel_event: threading.Event,
            resume_plan=None,
            resume_snapshot=None,
        ):
            capability_fn = getattr(pipeline.transcriber, "checkpoint_capability", None)
            capability = (
                capability_fn()
                if callable(capability_fn)
                else capability_for(pipeline.settings.engine)
            )
            runtime_auth_required = bool(
                pipeline.settings.engine == "whisperx"
                and getattr(pipeline.transcriber, "diarization", False)
                and getattr(pipeline.transcriber, "hf_token", None)
            )
            checkpoint_cursor = None
            if resume_plan is not None:
                assert resume_snapshot is not None
                previous_segment = control.store.get_segment(
                    job.job_id, int(resume_snapshot["last_segment_index"])
                )
                checkpoint_cursor = CheckpointCursor(
                    mode=cast(Literal["segment", "window"], resume_plan.mode),
                    resume_start=resume_plan.resume_start,
                    last_segment_end=float(resume_snapshot["last_segment_end"]),
                    previous_text=(previous_segment or {}).get("text", ""),
                    next_window_index=resume_plan.next_window_index,
                    detected_language=resume_plan.detected_language,
                )
            audio_info: dict[str, object] = {}
            params_hash: str | None = None
            last_bucket = [-1]
            next_segment_index = resume_plan.next_segment_index if resume_plan is not None else None

            def maybe_set_checkpoint_identity() -> None:
                path = audio_info.get("path")
                fingerprint = audio_info.get("sha256")
                if (
                    isinstance(path, Path)
                    and isinstance(fingerprint, str)
                    and params_hash is not None
                ):
                    duration = audio_info.get("duration")
                    if (
                        isinstance(duration, bool)
                        or not isinstance(duration, (int, float))
                        or not math.isfinite(duration)
                        or duration <= 0
                    ):
                        raise RuntimeError("audio duration unavailable before checkpoint identity")
                    control.set_checkpoint_identity(
                        job.job_id,
                        audio_path=path,
                        audio_sha256=fingerprint,
                        decode_params_hash=params_hash,
                        capability=capability,
                        audio_duration=float(duration),
                        runtime_auth_required=runtime_auth_required,
                    )

            def advance_transcribe_progress(end: float) -> None:
                duration = audio_info.get("duration")
                if (
                    isinstance(duration, bool)
                    or not isinstance(duration, (int, float))
                    or not math.isfinite(duration)
                    or duration <= 0
                ):
                    raise RuntimeError("audio duration unavailable before progress update")
                bucket = min(99, int(end / duration * 100 // 5) * 5)
                snapshot = control.snapshot(job.job_id)
                if snapshot is None:
                    raise RuntimeError("job snapshot disappeared before progress update")
                current = int(snapshot["stage_current"]) if snapshot["stage"] == "transcribe" else 0
                if bucket > max(current, last_bucket[0]):
                    last_bucket[0] = bucket
                    control.update_stage(
                        job.job_id,
                        stage="transcribe",
                        current=bucket,
                        total=100,
                        known=True,
                    )

            def on_audio_ready(path: Path) -> None:
                audio_info["path"] = path
                audio_info["sha256"] = audio_sha256(path)
                audio_info["duration"] = probe_duration_seconds(path)
                maybe_set_checkpoint_identity()

            def on_decode_params(payload: dict[str, Any]) -> None:
                nonlocal params_hash
                params_hash = decode_params_hash(payload)
                if resume_plan is not None:
                    from mediascribe.web.job_recovery import validate_resume_decode

                    params_hash = validate_resume_decode(resume_plan, payload)
                maybe_set_checkpoint_identity()

            def on_segment(segment: dict[str, Any]) -> None:
                nonlocal next_segment_index
                path = audio_info.get("path")
                fingerprint = audio_info.get("sha256")
                if (
                    not isinstance(path, Path)
                    or not isinstance(fingerprint, str)
                    or params_hash is None
                ):
                    raise RuntimeError("ASR segment arrived before checkpoint identity")
                snapshot = control.snapshot(job.job_id)
                if snapshot is None:
                    raise RuntimeError("job snapshot disappeared before segment commit")
                segment_index = (
                    next_segment_index
                    if next_segment_index is not None
                    else int(snapshot["last_segment_index"]) + 1
                )
                control.commit_segment(
                    job.job_id,
                    attempt_id=job.attempt_id,
                    segment_index=segment_index,
                    start=float(segment["start"]),
                    end=float(segment["end"]),
                    text=str(segment["text"]),
                    engine=pipeline.settings.engine,
                    model=pipeline.settings.model,
                    decode_params_hash=params_hash,
                    words=segment.get("words"),
                )
                next_segment_index = segment_index + 1
                advance_transcribe_progress(float(segment["end"]))

            def prior_segments() -> list[dict]:
                segments = []
                for row in control.store.get_segments(job.job_id):
                    segment: dict[str, Any] = {
                        "text": row["text"],
                        "start": float(row["start"]),
                        "end": float(row["end"]),
                    }
                    raw = row.get("words_json")
                    if raw:
                        segment["words"] = json.loads(raw)
                    segments.append(segment)
                return segments

            def on_window_started(index: int, start: float, end: float) -> None:
                control.start_checkpoint_window(
                    job.job_id, window_index=index, start=start, end=end
                )

            def on_window_committed(
                index: int, start: float, end: float, detected_language: str | None
            ) -> None:
                control.commit_checkpoint_window(
                    job.job_id,
                    window_index=index,
                    start=start,
                    end=end,
                    detected_language=detected_language,
                )
                advance_transcribe_progress(end)

            def durable_progress(stage: str, percent: float) -> None:
                snapshot = control.snapshot(job.job_id)
                if (
                    stage == "transcribe"
                    and snapshot is not None
                    and snapshot["stage"] == "transcribe"
                    and snapshot["progress_known"]
                    and snapshot["stage_current"] > 0
                    and percent <= 0
                ):
                    return
                if percent <= 0:
                    current, known = 0, False
                elif percent >= 100:
                    current, known = 100, True
                else:
                    current, known = max(1, min(99, round(percent))), True
                if (
                    stage == "transcribe"
                    and snapshot is not None
                    and snapshot["stage"] == "transcribe"
                    and snapshot["progress_known"]
                ):
                    current = max(current, int(snapshot["stage_current"]))
                control.update_stage(
                    job.job_id,
                    stage=stage,
                    current=current,
                    total=100,
                    known=known,
                )

            wp = with_progress(
                pipeline,
                job,
                job.url,
                out_path=out_path,
                cancel_event=cancel_event,
                segment_cb=on_segment,
                audio_path_cb=on_audio_ready,
                decode_params_cb=on_decode_params,
                progress_sink=durable_progress,
                checkpoint_capability=capability,
                checkpoint_cursor=checkpoint_cursor,
                prior_segments_cb=prior_segments,
                checkpoint_window_started_cb=on_window_started,
                checkpoint_window_committed_cb=on_window_committed,
                resume_start=resume_plan.resume_start if resume_plan is not None else None,
                resume_source=resume_source,
                resume_audio_path=resume_plan.audio_path if resume_plan is not None else None,
            )

            def run() -> Any:
                from mediascribe.progress import JobCancelled

                current = control.hydrate(job.job_id)
                if current is not None and (current.cancelled or current.cancel_requested):
                    raise JobCancelled(f"job {job.job_id} cancelled")
                control.start_attempt(job.job_id)
                return wp()

            return run

        for url in urls:
            if resume_plan is not None:
                assert resume_job is not None
                job = resume_job
                out_path = resume_plan.output_path
            else:
                out_path = retry_output or out_dir / f"{int(time.time())}_{uuid.uuid4().hex[:8]}.md"
                job = control.submit(
                    url,
                    request=safe_request,
                    selection=selection,
                    out_path=out_path,
                    retry_of=retry_of,
                )
                app.state.job_requests[job.job_id] = {
                    "request": req.model_dump(),
                    "output_path": str(out_path),
                    "retry_job_id": None,
                }
            job_ids_by_src[url] = job.job_id
            sources_by_jid[job.job_id] = url
            evt = threading.Event()
            cancel_events[url] = evt
            wp = _make_runner(job, out_path, evt, resume_plan, resume_snapshot)
            runners[url] = functools.partial(
                _store_job_result,
                wp,
                job.job_id,
                out_path,
                url,
                app.state.job_results,
                app.state.jobs_state_lock,
                registry,
                control,
                vault=app.state.wiki_vault,
                events=events,
            )
            jobs_out.append(
                {
                    "job_id": job.job_id,
                    "attempt_id": job.attempt_id,
                    "retry_of": job.retry_of,
                    "url": url,
                    "status": job.status,
                    "ws": f"/ws/progress/{job.job_id}",
                    "result": f"/api/jobs/{job.job_id}/result",
                }
            )
            job_ids.append(job.job_id)
        # 注册 AP 到本批所有 job_id — cancel_job 按 job_id 找到 AP 后走
        # cancel_one(url) 只取消该 job(P0-2);_run_batch_job 结束时清理
        # 本批注册,避免注册表泄漏。
        job_sources: Dict[str, str] = getattr(app.state, "job_sources", {})
        for jid in job_ids:
            app.state.async_pipelines[jid] = ap
            job_sources[jid] = sources_by_jid[jid]
        for url, jid in zip(urls, job_ids, strict=True):
            try:
                events.record_input(
                    input_value=url,
                    selection_mode="explicit"
                    if req.engine is not None or req.model is not None
                    else "auto",
                    engine=pipeline.settings.engine,
                    model=pipeline.settings.model,
                    device=pipeline.settings.device,
                    task_id=jid,
                )
            except Exception:
                logger.debug("input_submitted event recording failed", exc_info=True)
        app.state.job_executor.submit(
            _run_batch_job,
            ap,
            urls,
            runners,
            job_ids,
            app.state.async_pipelines,
            registry=registry,
            job_ids_by_src=job_ids_by_src,
            job_sources=job_sources,
            cancel_events=cancel_events,
            control=control,
            events=events,
        )
        if resume_plan is not None:
            return {
                "job_id": resume_plan.job_id,
                "attempt_id": resume_plan.attempt_id,
                "status": "queued",
                "resume_start": resume_plan.resume_start,
                "next_segment_index": resume_plan.next_segment_index,
                "checkpoint_mode": resume_plan.mode,
                "next_window_index": resume_plan.next_window_index,
            }
        return {
            "jobs": jobs_out,
            "requested": len(req.urls),
            "unique": len(urls),
            # 资源守卫的降档说明 —— 降档发生在 pipeline 构建时(= 提交这一刻)，
            # 所以**提交响应**就是最直接的告知时机，比事后读 metadata 更及时。
            "notices": list(getattr(pipeline, "_resource_notices", [])),
        }

    @app.post(
        "/api/jobs",
        dependencies=[_depends(_require_api_token), _depends(_require_json_content_type)],
    )
    def submit_jobs(
        req: TranscribeRequest,
        request: Request,
    ) -> Dict[str, Any]:
        _enforce_rate_limit(app.state.rate_limiter, request)
        return _submit_jobs(req)

    @app.post("/api/jobs/{job_id}/resume", dependencies=[_depends(_require_api_token)])
    def resume_job(job_id: str, request: Request):
        _enforce_rate_limit(app.state.rate_limiter, request)
        snapshot = app.state.job_control.snapshot(job_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        if snapshot.get("status") != "interrupted" or not snapshot.get("resumable"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "RECOVERY_RESUME_UNAVAILABLE",
                    "resume_block_reason": snapshot.get("resume_block_reason")
                    or "RESUME_UNSUPPORTED",
                },
            )
        stored_request = json.loads(snapshot.get("request_json") or "{}")
        return _submit_jobs(TranscribeRequest(**stored_request), resume_of=job_id)

    @app.post("/api/jobs/{job_id}/retry", dependencies=[_depends(_require_api_token)])
    def retry_job(job_id: str, request: Request) -> Dict[str, Any]:
        _enforce_rate_limit(app.state.rate_limiter, request)
        registry = _job_registry(app)
        job = registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        with app.state.jobs_state_lock:
            recovery = job.recovery or (app.state.job_results.get(job_id) or {}).get("recovery")
            if (
                not job.finished
                or job.cancelled
                or not job.error
                or not recovery
                or not recovery.get("retryable")
            ):
                raise HTTPException(status_code=409, detail=t("RECOVERY_RETRY_UNAVAILABLE"))
            snapshot = app.state.job_requests.get(job_id)
            if snapshot is None:
                raise HTTPException(status_code=409, detail=t("RECOVERY_RETRY_UNAVAILABLE"))
            if snapshot.get("retry_job_id") is not None:
                raise HTTPException(status_code=409, detail=t("RECOVERY_RETRY_UNAVAILABLE"))
            snapshot["retry_job_id"] = "submitting"
        try:
            retry_request = TranscribeRequest(**snapshot["request"])
            result = _submit_jobs(
                retry_request,
                retry_of=job_id,
                retry_output=Path(snapshot["output_path"]),
            )
        except Exception:
            with app.state.jobs_state_lock:
                snapshot["retry_job_id"] = None
            raise
        retry_id = result["jobs"][0]["job_id"]
        with app.state.jobs_state_lock:
            snapshot["retry_job_id"] = retry_id
        try:
            from mediascribe.web.route_events import _event_recorder

            _event_recorder(app).record_recovery(
                action="retry",
                task_id=job_id,
                retry_task_id=retry_id,
                error_code=recovery.get("code"),
            )
        except Exception:
            logger.debug("recovery_action_selected event recording failed", exc_info=True)
        return result


def _result_from_store(app: "FastAPI", job_id: str) -> Dict[str, Any]:
    """P1-3: 内存注册表未命中时的回源路径(重启 / 1h purge 之后仍可取回)。

    从 SQLite 任务表读 out_path,文件还在就直接读出 markdown ——
    此前这两条路径会让 result 端点 404,成果明明已落盘却无人指路。
    """
    store = getattr(app.state, "job_store", None)
    row = None
    if store is not None:
        try:
            row = store.get(job_id)
        except sqlite3.OperationalError:
            logger.debug("job store gone before get (job=%s)", job_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("job store get failed (job=%s): %r", job_id, exc)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    markdown = None
    out_path = row.get("out_path")
    if out_path:
        p = Path(out_path)
        if p.exists():
            markdown = p.read_text(encoding="utf-8", errors="replace")
    return {
        "job_id": job_id,
        "attempt_id": None,
        "retry_of": None,
        "finished": bool(row.get("finished")),
        "cancelled": bool(row.get("cancelled")),
        "error": row.get("error"),
        "error_code": None,
        "recovery": None,
        "markdown": markdown,
        "engine": row.get("engine"),
        "title": row.get("title"),
        "status": "no_speech"
        if row.get("error") == "NO_SPEECH"
        else "failed"
        if row.get("error")
        else "finished",
        "summary": {
            "input": row.get("title"),
            "engine": row.get("engine"),
            "model": None,
            "duration_seconds": None,
        },
        "artifact": {
            "name": Path(out_path).name,
            "url": f"/api/jobs/{job_id}/artifact",
        }
        if out_path
        else None,
        "wiki": None,
        "stage": row.get("stage"),
        "stage_current": None,
        "stage_total": None,
    }


def _register_job_result_route(app: "FastAPI") -> None:
    """Route registrar — create_app 装配时调用(2026-10-03 审查拆分, 路由逐字搬移)。"""

    @app.get("/api/jobs/{job_id}/result", dependencies=[_depends(_require_api_token)])
    def job_result(job_id: str) -> Dict[str, Any]:
        """Return the markdown + metadata for a job.

        Returns ``finished=False`` until the job reaches a terminal
        state.  After completion, returns ``markdown`` (string) and
        optional ``engine`` / ``title``.  Cancelled jobs return
        ``cancelled=True`` with ``markdown=None``.

        P1-3(2026-11-20 评审): 内存未命中时回源 SQLite 任务表 + out_path
        文件 —— 服务重启或 1h purge 后成果仍可取回。
        """
        registry = _job_registry(app)
        job = registry.get(job_id)
        if job is None:
            return _result_from_store(app, job_id)
        # v3.2.0c-fix (P3-2): hold the lock while reading so a
        # concurrent /api/health purge cannot pop the entry mid-read.
        with app.state.jobs_state_lock:
            stored = app.state.job_results.get(job_id)
            stored_copy = dict(stored) if stored else None
            retry_job_id = (app.state.job_requests.get(job_id) or {}).get("retry_job_id")
        stored = stored_copy
        error_code = job.error_code or (stored or {}).get("error_code")
        status = "no_speech" if error_code == "NO_SPEECH" else "failed" if job.error else "finished"
        return {
            "job_id": job_id,
            "attempt_id": job.attempt_id,
            "retry_of": job.retry_of,
            "retry_job_id": retry_job_id,
            "finished": job.finished,
            "cancelled": job.cancelled,
            "status": status,
            "error_code": error_code,
            "recovery": job.recovery or (stored or {}).get("recovery"),
            "error": (job.recovery or {}).get("reason") or job.error or (stored or {}).get("error"),
            "markdown": (stored or {}).get("markdown") if stored else None,
            "engine": (stored or {}).get("engine") if stored else None,
            "title": (stored or {}).get("title") if stored else None,
            "summary": {
                "input": (stored or {}).get("input"),
                "engine": (stored or {}).get("engine"),
                "model": (stored or {}).get("model"),
                "duration_seconds": (stored or {}).get("duration_seconds"),
            },
            "artifact": (stored or {}).get("artifact"),
            "wiki": (stored or {}).get("wiki") if stored else None,
            "stage": job.stage,
            "stage_current": job.stage_current,
            "stage_total": job.stage_total,
        }

    @app.get("/api/jobs/{job_id}/artifact", dependencies=[_depends(_require_api_token)])
    def job_artifact(job_id: str):
        """Open a completed Markdown artifact without exposing its server path."""
        from fastapi.responses import FileResponse

        registry = _job_registry(app)
        job = registry.get(job_id)
        if job is None:
            row = getattr(app.state, "job_store", None)
            row = row.get(job_id) if row is not None else None
            if row is None:
                raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
            artifact_path = Path(row["out_path"]) if row.get("out_path") else None
        else:
            with app.state.jobs_state_lock:
                stored = dict(app.state.job_results.get(job_id) or {})
            artifact_path = Path(stored["artifact_path"]) if stored.get("artifact_path") else None
        if artifact_path is None or not artifact_path.is_file():
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(
            artifact_path,
            media_type="text/markdown",
            filename=artifact_path.name,
            content_disposition_type="inline",
        )

    @app.post(
        "/api/jobs/{job_id}/events",
        status_code=204,
        dependencies=[
            _depends(_require_api_token),
            _depends(_require_json_content_type),
        ],
    )
    def job_artifact_event(job_id: str, payload: ArtifactEventRequest):
        """Record a content-free local artifact interaction event."""
        from fastapi import Response

        registry = _job_registry(app)
        job = registry.get(job_id)
        if job is None or not job.finished or job.error or job.cancelled:
            raise HTTPException(status_code=404, detail="completed artifact not found")
        from mediascribe.web.route_events import _event_recorder

        _event_recorder(app).record_artifact(
            action=payload.action,
            task_id=job_id,
        )
        return Response(status_code=204)
