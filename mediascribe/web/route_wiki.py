"""知识库(vault)路由 —— 页面 + 笔记读写。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

try:
    from fastapi import Depends, HTTPException
    from fastapi.responses import HTMLResponse
except ImportError:  # pragma: no cover - optional
    # fastapi 缺失时本模块仍要能导入(见 _depends 的说明)。
    Depends = HTTPException = None  # type: ignore[assignment,misc]
    HTMLResponse = None  # type: ignore[assignment,misc]


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


from mediascribe.web.security import _require_api_token
from mediascribe.web.static_assets import STATIC_DIR, _static_asset_version

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查用
    from fastapi import FastAPI


def _register_wiki_routes(app: "FastAPI") -> None:
    """Route registrar — create_app 装配时调用(2026-10-03 审查拆分, 路由逐字搬移)。"""

    # 知识库视图 (Karpathy 式 vault 的 Web 浏览层)
    # -------------------------------------------------------------------
    @app.get("/wiki", response_class=HTMLResponse)
    def wiki_page() -> HTMLResponse:
        html = (STATIC_DIR / "wiki.html").read_text(encoding="utf-8")
        html = html.replace("{{STATIC_VERSION}}", _static_asset_version())
        return HTMLResponse(content=html)

    @app.get("/api/wiki", dependencies=[_depends(_require_api_token)])
    def wiki_index() -> Dict[str, Any]:
        """知识库索引: 笔记清单 + HOME 内容 + vault 路径。"""
        vault = app.state.wiki_vault
        if vault is None:
            return {"enabled": False, "notes": [], "home": "", "raw_count": 0}
        data = vault.index()
        data["enabled"] = True
        data["path"] = str(vault.root)
        payload: Dict[str, Any] = data
        return payload

    @app.get("/api/wiki/note", dependencies=[_depends(_require_api_token)])
    def wiki_note(name: str) -> Any:
        """返回单篇笔记原文。只接受 vault 内的 ``.md`` 相对路径。"""
        from fastapi.responses import Response

        vault = app.state.wiki_vault
        if vault is None:
            raise HTTPException(status_code=404, detail="wiki disabled")
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts or rel.suffix != ".md":
            raise HTTPException(status_code=400, detail="invalid note path")
        target = (vault.root / rel).resolve()
        try:
            contained = target.is_relative_to(vault.root.resolve())
        except OSError:  # pragma: no cover - defensive
            contained = False
        if not contained:
            raise HTTPException(status_code=400, detail="invalid note path")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="note not found")
        return Response(
            content=target.read_text(encoding="utf-8"),
            media_type="text/markdown; charset=utf-8",
        )

    @app.delete("/api/wiki/note", dependencies=[_depends(_require_api_token)])
    def wiki_note_delete(name: str) -> Dict[str, Any]:
        """删除单篇笔记并同步派生视图(概念账本 / 聚合 / HOME)。"""
        vault = app.state.wiki_vault
        if vault is None:
            raise HTTPException(status_code=404, detail="wiki disabled")
        try:
            removed = vault.delete_note(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="note not found") from exc
        return {"deleted": removed}
