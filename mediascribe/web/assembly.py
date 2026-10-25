"""Web 应用装配 —— 从 ``mediascribe/web/app.py`` 拆出(2026-10-25 二次拆分)。

``app.py`` 保留**公共门面**：「薄壳」``create_app`` + 全部历史公共名字的
re-export；真正的装配逻辑(状态、lifespan、CORS、路由注册)在这里。

为什么再拆一层
--------------
T03 把 app.py 从 2088 行降到 515 行后，剩下的构成是：docstring 58 + re-export
import 72 + ``__all__`` 57 + ``create_app`` 156 + 其它 112。**唯一还大的一块
就是 ``create_app``**（156 行，占 30%）—— 把它抽走，app.py 就只剩门面。

两条不能破的约束
----------------
1. **``_FASTAPI_AVAILABLE`` 的检查必须留在 app.py** —— 测试
   ``test_create_app_raises_when_fastapi_missing`` 会把
   ``app._FASTAPI_AVAILABLE`` 置 False 再调 ``create_app()`` 期望 RuntimeError；
   检查若搬进本模块，读的就是**另一个模块**的标志 ⇒ patch 失效。
   本模块的 import 也因此放在 ``create_app`` **函数体内**（无 fastapi 时
   ``import mediascribe.web.assembly`` 本身就会 ImportError）。
2. **``pipeline_factory`` 由 app.py 注入** —— 它要在**调用时**查 app 模块全局的
   ``_build_pipeline``（那里有测试 patch 目标），本模块只负责转发。
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


def build_app(
    *,
    workspace: Optional[Path],
    api_token: Optional[str],
    cors_origins: Optional[List[str]],
    app_version: str,
    pipeline_factory: Any,
):
    """装配并返回一个 FastAPI 应用。

    这是原 ``create_app`` 的主体，逐字搬移。``app.py`` 的薄壳负责
    fastapi 可用性检查、版本号解析与 pipeline 工厂注入, 然后调到这里。

    Args:
        workspace: 转写稿与音频的落盘目录（None ⇒ 读环境变量/默认目录）。
        api_token: 可选共享密钥（None ⇒ 读 ``MEDIASCRIBE_API_TOKEN``）。
        cors_origins: 显式允许的来源（None ⇒ 用内置默认或环境变量）。
        app_version: 应用版本号（由 app.py 从单一真源解析后传入）。
        pipeline_factory: 构造 Pipeline 的工厂（app.py 注入，调用时才查
            app 模块全局的 ``_build_pipeline`` ⇒ 测试 patch 生效）。
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
    # 并发度可通过环境变量覆盖（参考 AsyncPipeline._default_max_concurrent）。
    # 默认 2：转录是 GPU/IO 密集型，过多并发反而争抢显存。
    try:
        _max_workers = int(os.environ.get("MEDIASCRIBE_MAX_WORKERS", "2"))
    except ValueError:
        _max_workers = 2
    if _max_workers <= 0:
        _max_workers = 2
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

    # 附加修复: Docker 镜像已设 ``MEDIASCRIBE_WORKSPACE=/workspace`` 且
    # compose 已挂卷, 但代码此前不读该变量, 导致容器内数据不落卷。
    if workspace is None:
        env_ws = os.environ.get("MEDIASCRIBE_WORKSPACE", "").strip()
        workspace = Path(env_ws) if env_ws else Path.cwd() / "web-workspace"

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
    return app
