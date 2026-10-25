"""静态页面路由 —— ``/`` 工作台首页。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
except ImportError:  # pragma: no cover - optional
    # 只在函数体里用(fastapi 缺失时不会执行到)。
    HTMLResponse = StaticFiles = None  # type: ignore[assignment,misc]

from mediascribe.web.static_assets import STATIC_DIR, _static_asset_version, _StaticCacheControl

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查用
    from fastapi import FastAPI


def _register_page_routes(app: "FastAPI") -> None:
    """Route registrar — create_app 装配时调用(2026-10-03 审查拆分, 路由逐字搬移)。"""

    # -------------------------------------------------------------------
    # 静态资源（v3.4.x P4：前端拆成原生 ES modules 后，页面要引用
    # styles.css / app.js / modules/*.js —— 必须真的能被服务端提供，
    # 否则页面在生产环境下是**坏的**（CSS/JS 全 404）。
    # -------------------------------------------------------------------
    if STATIC_DIR.is_dir():
        app.mount(
            "/static",
            _StaticCacheControl(StaticFiles(directory=str(STATIC_DIR))),
            name="static",
        )

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        # 版本号由后端注入 —— 单一真源是 mediascribe._version.__version__。
        # 前端不再硬编码版本字面量, 避免 footer 与 /api/health 各说各话。
        html = html.replace("{{APP_VERSION}}", app.version)
        # 静态资源缓存版本 —— 见 _static_asset_version()。
        html = html.replace("{{STATIC_VERSION}}", _static_asset_version())
        return HTMLResponse(content=html)
