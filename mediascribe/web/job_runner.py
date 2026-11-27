"""后台任务执行与结果写回。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, cast

from mediascribe.web.job_control import JobControl

logger = logging.getLogger(__name__)


def _record_terminal_event(
    events: Any,
    *,
    result: str,
    task_id: Optional[str],
    error_code: Any = None,
    stage: Any = None,
    engine: Any = None,
    model: Any = None,
) -> None:
    if events is None or task_id is None:
        return
    try:
        events.record_finished(
            result=result,
            task_id=task_id,
            error_code=error_code,
            stage=stage,
            engine=engine,
            model=model,
        )
    except Exception:
        logger.debug("transcription_finished event recording failed", exc_info=True)


def _store_job_result(
    runner: Callable[[], Any],
    job_id: str,
    out_path: Path,
    url: str,
    results_store: Dict[str, Dict[str, Any]],
    results_lock: "threading.Lock",
    registry: Any,
    control: JobControl,
    vault: Any = None,
    events: Any = None,
) -> None:
    """执行 ``runner()`` 并把结果安全写入 ``results_store``。

    ``runner`` 由调用方构造(可包含 :func:`mediascribe.progress.with_progress`
    进度包装)。所有写操作都在 ``results_lock`` 内,且先校验 job 仍在
    ``registry`` 中(关闭 TOCTOU 窗口)。

    v3.2.0c-fix (P3-1): If the job has been purged from ``registry``
    between ``runner()`` finishing and the result-store write (e.g. by
    a concurrent ``/api/health`` purge on a long-running job older
    than 1 hour), the write is skipped — otherwise we would create an
    orphan entry that ``/api/health`` has just cleaned.
    v3.2.0c-fix (P3-2): All writes to ``results_store`` are guarded by
    ``results_lock`` so the /api/health iteration in another thread
    cannot raise ``RuntimeError: dictionary changed size during
    iteration``.
    v3.2.0c-fix (R6): The ``registry.get(job_id)`` check is performed
    INSIDE ``results_lock`` (same lock ``/api/health`` acquires for
    purge + orphan cleanup), so a concurrent purge cannot mutate the
    registry between the check and the write — closing the TOCTOU
    window that P3-1 alone left open. Any code that mutates
    ``app.state.jobs`` (purge, future DELETE endpoints) MUST hold
    ``results_lock`` to preserve this invariant.
    """
    try:
        result = runner()
        from mediascribe.pipeline_stages import ResultWriteError, validate_transcript_result

        try:
            md = out_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ResultWriteError(str(exc)) from exc
        validate_transcript_result(result, md)
        engine = getattr(result, "engine", None)
        meta = getattr(result, "metadata", None) or {}
        title = meta.get("title")
        # Karpathy 式知识库归档 (Phase 1) — 失败绝不影响任务结果,
        # 只记服务端日志并继续无 wiki 信息的结果写回。
        wiki_info = None
        if vault is not None and out_path.exists():
            try:
                wiki_info = vault.archive_transcript(out_path, meta)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("wiki 归档失败 (job=%s): %r", job_id, exc)
        control.finish(
            job_id,
            status="succeeded",
            result={"out_path": str(out_path)},
            engine=engine,
            model=getattr(result, "model", None),
            title=title,
            out_path=out_path,
        )
        # P3-1 + R6-fix: registry check is INSIDE the lock so a
        # concurrent /api/health purge (which also holds this lock,
        # see /api/health) cannot mutate the registry between the
        # check and the write — closing the TOCTOU window that would
        # otherwise let the worker resurrect an orphan entry.
        with results_lock:
            if registry.get(job_id) is None:
                return
            results_store[job_id] = {
                "markdown": md,
                "engine": engine,
                "model": getattr(result, "model", None),
                "title": title,
                "url": url,
                "input": getattr(getattr(result, "source", None), "display_name", url),
                "duration_seconds": getattr(result, "duration_seconds", None),
                "artifact_path": str(out_path),
                "artifact": {
                    "name": out_path.name,
                    "url": f"/api/jobs/{job_id}/artifact",
                },
                "wiki": wiki_info,
            }
        try:
            control.store.delete_segments(job_id)
        except Exception:
            logger.warning(
                "segment cleanup failed after durable success (job=%s)", job_id, exc_info=True
            )
        _record_terminal_event(
            events,
            result="success",
            task_id=job_id,
            engine=engine,
            model=getattr(result, "model", None),
        )
    except Exception as exc:  # pragma: no cover - defensive
        from mediascribe.pipeline_stages import NoSpeechError, PipelineCancelled
        from mediascribe.progress import JobCancelled

        snapshot = control.snapshot(job_id) or {}
        if (
            isinstance(exc, (JobCancelled, PipelineCancelled))
            or snapshot.get("cancelled")
            or snapshot.get("cancel_requested")
        ):
            control.finish(job_id, status="cancelled")
            _record_terminal_event(events, result="cancelled", task_id=job_id)
            return
        # ``with_progress`` only propagates failures; this runner commits the
        # terminal state through ``JobControl`` below.
        # P3-1 + R6-fix: same TOCTOU-closing pattern on exception path.
        # P2-8: per-URL 错误信息保留给提交者(下载失败等用户输入错误),
        # 完整异常落服务端日志。
        from mediascribe.recovery import recovery_for_exception

        logger.exception("job %s failed (url=%s)", job_id, url[:200])
        failed_job = registry.get(job_id)
        if failed_job is None:
            return
        recovery = getattr(failed_job, "recovery", None) or recovery_for_exception(exc)
        status = (
            "no_speech"
            if isinstance(exc, NoSpeechError) or recovery["code"] == "NO_SPEECH"
            else "failed"
        )
        control.finish(
            job_id,
            status=status,
            error=recovery["reason"],
            error_code=recovery["code"],
            recovery=recovery,
        )
        with results_lock:
            if registry.get(job_id) is None:
                return
            results_store[job_id] = {
                "markdown": "",
                "engine": None,
                "title": None,
                "url": url,
                "error": recovery["reason"],
                "error_code": recovery["code"],
                "recovery": recovery,
            }
        _record_terminal_event(
            events,
            result=status,
            task_id=job_id,
            error_code=recovery["code"],
        )


def _run_job_safely(
    runner: Callable[[], Any],
    job_id: str,
    out_path: Path,
    url: str,
    results_store: Dict[str, Dict[str, Any]],
    results_lock: "threading.Lock",
    registry: Any,
    control: JobControl,
    vault: Any = None,
    events: Any = None,
) -> None:
    """遗留单任务提交路径的薄封装 — 委托 :func:`_store_job_result`。

    v3.2.0e:批量提交已改走 :func:`_run_batch_job`(``AsyncPipeline.run_batch``),
    此处保留以兼容单 URL 直接 ``ThreadPoolExecutor.submit`` 的调用方。
    """
    _store_job_result(
        runner,
        job_id,
        out_path,
        url,
        results_store,
        results_lock,
        registry,
        control=control,
        vault=vault,
        events=events,
    )


def _run_batch_job(
    ap: Any,
    urls: List[str],
    runners: Dict[str, Callable[[], Any]],
    job_ids: List[str],
    async_pipelines: Dict[str, Any],
    *,
    registry: Any = None,
    job_ids_by_src: Dict[str, str] | None = None,
    job_sources: Dict[str, str] | None = None,
    cancel_events: "Dict[str, threading.Event] | None" = None,
    control: JobControl,
    events: Any = None,
) -> None:
    """整批提交路径:在独立线程跑 ``AsyncPipeline.run_batch``。

    * GPU 感知并发 + 单视频失败隔离 + 可取消(见 ``cancel_job`` / ws_progress)。
    * 每个 URL 的 ``runners[url]`` 是 ``with_progress`` + ``_store_job_result``
      封装的带进度 thunk。
    * 批处理结束后清理 ``async_pipelines`` / ``job_sources`` 中本批 job_id
      注册(由 submit_jobs 传入),避免注册表泄漏。
    * P0-2(2026-11-20 评审修复):批次结束后把 ``_FailedResult`` 映射回
      registry **终态** —— 被 cancel 打断的**排队**任务此前永远停在
      ``finished=False``(``/api/jobs`` 永远列出、UI 卡片悬挂、purge 只清
      finished 永不清 → 僵尸任务 + 内存泄漏)。runner 已跑过的任务由
      ``with_progress`` / ``_store_job_result`` 自己置终态,这里只补缺口;
      ``job.finished`` 已 True 的一律跳过(幂等)。
    """
    results: Any = None
    try:
        results = asyncio.run(ap.run_batch(urls, runners=runners, cancel_events=cancel_events))
    except Exception:
        # P2-7: 不再裸吞 BaseException — KeyboardInterrupt / SystemExit
        # 照常传播; 普通异常记入服务端日志(各 job 自身状态已反映结果)。
        logger.exception("batch job failed")
    finally:
        for jid in job_ids:
            async_pipelines.pop(jid, None)
            if job_sources is not None:
                job_sources.pop(jid, None)
    if results is None or registry is None or not job_ids_by_src:
        return
    from mediascribe.pipeline_async import _FailedResult
    from mediascribe.pipeline_stages import PipelineCancelled

    for r in results:
        if not isinstance(r, _FailedResult):
            continue
        src = getattr(r, "source", None) or ""
        victim_jid = job_ids_by_src.get(src)
        if victim_jid is None:
            continue
        job = registry.get(victim_jid)
        if job is None or getattr(job, "finished", False):
            continue
        exc = getattr(r, "exc", None)
        if isinstance(exc, PipelineCancelled):
            control.finish(victim_jid, status="cancelled")
            _record_terminal_event(events, result="cancelled", task_id=victim_jid)
        else:
            from mediascribe.recovery import recovery_for_exception

            recovery = recovery_for_exception(cast(BaseException, exc))
            from mediascribe.pipeline_stages import NoSpeechError

            status = (
                "no_speech"
                if isinstance(exc, NoSpeechError) or recovery["code"] == "NO_SPEECH"
                else "failed"
            )
            control.finish(
                victim_jid,
                status=status,
                error=recovery["reason"],
                error_code=recovery["code"],
                recovery=recovery,
            )
            _record_terminal_event(
                events,
                result=status,
                task_id=victim_jid,
                error_code=recovery["code"],
            )
