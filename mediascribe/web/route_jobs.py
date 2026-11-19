"""后台任务路由 —— 提交 / 取消 / 结果, 及其共享小工具。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import functools
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, cast

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


from mediascribe.web.job_runner import _run_batch_job, _store_job_result
from mediascribe.web.models import TranscribeRequest
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


def _pipeline_init_error(exc: Exception) -> HTTPException:
    """pipeline 初始化失败的统一 500 响应(细节只落服务端日志)。"""
    return HTTPException(
        status_code=500,
        detail="internal error while initialising the pipeline; see server logs",
    )


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
        registry = _job_registry(app)
        cancelled = registry.cancel(job_id)
        if not cancelled:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        ap = app.state.async_pipelines.get(job_id)
        if ap is not None and hasattr(ap, "cancel_one"):
            try:
                src = getattr(app.state, "job_sources", {}).get(job_id, "")
                ap.cancel_one(src)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("AsyncPipeline.cancel_one 异常: %r", exc)
        store = getattr(app.state, "job_store", None)
        if store is not None:
            try:
                store.mark_terminal(job_id, finished=True, cancelled=True)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("JobStore.mark_terminal(cancel) 异常: %r", exc)
        return {"job_id": job_id, "cancelled": True}

    @app.get("/api/jobs/{job_id}", dependencies=[_depends(_require_api_token)])
    def job_status(job_id: str) -> Dict[str, Any]:
        """Return the current snapshot of a job's progress."""
        registry = _job_registry(app)
        job = registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return job.to_dict()

    @app.get("/api/jobs", dependencies=[_depends(_require_api_token)])
    def list_jobs() -> Dict[str, Any]:
        """List the jobs this server still knows about (newest first).

        The Web UI persists ``job_id`` in ``localStorage`` and calls this on
        page load to restore the queue.  Without it a browser refresh drops
        every card while the jobs keep running server-side — the user loses
        the transcript even though the work completed.

        Only live registry entries are returned; ``/api/health`` purges
        finished jobs older than one hour, so this naturally shrinks over
        time.  Each entry carries the fields the UI needs to rebuild a card:
        ``job_id`` / ``url`` / ``stage`` / ``finished`` / ``cancelled``.
        """
        registry = _job_registry(app)
        # ``ProgressRegistry`` 用 dict 存储 ⇒ ``list()`` 返回的就是**插入
        # 顺序**(= 创建顺序)。倒过来即「最新在前」。
        # 不用 created_at 排序: Windows 时钟分辨率约 15ms, 同一批提交的
        # 多个 job 时间戳会相同 ⇒ 排序结果不确定。
        jobs = list(reversed(registry.list()))
        entries = [
            {
                "job_id": job.job_id,
                "url": job.url,
                "stage": job.stage,
                "stage_index": job.stage_index,
                "stage_current": job.stage_current,
                "stage_total": job.stage_total,
                "finished": job.finished,
                "cancelled": job.cancelled,
                "error": job.error,
                "created_at": job.created_at,
            }
            for job in jobs
        ]
        # P1-3(2026-11-20 评审): 合并 SQLite 历史 — 重启后内存注册表为空,
        # 历史任务(含上次进程遗留、已标 lost 的)从任务表补出,UI 刷新后
        # 卡片不再蒸发。live 态优先,store 里同 job_id 的旧行跳过。
        store = getattr(app.state, "job_store", None)
        if store is not None:
            try:
                history_rows = store.list()
            except sqlite3.OperationalError:
                logger.debug("job store gone before list")
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("job store list failed: %r", exc)
                history_rows = []
            live_ids = {e["job_id"] for e in entries}
            for row in history_rows:
                if row["job_id"] in live_ids:
                    continue
                entries.append(
                    {
                        "job_id": row["job_id"],
                        "url": row["url"],
                        "stage": row.get("stage"),
                        "stage_index": None,
                        "stage_current": None,
                        "stage_total": None,
                        "finished": bool(row.get("finished")),
                        "cancelled": bool(row.get("cancelled")),
                        "error": row.get("error"),
                        "created_at": row.get("created_at"),
                    }
                )
        return {"jobs": entries}


def _register_submit_jobs_route(app: "FastAPI", workspace: Path, pipeline_factory) -> None:
    """Route registrar — create_app 装配时调用(2026-10-03 审查拆分, 路由逐字搬移)。"""

    @app.post(
        "/api/jobs",
        dependencies=[_depends(_require_api_token), _depends(_require_json_content_type)],
    )
    def submit_jobs(
        req: TranscribeRequest,
        request: Request,
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
        _enforce_rate_limit(app.state.rate_limiter, request)
        if not req.urls:
            raise HTTPException(status_code=400, detail="urls must be non-empty")
        # P2-6: 去重保序 — 重复 URL 复用同一 job。
        urls = list(dict.fromkeys(req.urls))
        # P1-2 + P1-3: SSRF / 本地路径校验 — 私网 URL 与 workspace 外
        # 本地路径在提交入口拒绝。
        try:
            _validate_submitted_urls(urls, workspace)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            pipeline = pipeline_factory(req, workspace)
        except Exception as exc:
            # P2-8: 内部异常细节不回显客户端, 落服务端日志。
            logger.exception("pipeline init failed")
            raise _pipeline_init_error(exc) from exc
        from mediascribe.pipeline_async import AsyncPipeline
        from mediascribe.progress import with_progress

        registry = _job_registry(app)
        out_dir = workspace / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        # v3.2.0e wiring: 整批走一个 AsyncPipeline.run_batch,获得 GPU 感知并发
        # + 单视频失败隔离 + 可取消(见 cancel_job / ws_progress)。每个 URL 的
        # runner 用 with_progress 包装以保留进度事件与落盘,再套 _store_job_result
        # 安全写回 job_results。
        ap = AsyncPipeline(pipeline)
        runners: Dict[str, Callable[[], Any]] = {}
        jobs_out: List[Dict[str, Any]] = []
        job_ids: List[str] = []
        # P1-3(2026-11-20 评审): SQLite 任务表 — 提交即登记,终态由
        # _store_job_result / _run_batch_job 写回;重启后成果仍可取回。
        job_store = getattr(app.state, "job_store", None)
        # P0-2: url→job_id 与 job_id→url 双向映射 — 前者给 _run_batch_job
        # 做 _FailedResult→registry 终态映射(消灭僵尸任务),后者给
        # cancel_job 定位 per-URL 取消目标。
        job_ids_by_src: Dict[str, str] = {}
        sources_by_jid: Dict[str, str] = {}
        for url in urls:
            job = registry.create(url)
            job_ids_by_src[url] = job.job_id
            sources_by_jid[job.job_id] = url
            out_path = out_dir / f"{int(time.time())}_{uuid.uuid4().hex[:8]}.md"
            if job_store is not None:
                try:
                    job_store.upsert(job.job_id, url, out_path=str(out_path))
                except sqlite3.OperationalError:
                    logger.debug("job store gone before upsert (job=%s)", job.job_id)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("job store upsert failed (job=%s): %r", job.job_id, exc)
            wp = with_progress(pipeline, job, url, out_path=out_path)
            runners[url] = functools.partial(
                _store_job_result,
                wp,
                job.job_id,
                out_path,
                url,
                app.state.job_results,
                app.state.jobs_state_lock,
                registry,
                vault=app.state.wiki_vault,
                store=job_store,
            )
            jobs_out.append(
                {
                    "job_id": job.job_id,
                    "url": url,
                    "status": "queued",
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
            store=job_store,
        )
        return {
            "jobs": jobs_out,
            "requested": len(req.urls),
            "unique": len(urls),
            # 资源守卫的降档说明 —— 降档发生在 pipeline 构建时(= 提交这一刻)，
            # 所以**提交响应**就是最直接的告知时机，比事后读 metadata 更及时。
            "notices": list(getattr(pipeline, "_resource_notices", [])),
        }


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
        "finished": bool(row.get("finished")),
        "cancelled": bool(row.get("cancelled")),
        "error": row.get("error"),
        "markdown": markdown,
        "engine": row.get("engine"),
        "title": row.get("title"),
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
        stored = stored_copy
        return {
            "job_id": job_id,
            "finished": job.finished,
            "cancelled": job.cancelled,
            "error": job.error or (stored or {}).get("error"),
            "markdown": (stored or {}).get("markdown") if stored else None,
            "engine": (stored or {}).get("engine") if stored else None,
            "title": (stored or {}).get("title") if stored else None,
            "wiki": (stored or {}).get("wiki") if stored else None,
            "stage": job.stage,
            "stage_current": job.stage_current,
            "stage_total": job.stage_total,
        }
