"""后台任务执行与结果写回。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
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
    store: Any = None,
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
        # P1-3(2026-11-20 评审): 终态写 SQLite 任务表 — 成果(out_path/
        # engine/title)在重启与 1h purge 后仍可取回。放在 results_lock 外,
        # SQLite 有自己的锁。
        if store is not None:
            try:
                store.mark_terminal(
                    job_id,
                    finished=True,
                    out_path=str(out_path),
                    engine=engine,
                    title=title,
                )
            except sqlite3.OperationalError:
                # 预期内:workspace 已被清理(测试/停机) → mode=rw 连接失败,
                # best-effort 持久层安静放弃,不刷日志。
                logger.debug("job store gone before terminal write (job=%s)", job_id)
            except Exception:  # pragma: no cover - defensive
                logger.warning("job store terminal write failed (job=%s)", job_id, exc_info=True)
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
        if store is not None:
            try:
                store.mark_terminal(job_id, finished=True, error=f"{exc.__class__.__name__}: {exc}")
            except sqlite3.OperationalError:
                logger.debug("job store gone before error write (job=%s)", job_id)
            except Exception:  # pragma: no cover - defensive
                logger.warning("job store error write failed (job=%s)", job_id, exc_info=True)


def _run_job_safely(
    runner: Callable[[], Any],
    job_id: str,
    out_path: Path,
    url: str,
    results_store: Dict[str, Dict[str, Any]],
    results_lock: "threading.Lock",
    registry: Any,
    vault: Any = None,
    store: Any = None,
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
        vault=vault,
        store=store,
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
    store: Any = None,
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
        job = registry.get(victim_jid) if victim_jid else None
        if job is None or getattr(job, "finished", False):
            continue
        exc = getattr(r, "exc", None)
        if isinstance(exc, PipelineCancelled):
            job.cancel()
            if store is not None:
                try:
                    store.mark_terminal(victim_jid, finished=True, cancelled=True)
                except sqlite3.OperationalError:
                    logger.debug("job store gone before cancel write (job=%s)", victim_jid)
                except Exception:  # pragma: no cover - defensive
                    logger.warning(
                        "job store cancel write failed (job=%s)", victim_jid, exc_info=True
                    )
        else:
            msg = f"{type(exc).__name__}: {exc}"
            job.fail(msg)
            if store is not None:
                try:
                    store.mark_terminal(victim_jid, finished=True, error=msg)
                except sqlite3.OperationalError:
                    logger.debug("job store gone before fail write (job=%s)", victim_jid)
                except Exception:  # pragma: no cover - defensive
                    logger.warning(
                        "job store fail write failed (job=%s)", victim_jid, exc_info=True
                    )
