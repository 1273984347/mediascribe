"""静态资源服务 —— 缓存响应头与缓存版本号。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

#: 静态资源目录(与 app.py 同级)。
STATIC_DIR = Path(__file__).parent / "static"


class _StaticCacheControl:
    """纯 ASGI 中间件：给 ``/static/*`` 的响应加 ``Cache-Control``。

    Starlette 0.52 的 ``StaticFiles`` 不接受自定义响应头参数, 且 304
    (协商缓存命中) 走的是另一条返回路径、不经过 ``file_response`` ——
    所以这里用一层薄 ASGI 包装, 让 200 与 304 都带上同一个头。

    ``no-cache, must-revalidate`` 的语义是「每次使用前必须向服务器
    校验」：文件没变时服务端回 304 (零 body), 文件变了就下发新内容。
    这修掉了「改了前端样式但页面仍是旧的」—— 浏览器不再用启发式缓存
    顶着旧样式。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not scope.get("path", "").startswith("/static/"):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"cache-control", b"no-cache, must-revalidate"))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _static_asset_version() -> str:
    """静态资源缓存版本号 —— 取 ``static/`` 下最新文件的 mtime(毫秒)。

    页面模板用 ``?v=<version>`` 引用样式与脚本, 任何前端文件改动都会让
    URL 变化, 浏览器随之放弃旧缓存。配合 ``_StaticCacheControl`` 的
    ``no-cache`` 响应头(覆盖 ES module 内部 import 这类无法加 query 的
    引用), 前端改动刷新即可见, 不需要手维护版本号。
    """
    try:
        newest = 0.0
        for path in STATIC_DIR.rglob("*"):
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
        return str(int(newest * 1000))
    except OSError:  # pragma: no cover - defensive
        return "0"
