"""Web app assembly — extracted from ``mediascribe/web/app.py`` (second split, 2026-10-25).

``app.py`` keeps the **public facade**: a thin ``create_app`` shell plus
re-exports of every historical public name; the real assembly logic
(state, lifespan, CORS, route registration) lives here.

Why another split
-----------------
After T03 took app.py from 2088 lines to 515, the remainder was:
docstring 58 + re-export imports 72 + ``__all__`` 57 + ``create_app`` 156
+ other 112. The **only remaining big block was ``create_app``** (156
lines, 30%) — extracting it leaves app.py as a pure facade.

Two constraints that must not break
-----------------------------------
1. **The ``_FASTAPI_AVAILABLE`` check must stay in app.py** — the test
   ``test_create_app_raises_when_fastapi_missing`` sets
   ``app._FASTAPI_AVAILABLE = False`` then expects ``create_app()`` to
   raise RuntimeError. If the check moved into this module it would read
   **another module's** flag ⇒ the patch stops working. This module's
   imports therefore sit **inside ``create_app``'s body** (without
   fastapi, ``import mediascribe.web.assembly`` itself would ImportError).
2. **``pipeline_factory`` is injected by app.py** — it must look up the
   app module global ``_build_pipeline`` **at call time** (that global is
   the tests' patch target); this module only forwards it.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from mediascribe.web.cors import _resolve_cors_origins
from mediascribe.web.ratelimit import _build_rate_limiter
from mediascribe.web.route_events import _register_event_routes
from mediascribe.web.route_extension import _register_extension_routes
from mediascribe.web.route_health import _register_health_routes
from mediascribe.web.route_jobs import (
    _register_job_control_routes,
    _register_job_result_route,
    _register_submit_jobs_route,
)
from mediascribe.web.route_pages import _register_page_routes
from mediascribe.web.route_transcribe import _register_transcribe_route
from mediascribe.web.route_wiki import _register_wiki_routes
from mediascribe.web.route_ws import _register_ws_progress_route
from mediascribe.web.ws_bridge import _JobEventBridge

logger = logging.getLogger(__name__)


def _positive_int_env(*names: str, default: int) -> int:
    """按顺序取第一个非空且为正整数的 env;全无效时回退默认值。

    供 P1-6 拆名后的 env 别名链使用(新名优先,旧名兼容)。
    """
    for name in names:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if value > 0:
            return value
    return default


def build_app(
    *,
    workspace: Optional[Path],
    api_token: Optional[str],
    cors_origins: Optional[List[str]],
    app_version: str,
    pipeline_factory: Any,
):
    """Assemble and return a FastAPI application.

    This is the body of the original ``create_app``, moved verbatim.
    app.py's thin shell handles the fastapi availability check, version
    resolution and pipeline factory injection, then calls here.

    Args:
        workspace: Directory where transcripts and audio land
            (None ⇒ read env vars / default directory).
        api_token: Optional shared secret (None ⇒ read
            ``MEDIASCRIBE_API_TOKEN``).
        cors_origins: Explicitly allowed origins (None ⇒ built-in
            defaults or env vars).
        app_version: App version (resolved by app.py from the single
            source of truth).
        pipeline_factory: Pipeline factory (injected by app.py; looks up
            the app module global ``_build_pipeline`` at call time ⇒
            test patches take effect).
    """
    # fastapi 是可选依赖 —— 顶层 import 会让本模块在无 fastapi 时
    # ImportError(而不是优雅降级)。它在下面只于**函数体内**用到, 故在此处导入。
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    # v3.2.0c-fix: release worker threads + GPU memory on shutdown.
    # Without this the ``ThreadPoolExecutor`` threads outlive the app
    # (it is the only deviation in the codebase — every other usage
    # wraps the executor in a ``with`` block) and may write to a
    # tempdir that's already been deleted.  ``cancel_futures=True``
    # abandons not-yet-started submissions; running tasks cooperate
    # via the ``job.cancelled`` flag between stages.
    @asynccontextmanager
    async def _lifespan(app: "FastAPI"):
        yield
        executor = getattr(app.state, "job_executor", None)
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("executor 关闭异常: %r", exc)
        # P2-9: 停机时叫停所有 WS bridge drain 线程。
        for bridge in list(getattr(app.state, "ws_bridges", {}).values()):
            bridge.close()
        # P1-3: 停机时收 SQLite 任务表连接(Windows 上不关会让文件句柄
        # 挂住 workspace 目录的删除/移动)。
        job_store = getattr(app.state, "job_store", None)
        if job_store is not None:
            try:
                job_store.close()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("job store close 异常: %r", exc)

    app = FastAPI(
        title="MediaScribe Web UI",
        version=app_version,
        description="Transcribe videos to Markdown from your browser.",
        lifespan=_lifespan,
    )

    # CORS — pre-allow extension + local origins; the user can override.
    if api_token is None:
        api_token = os.environ.get("MEDIASCRIBE_API_TOKEN", "").strip() or None
    if cors_origins is None:
        cors_origins, cors_regex = _resolve_cors_origins()
    else:
        cors_regex = None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_origin_regex=cors_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    # Persist the active token on app.state for tests / introspection.
    app.state.api_token_configured = bool(api_token)
    # Rate limiter: per-client, sliding-window.  Default = 10 req / 60 s.
    # Disable by setting ``MEDIASCRIBE_RATE_LIMIT=0``.
    app.state.rate_limiter = _build_rate_limiter()
    # Job progress registry (v3.2.0a) — backs ``/ws/progress/{job_id}``
    # and the ``/api/jobs/{job_id}`` REST helpers.
    from mediascribe.progress import ProgressRegistry

    app.state.jobs = ProgressRegistry()
    # v3.2.0c Tier 1: background job runner — ThreadPoolExecutor for
    # ``with_progress`` thunks + a results store keyed by job_id.
    # The executor lives for the lifetime of the app; on shutdown the
    # pending tasks are abandoned (cooperative cancel inside
    # ``with_progress`` checks ``job.cancelled`` between stages).
    # P1-6 拆名(2026-11-20 评审):本值语义 = **并发批次数**(每个批次
    # 占一个线程,批内并发由 MEDIASCRIBE_BATCH_CONCURRENCY 控制)。
    # 旧名 MEDIASCRIBE_MAX_WORKERS 作兼容别名;旧实现一名两义,调大它会
    # 同时放大批次数与批内并发、相乘放大 GPU 占用。
    # 默认 2：转录是 GPU/IO 密集型，过多并发反而争抢显存。
    _max_workers = _positive_int_env(
        "MEDIASCRIBE_JOB_EXECUTORS", "MEDIASCRIBE_MAX_WORKERS", default=2
    )
    app.state.job_executor = ThreadPoolExecutor(
        max_workers=_max_workers, thread_name_prefix="v2t-job"
    )
    # app.state 属性不能在赋值处写类型标注(mypy: Type cannot be declared
    # in assignment to non-self attribute), 类型标注提升到局部变量再挂载。
    job_results: Dict[str, Dict[str, Any]] = {}
    app.state.job_results = job_results
    # v3.2.0c-fix (P3-2): lock guarding ``app.state.job_results`` against
    # concurrent mutation between the worker thread (``_run_job_safely``)
    # and the /api/health iteration+pop in the request thread. Without
    # this, /api/health's ``[k for k in app.state.job_results if ...]``
    # could raise ``RuntimeError: dictionary changed size during
    # iteration`` when a worker writes between iterations.
    #
    # v3.2.0c-fix (R2-F4 rename): the original name ``job_results_lock``
    # under-described the lock's actual scope. Per the R6 invariant
    # declared on ``_run_job_safely``, this lock guards BOTH
    # ``app.state.job_results`` (markdown dict) AND ``app.state.jobs``
    # (ProgressRegistry mutate paths — purge, future DELETE endpoints).
    # The renamed ``jobs_state_lock`` reflects that expanded duty so
    # future maintainers do not assume a narrower "results-only" scope
    # and add unsynchronised registry mutations.
    app.state.jobs_state_lock = threading.Lock()
    # Optional mapping from job_id → AsyncPipeline (for future
    # async-wired cancellations).  Populated by callers that wrap
    # Pipeline in AsyncPipeline; empty by default.
    async_pipelines: Dict[str, Any] = {}
    app.state.async_pipelines = async_pipelines
    # P2-9: job_id → WS event bridge (resident drain thread per job).
    ws_bridges: Dict[str, _JobEventBridge] = {}
    app.state.ws_bridges = ws_bridges
    app.state.ws_bridge_lock = threading.Lock()
    # P0-2(2026-11-20 评审): job_id → 提交 URL,cancel_job 用它定位
    # AsyncPipeline.cancel_one(url) 的 per-URL 取消目标;批次结束由
    # _run_batch_job 清理本批条目。(app.state 属性不能在赋值处写类型
    # 标注 —— mypy 限制,局部变量声明后挂载。)
    job_sources: Dict[str, str] = {}
    app.state.job_sources = job_sources
    job_requests: Dict[str, Dict[str, Any]] = {}
    app.state.job_requests = job_requests
    from mediascribe.events import EventRecorder
    from mediascribe.events.identity import event_retention_status
    from mediascribe.events.schema import is_internal_test

    app.state.events_enabled = not is_internal_test() and event_retention_status()["enabled"]
    app.state.event_recorder = EventRecorder(surface="web")

    # 附加修复: Docker 镜像已设 ``MEDIASCRIBE_WORKSPACE=/workspace`` 且
    # compose 已挂卷, 但代码此前不读该变量, 导致容器内数据不落卷。
    if workspace is None:
        env_ws = os.environ.get("MEDIASCRIBE_WORKSPACE", "").strip()
        workspace = Path(env_ws) if env_ws else Path.cwd() / "web-workspace"

    # P1-3(2026-11-20 评审): SQLite 任务表 — 任务态不再 100% 进程内存。
    # 启动时把上次进程遗留的 unfinished 任务标记为 interrupted(重启可观测,
    # 不自动续跑);/api/jobs 合并历史;/api/jobs/{id}/result 未命中内存时回源
    # 本表 + out_path 文件 —— 重启与 1h purge 之后成果仍可取回。
    from mediascribe.web.job_control import JobControl
    from mediascribe.web.job_store import JobStore

    job_store = JobStore(workspace / "jobs.sqlite3")
    app.state.job_store = job_store
    app.state.job_control = JobControl(job_store, app.state.jobs)
    app.state.job_control.recover_interrupted()

    # Karpathy 式知识库 vault (mediascribe.wiki) — 每次任务成功后
    # 把转写稿归档进 <workspace>/vault/; MEDIASCRIBE_WIKI=0 停用。
    # 配置了 MEDIASCRIBE_LLM_* 时, 概念提取自动生成 wiki/概念 - X.md。
    from mediascribe.wiki import ConceptExtractor, vault_for_workspace

    app.state.wiki_vault = vault_for_workspace(workspace, extractor=ConceptExtractor.from_env())

    _register_health_routes(app)
    _register_extension_routes(app)
    _register_page_routes(app)
    _register_wiki_routes(app)
    _register_transcribe_route(app, workspace, pipeline_factory)
    _register_ws_progress_route(app)
    _register_job_control_routes(app)
    _register_submit_jobs_route(app, workspace, pipeline_factory)
    _register_job_result_route(app)
    _register_event_routes(app)
    return app
