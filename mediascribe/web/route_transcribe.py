"""同步转写路由 —— ``POST /api/transcribe``(兼容扩展与 MCP)。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, List

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


from mediascribe.web.models import TranscribeItem, TranscribeRequest, TranscribeResponse
from mediascribe.web.pipeline_bridge import _run_one
from mediascribe.web.ratelimit import _enforce_rate_limit
from mediascribe.web.route_jobs import _pipeline_init_error
from mediascribe.web.security import _require_api_token, _require_json_content_type
from mediascribe.web.validation import _validate_submitted_urls

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查用
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _register_transcribe_route(app: "FastAPI", workspace: Path, pipeline_factory) -> None:
    """Route registrar — create_app 装配时调用(2026-10-03 审查拆分, 路由逐字搬移)。"""

    @app.post(
        "/api/transcribe",
        response_model=TranscribeResponse,
        dependencies=[_depends(_require_api_token), _depends(_require_json_content_type)],
    )
    def transcribe(req: TranscribeRequest, request: Request) -> TranscribeResponse:
        # Rate limit is applied after auth so unauthenticated callers
        # cannot exhaust the limiter for legitimate users.
        _enforce_rate_limit(app.state.rate_limiter, request)
        if not req.urls:
            raise HTTPException(status_code=400, detail="urls must be non-empty")
        # P1-2 + P1-3: SSRF / 本地路径校验 — 私网 URL 与 workspace 外
        # 本地路径在提交入口拒绝。
        try:
            _validate_submitted_urls(req.urls, workspace)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            pipeline = pipeline_factory(req, workspace)
        except Exception as exc:
            # P2-8: 内部异常细节(路径/stack)不回显客户端, 落服务端日志。
            logger.exception("pipeline init failed")
            raise _pipeline_init_error(exc) from exc
        items: List[TranscribeItem] = []
        for url in req.urls:
            items.append(_run_one(pipeline, url, workspace / "out", req))
        return TranscribeResponse(results=items)
