"""
Web UI for MediaScribe (FastAPI + vanilla HTML).

A minimal browser interface that lets a non-technical user paste
one or more URLs and get back Markdown transcriptions.  The page
calls ``/api/transcribe`` for each URL; the server runs the same
``Pipeline`` that the CLI uses and returns the resulting markdown
inline.  No persistent storage on the server — the markdown is
shown in a copyable textbox and can be downloaded with one click.

This module is *optional*: it depends on ``fastapi`` and ``uvicorn``,
which are NOT hard dependencies.  Install them via
``pip install mediascribe[web]``.

Usage:
    uvicorn mediascribe.web.app:app --reload --port 8000
    # or
    python -m mediascribe.web.app --port 8000

Endpoints:
    GET  /            → HTML page
    GET  /api/health  → JSON liveness probe (always public)
    POST /api/transcribe
         body: {"urls": ["..."], "engine": "whisper", "model": "small",
                "language": "zh", "ocr_engine": "auto",
                "wechat_cookies": "..."}
         returns: {"results": [{"url": "...", "markdown": "...",
                                  "engine": "...", "ok": true}, ...]}

Security:
    * CORS: extension origins and the local dashboard are pre-allowed
      by default.  Set ``MEDIASCRIBE_CORS_ORIGINS`` to a comma-separated
      list to override.  ``*`` is also accepted.
    * Auth: if ``MEDIASCRIBE_API_TOKEN`` is set, every API request
      (except ``/api/health`` and ``/``) must carry
      ``Authorization: Bearer <token>``.  The same token is required on
      the ``/ws/progress/{job_id}`` WebSocket handshake via
      ``?token=`` (or the first ``Sec-WebSocket-Protocol`` entry).
      The token is loaded once at startup; rotate by restarting the
      server.
    * SSRF: user-submitted http(s) URLs are resolved and rejected when
      they point at private / loopback / link-local / reserved
      addresses (``MEDIASCRIBE_ALLOWED_HOSTS`` opts hostnames out for
      local development).  Local file paths are only accepted inside
      the workspace directory.
    * CSRF: JSON-body POST endpoints require ``Content-Type:
      application/json`` (else 415), so cross-site ``text/plain`` form
      posts cannot drive them.
    * Misc env knobs: ``MEDIASCRIBE_PUBLIC_BASE_URL`` (origin rendered
      into INSTALL.md), ``MEDIASCRIBE_WORKSPACE`` (data root),
      ``MEDIASCRIBE_LOG_LEVEL`` (root log level),
      ``MEDIASCRIBE_WIKI`` (set ``0`` to disable the knowledge vault —
      ``mediascribe.wiki`` archives every finished transcript into
      ``<workspace>/vault/``, an Obsidian-compatible wiki),
      ``MEDIASCRIBE_WS_MAX_SESSION_SECONDS`` /
      ``MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS`` (WebSocket lifetime caps).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查用
    pass

# FastAPI / Pydantic are optional dependencies.  The CLI may
# still want to print the help without them, so we keep imports
# inside the route handlers.
#
# 2026-10-25 职责拆分后, 这里的一批 import 只剩下「**探测可用性**」的用途
# (真正的使用点已搬到 route_*.py / models.py) ⇒ 标 noqa: F401。
try:
    from fastapi import (  # noqa: F401 — 仅探测可用性
        Depends,
        FastAPI,
        Header,
        HTTPException,
        Request,
        WebSocket,
        status,
    )
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, Response  # noqa: F401 — 仅探测可用性
    from fastapi.staticfiles import StaticFiles  # noqa: F401 — 仅探测可用性
    from pydantic import BaseModel, Field, field_validator  # noqa: F401 — 仅探测可用性

    _FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - optional
    _FASTAPI_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sub-module imports (2026-10-25 职责拆分)。
# 绝对路径是刻意的: 测试用 `import app`(裸模块) 与
# `import mediascribe.web.app`(包路径) 两条路径加载同一份 app.py,
# 相对导入会让两条路径各拿一份子模块实例 → _RateLimiter 这类对象不一致。
# ---------------------------------------------------------------------------
from mediascribe.web.cors import (
    _DEFAULT_CORS_ORIGIN_REGEX,
    _default_cors_origins,
    _resolve_cors_origins,
)
from mediascribe.web.gpu_health import (
    _cached_gpu_health,
    _gpu_health_cache,
    _gpu_health_lock,
    _reset_gpu_health_cache,
)
from mediascribe.web.job_runner import (
    _run_batch_job,
    _run_job_safely,
    _store_job_result,
)
from mediascribe.web.logging_setup import (
    _configure_logging_from_env,
)
from mediascribe.web.models import (
    TranscribeItem,
    TranscribeRequest,
    TranscribeResponse,
)
from mediascribe.web.pipeline_bridge import (
    _PIPELINE_CACHE,
    _PIPELINE_CACHE_LOCK,
    _build_pipeline,
    _run_one,
)
from mediascribe.web.public_url import (
    _public_base_url,
)
from mediascribe.web.ratelimit import (
    _build_rate_limiter,
    _enforce_rate_limit,
    _RateLimiter,
)
from mediascribe.web.security import (
    _env_float,
    _require_api_token,
    _require_json_content_type,
    _trusted_no_auth_client,
    _verify_api_token,
)
from mediascribe.web.static_assets import (
    STATIC_DIR,
    _static_asset_version,
    _StaticCacheControl,
)
from mediascribe.web.validation import (
    _is_local_path_source,
    _revalidate_short_link_target,
    _validate_local_source,
    _validate_public_url,
    _validate_submitted_urls,
)
from mediascribe.web.ws_bridge import (
    _WS_TERMINAL_EVENTS,
    _aio_queue_put_drop_oldest,
    _JobEventBridge,
)

# ---------------------------------------------------------------------------
# Route registrar imports (2026-10-25 职责拆分)。
# 这 7 个模块**依赖 fastapi**, 而 fastapi 是可选依赖 —— 所以放在
# ``_FASTAPI_AVAILABLE`` 条件下导入: 缺失时本模块仍要能 import,
# 好让 ``create_app()`` 抛出设计好的 RuntimeError("FastAPI is not
# installed…"), 而不是让 ``import mediascribe.web.app`` 直接
# ImportError(栈里看不出是缺哪个包)。
# ---------------------------------------------------------------------------
if _FASTAPI_AVAILABLE:
    from mediascribe.web.route_extension import (
        _EXTENSION_MANIFEST_VERSION,
        _import_extension_builder,
        _read_extension_manifest_version,
        _register_extension_routes,
    )
    from mediascribe.web.route_health import (
        _register_health_routes,
    )
    from mediascribe.web.route_jobs import (
        _job_registry,
        _pipeline_init_error,
        _register_job_control_routes,
        _register_job_result_route,
        _register_submit_jobs_route,
    )
    from mediascribe.web.route_pages import (
        _register_page_routes,
    )
    from mediascribe.web.route_transcribe import (
        _register_transcribe_route,
    )
    from mediascribe.web.route_wiki import (
        _register_wiki_routes,
    )
    from mediascribe.web.route_ws import (
        _register_ws_progress_route,
    )

#: 本模块的公共名字 —— 多数是**从子模块 re-export** 的(历史上它们
#: 就住在 app.py, 测试与外部代码用 ``from app import X`` 取)。
#: 列出它们可让 ruff 认出「这是有意导出」而不是「未使用的 import」。
__all__ = [
    "_register_health_routes",
    "_register_extension_routes",
    "_import_extension_builder",
    "_read_extension_manifest_version",
    "_EXTENSION_MANIFEST_VERSION",
    "_register_page_routes",
    "_register_wiki_routes",
    "_register_transcribe_route",
    "_register_ws_progress_route",
    "_job_registry",
    "_pipeline_init_error",
    "_register_job_control_routes",
    "_register_submit_jobs_route",
    "_register_job_result_route",
    "_configure_logging_from_env",
    "_public_base_url",
    "_default_cors_origins",
    "_DEFAULT_CORS_ORIGIN_REGEX",
    "_resolve_cors_origins",
    "_StaticCacheControl",
    "_static_asset_version",
    "_validate_public_url",
    "_is_local_path_source",
    "_validate_local_source",
    "_validate_submitted_urls",
    "_revalidate_short_link_target",
    "_trusted_no_auth_client",
    "_require_api_token",
    "_verify_api_token",
    "_env_float",
    "_require_json_content_type",
    "_RateLimiter",
    "_build_rate_limiter",
    "_enforce_rate_limit",
    "_cached_gpu_health",
    "_reset_gpu_health_cache",
    "_gpu_health_cache",
    "_gpu_health_lock",
    "_store_job_result",
    "_run_job_safely",
    "_run_batch_job",
    "_WS_TERMINAL_EVENTS",
    "_aio_queue_put_drop_oldest",
    "_JobEventBridge",
    "TranscribeRequest",
    "TranscribeItem",
    "TranscribeResponse",
    "_build_pipeline",
    "_PIPELINE_CACHE",
    "_PIPELINE_CACHE_LOCK",
    "_run_one",
    "create_app",
    "main",
    "ROOT",
    "STATIC_DIR",
]

ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


# v3.2.0e: Pipeline 实例缓存，按 (engine, model, device, language) 复用。
# 避免每个 Web 请求重新加载 Whisper 模型（5s+ → 0ms）。
# v3.2.0e+: 保护 _PIPELINE_CACHE 的 LRU 淘汰与写入，防止并发 race。
# ---------------------------------------------------------------------------
# Pipeline 工厂 — 路由层通过它拿 pipeline
# ---------------------------------------------------------------------------
# 间接一层是**刻意**的: ``_build_pipeline`` 有测试 patch 目标
# (``mock.patch("app._build_pipeline")`` / ``mock.patch("mediascribe.web.app.
# _build_pipeline")``), 而测试的顺序是「先 create_app, 再 patch」——
# 若在 create_app 时把 ``_build_pipeline`` 本身当参数传给路由模块, 会在
# 装配期就把原函数捕获下来 ⇒ patch 失效(路由跑的是真 pipeline)。
# 工厂在**调用时**才去 app 模块的全局查找它 ⇒ patch 语义与拆分前一致。
def _pipeline_factory(req, workspace):
    """构造 Pipeline —— 委托 app 模块的 ``_build_pipeline``。"""
    return _build_pipeline(req, workspace)


# ---------------------------------------------------------------------------
# Background job runner (v3.2.0c Tier 1 — cancel WS bridge)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# WS progress plumbing (P2-9) — 常驻 drain 线程桥接阻塞 events 队列
# ---------------------------------------------------------------------------


def create_app(
    workspace: Optional[Path] = None,
    api_token: Optional[str] = None,
    cors_origins: Optional[List[str]] = None,
):
    """Build the FastAPI app.  ``workspace`` is the temp dir for transcripts.

    Args:
        workspace: directory where transcripts and audio are written.
        api_token: optional shared secret.  If ``None``, falls back to
            ``MEDIASCRIBE_API_TOKEN`` from the environment; if neither is
            set, the API is unauthenticated (fine for ``127.0.0.1``-only
            dev usage; **do not** expose such an instance on a public
            network).
        cors_origins: explicit list of allowed origins.  ``None`` falls
            back to ``MEDIASCRIBE_CORS_ORIGINS`` or the built-in
            extension-friendly defaults.
    """
    if not _FASTAPI_AVAILABLE:
        raise RuntimeError("FastAPI is not installed. Run: pip install mediascribe[web]")
    # 版本号单一真源：mediascribe._version.__version__（mediascribe/__init__
    # 转发同一常量）。此处才导入 —— 顶层导入会把整个 ASR 栈(torch)拖进
    # Web 启动路径，而 Web 层只需要这个字符串。
    from mediascribe._version import __version__

    # 附加修复: docker-compose 已设 ``MEDIASCRIBE_LOG_LEVEL=info`` 但代码
    # 此前不读 — 在应用装配处应用一次(幂等)。
    _configure_logging_from_env()
    if not (api_token or os.environ.get("MEDIASCRIBE_API_TOKEN", "")).strip():
        logger.warning(
            "MEDIASCRIBE_API_TOKEN 未配置 — API 处于无鉴权模式, "
            "仅信任本机/内网来源(公网来源会被拒绝)。"
            "暴露到本机以外之前请务必设置该环境变量。"
        )

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
        version=__version__,
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
    _register_transcribe_route(app, workspace, _pipeline_factory)
    _register_ws_progress_route(app)
    _register_job_control_routes(app)
    _register_submit_jobs_route(app, workspace, _pipeline_factory)
    _register_job_result_route(app)
    return app


# ---------------------------------------------------------------------------
# CLI (run via ``python -m mediascribe.web.app``)
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--workspace", type=Path, default=None)
    args = parser.parse_args(argv)

    if not _FASTAPI_AVAILABLE:
        print("FastAPI not installed. Run: pip install mediascribe[web]")
        return 1
    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed. Run: pip install mediascribe[web]")
        return 1
    app = create_app(workspace=args.workspace)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
    return 0

    # -------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Job progress (v3.2.0a) — WebSocket-friendly
    # -------------------------------------------------------------------------

    # -----------------------------------------------------------------
    # v3.2.0c Tier 1 — async job submission + result retrieval
    # -----------------------------------------------------------------


# ---------------------------------------------------------------------------
# 模块级 ASGI 入口
# ---------------------------------------------------------------------------
# ``uvicorn mediascribe.web.app:app`` 是本模块 docstring 与
# ``scripts/launch/one_click_up.py`` 一直宣称的启动方式，但**模块级 ``app``
# 从来不存在** ⇒ 该命令必然报
# ``ERROR: Error loading ASGI app. Attribute "app" not found``。
#
# 这里补上它。仅在被**导入**时创建（``__main__`` 路径由 main() 自己建
# app），避免 ``python -m mediascribe.web.app`` 创建两个实例。
if _FASTAPI_AVAILABLE and __name__ != "__main__":
    app = create_app()


# ``__main__`` 块必须放在**所有路由注册函数定义之后** —— 放在文件中部
# 时，``create_app()`` 调用 ``_register_health_routes`` 等函数时它们尚未
# 定义，``python -m mediascribe.web.app`` 会直接抛
# ``NameError: name '_register_health_routes' is not defined``。
if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
