"""浏览器扩展的安装页 / 打包下载路由。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from fastapi import HTTPException, Request, status
    from fastapi.responses import HTMLResponse, Response
except ImportError:  # pragma: no cover - optional
    # fastapi 缺失时本模块仍要能导入(见模块 docstring)。
    HTTPException = Request = status = None  # type: ignore[assignment,misc]
    HTMLResponse = Response = None  # type: ignore[assignment,misc]

from mediascribe.web.public_url import _public_base_url
from mediascribe.web.ratelimit import _enforce_rate_limit

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查用
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

#: 扩展源码树 —— 解析一次, 下载端点直接流式打包。
_ROOT = Path(__file__).parent.parent.parent
_EXTENSION_ROOT = _ROOT / "extension"


def _read_extension_manifest_version() -> str:
    """Return the ``version`` field from ``extension/manifest.json``.

    We tolerate any failure (file missing, JSON invalid) and fall
    back to ``"0.0.0"`` — the install page must always render, even
    on a partial install.
    """
    manifest_path = _EXTENSION_ROOT / "manifest.json"
    if not manifest_path.is_file():
        return "0.0.0"
    try:
        import json as _json

        data = _json.loads(manifest_path.read_text(encoding="utf-8"))
        v = str(data.get("version", "0.0.0")).strip()
        return v or "0.0.0"
    except Exception:  # pragma: no cover - defensive
        return "0.0.0"


_EXTENSION_MANIFEST_VERSION = _read_extension_manifest_version()


def _import_extension_builder():
    """导入扩展打包模块, 兼容「包导入」与「脚本态裸模块」两种加载路径。

    web/app.py 既可能被 ``mediascribe.web.app`` 正常导入, 也可能被
    测试 client 以顶层脚本模块加载(此时 ``extension_builder`` 没有
    包前缀) — 两种都试, 都失败才抛给调用方转 500。
    """
    import importlib

    try:
        return importlib.import_module("mediascribe.web.extension_builder")
    except Exception:
        return importlib.import_module("extension_builder")


def _register_extension_routes(app: "FastAPI") -> None:
    """Route registrar — create_app 装配时调用(2026-10-03 审查拆分, 路由逐字搬移)。"""

    @app.get("/extension", response_class=HTMLResponse)
    def extension_page() -> HTMLResponse:
        """The user-facing install page for the browser extension.

        The page is rendered as a server-side template so we can show
        a useful banner when ``MEDIASCRIBE_API_TOKEN`` is set, and we
        do not need a second build step.
        """
        auth_on = app.state.api_token_configured
        token_hint = (
            "Enter your Bearer token on the options page after loading "
            "the extension. The server you are talking to requires one."
            if auth_on
            else "No token is required by the server, so the extension options page is optional."
        )
        # Render the same minimal dark theme as the main UI (KIMI-style
        # neutral dark) for visual consistency.
        html = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="color-scheme" content="dark">'
            "<title>MediaScribe Browser Extension</title>"
            "<style>"
            "body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,"
            "Roboto,'PingFang SC','Microsoft YaHei','Noto Sans',sans-serif;"
            "background:#181817;color:rgba(255,255,255,.84);padding:32px;line-height:1.6;}"
            "a{color:#007cff;}h1{margin:0 0 4px;font-size:20px;}"
            "h2{margin-top:28px;font-size:14px;color:rgba(255,255,255,.56);}"
            "ol{padding-left:20px;}code,pre{background:#121212;border:1px solid rgba(255,255,255,.12);"
            "border-radius:6px;padding:2px 6px;font-size:12px;"
            "font-family:ui-monospace,'SF Mono','Cascadia Code',Consolas,monospace;}"
            "pre{padding:10px 12px;overflow:auto;}"
            ".card{background:#1f1f1f;border:1px solid rgba(255,255,255,.12);border-radius:12px;"
            "padding:20px 22px;max-width:760px;}"
            ".btn{display:inline-block;background:rgba(255,255,255,.84);color:#121212;font-weight:500;"
            "padding:9px 14px;border-radius:10px;text-decoration:none;margin-top:8px;}"
            ".btn:hover{background:#ffffff;}"
            ".btn.secondary{background:rgba(255,255,255,.05);color:rgba(255,255,255,.84);}"
            ".tag{display:inline-block;background:rgba(255,255,255,.05);color:rgba(255,255,255,.56);"
            "padding:2px 8px;border-radius:999px;font-size:11px;margin-left:6px;}"
            ".ok{color:#16c456;}.warn{color:#ff9f0a;}"
            "</style></head><body>"
            '<div class="card">'
            "<h1>MediaScribe Browser Extension"
            f'<span class="tag">manifest v{_EXTENSION_MANIFEST_VERSION}</span>'
            "</h1>"
            "<p>Send the URL of the page you are reading straight to this "
            "Web UI and read the transcript in a toolbar popup or the "
            "browser's right-side panel.</p>"
            f"<p>{token_hint}</p>"
            '<a class="btn" href="/api/extension/download">Download '
            "extension (.zip)</a> "
            '<a class="btn secondary" href="/api/extension/install.md">'
            "Install guide (markdown)</a>"
            "<h2>Install in 30 seconds</h2>"
            "<ol>"
            "<li>Click <b>Download extension</b> above and save the ZIP.</li>"
            "<li>Extract it into a permanent folder, e.g. "
            "<code>~/mediascribe-extension/</code>.</li>"
            "<li>Open <code>chrome://extensions/</code> "
            "(or <code>edge://extensions/</code>) and turn on "
            "<b>Developer mode</b>.</li>"
            "<li>Click <b>Load unpacked</b> and select the extracted folder.</li>"
            "<li>Click the toolbar icon (or open the side panel) — done.</li>"
            "</ol>"
            "<h2>Troubleshooting</h2>"
            "<ul>"
            "<li><b>Side panel button missing</b> — update to Chrome / "
            "Edge 114 or newer.</li>"
            "<li><b>401 Unauthorized</b> — your server has "
            "<code>MEDIASCRIBE_API_TOKEN</code> set. Paste the same token "
            "in the extension options page.</li>"
            "<li><b>CORS blocked</b> — the server is configured to reject "
            "browser-extension origins. Check <code>MEDIASCRIBE_CORS_ORIGINS</code>."
            "</li>"
            "</ul>"
            "</div></body></html>"
        )
        return HTMLResponse(content=html)

    @app.get("/api/extension/download")
    def extension_download(request: Request):
        """Stream a freshly-built ZIP of the ``extension/`` tree.

        The ZIP is built in-memory each time so the user always gets
        a copy that matches the running server's version.  ``INSTALL.md``
        is generated per-request so it embeds the actual endpoint URL
        instead of the ``example.com`` placeholder.
        """
        _enforce_rate_limit(app.state.rate_limiter, request)
        try:
            builder = _import_extension_builder()
            build_extension_zip = builder.build_extension_zip
            build_install_markdown = builder.build_install_markdown
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="extension builder module is not importable",
            ) from exc
        # Build the install doc with the user's actual origin so the
        # one-click instructions match the running server.
        # P2-10: 不再原样反射 Host 头 — 优先 env, 否则剥离 userinfo
        # 只留 scheme+host。
        origin = _public_base_url(request)
        install_md = build_install_markdown(web_ui_origin=origin)
        try:
            zip_bytes, filename = build_extension_zip(
                _EXTENSION_ROOT,
                extra_files={"INSTALL.md": install_md},
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(exc),
            ) from exc
        # Use a Response so we can set Content-Disposition explicitly.
        from fastapi.responses import Response

        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    @app.get("/api/extension/install.md", response_class=HTMLResponse)
    def extension_install_md(request: Request) -> Response:
        """Serve the per-user install guide as raw markdown.

        ``text/markdown`` is the right MIME type per RFC 7763, but
        browsers will try to render it as plain text.  ``HTMLResponse``
        is fine because the endpoint is also linked from the install
        page; if you need the raw bytes, hit
        ``/api/extension/install.md?raw=1``.
        """
        try:
            builder = _import_extension_builder()
            build_install_markdown = builder.build_install_markdown
        except Exception as exc:  # pragma: no cover
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="extension builder module is not importable",
            ) from exc
        # P2-10: 同上 — 不反射原始 Host 头。
        origin = _public_base_url(request)
        md = build_install_markdown(web_ui_origin=origin)
        if request.query_params.get("raw") == "1":
            from fastapi.responses import Response

            return Response(
                content=md,
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": 'inline; filename="INSTALL.md"'},
            )
        # Otherwise wrap the markdown in a tiny HTML shim so the
        # browser renders something readable.
        from html import escape

        body = (
            '<!doctype html><meta charset="utf-8">'
            '<meta name="color-scheme" content="dark">'
            "<title>MediaScribe — Install guide</title>"
            "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,"
            "Roboto,'PingFang SC','Microsoft YaHei','Noto Sans',sans-serif;"
            "background:#181817;color:rgba(255,255,255,.84);"
            "padding:32px;line-height:1.6;max-width:760px;}"
            "pre,code{background:#121212;border:1px solid rgba(255,255,255,.12);"
            "border-radius:6px;padding:2px 6px;"
            "font-family:ui-monospace,'SF Mono','Cascadia Code',Consolas,monospace;}"
            "a{color:#007cff;}</style>"
            '<a href="/extension">&larr; Back</a>'
            f"<pre>{escape(md)}</pre>"
        )
        return HTMLResponse(content=body)
