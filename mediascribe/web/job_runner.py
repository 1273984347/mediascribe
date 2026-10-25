"""后台任务执行与结果写回。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


def _store_job_result(
    runner: Callable[[], Any],
    job_id: str,
    out_path: Path,
    url: str,
    results_store: Dict[str, Dict[str, Any]],
    results_lock: "threading.Lock",
    registry: Any,
    vault: Any = None,
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
        md = ""
        if out_path.exists():
            md = out_path.read_text(encoding="utf-8", errors="replace")
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
        # P3-1 + R6-fix: registry check is INSIDE the lock so a
        # concurrent /api/health purge (which also holds this lock,
        # see /api/health) cannot mutate the registry between the
        # check and the write — closing the TOCTOU window that would
        # otherwise let the worker resurrect an orphan entry.
        with results_lock:
            if registry.get(job_id) is None:
                # Job was purged while we were running; don't write.
                return
            results_store[job_id] = {
                "markdown": md,
                "engine": engine,
                "title": title,
                "url": url,
                "wiki": wiki_info,
            }
    except Exception as exc:  # pragma: no cover - defensive
        # ``with_progress`` already marks the job as failed via
        # ``job.fail(...)``; we just record the failure here so the
        # REST ``/api/jobs/{id}/result`` endpoint can surface it.
        # P3-1 + R6-fix: same TOCTOU-closing pattern on exception path.
        # P2-8: per-URL 错误信息保留给提交者(下载失败等用户输入错误),
        # 完整异常落服务端日志。
        logger.exception("job %s failed (url=%s)", job_id, url[:200])
        with results_lock:
            if registry.get(job_id) is None:
                return
            results_store[job_id] = {
                "markdown": "",
                "engine": None,
                "title": None,
                "url": url,
                "error": f"{exc.__class__.__name__}: {exc}",
            }


def _run_job_safely(
    runner: Callable[[], Any],
    job_id: str,
    out_path: Path,
    url: str,
    results_store: Dict[str, Dict[str, Any]],
    results_lock: "threading.Lock",
    registry: Any,
    vault: Any = None,
) -> None:
    """遗留单任务提交路径的薄封装 — 委托 :func:`_store_job_result`。

    v3.2.0e:批量提交已改走 :func:`_run_batch_job`(``AsyncPipeline.run_batch``),
    此处保留以兼容单 URL 直接 ``ThreadPoolExecutor.submit`` 的调用方。
    """
    _store_job_result(
        runner, job_id, out_path, url, results_store, results_lock, registry, vault=vault
    )


def _run_batch_job(
    ap: Any,
    urls: List[str],
    runners: Dict[str, Callable[[], Any]],
    job_ids: List[str],
    async_pipelines: Dict[str, Any],
) -> None:
    """整批提交路径:在独立线程跑 ``AsyncPipeline.run_batch``。

    * GPU 感知并发 + 单视频失败隔离 + 可取消(见 ``cancel_job`` / ws_progress)。
    * 每个 URL 的 ``runners[url]`` 是 ``with_progress`` + ``_store_job_result``
      封装的带进度 thunk。
    * 批处理结束后清理 ``async_pipelines`` 中本批 job_id 注册(该字典即
      ``app.state.async_pipelines``,由 submit_jobs 传入),避免注册表泄漏。
    """
    try:
        asyncio.run(ap.run_batch(urls, runners=runners))
    except Exception:
        # P2-7: 不再裸吞 BaseException — KeyboardInterrupt / SystemExit
        # 照常传播; 普通异常记入服务端日志(各 job 自身状态已反映结果)。
        logger.exception("batch job failed")
    finally:
        for jid in job_ids:
            async_pipelines.pop(jid, None)
