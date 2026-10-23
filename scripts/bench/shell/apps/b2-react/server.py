#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B2 候选 —— FastAPI 托管 Vite+React 预构建静态工作台壳。

设计约束（重要）：**不修改 mediascribe 既有代码**。

* 页面资源：本目录 ``dist/``（Vite 构建产物）——``/`` 与 ``/assets/*`` 由静态层托管
* 后端 API：**原样**转发给 ``mediascribe.web.app.create_app()`` 创建的 ASGI 应用
  （``/api/*``、``/ws/*``、``/extension*``、``/wiki*`` 一律走原应用）

路由用「薄 ASGI 路由器」实现：静态壳在前，其余路径原样交给 MediaScribe 应用，
因此 MediaScribe 侧行为与 B1 完全一致，两者的差异**只**在页面交付形态。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"

# 仓库根: 优先用环境变量, 否则从本文件向上查找含 mediascribe/ 的目录。
REPO = os.environ.get("MEDIASCRIBE_REPO_ROOT")
if not REPO:
    _cand = HERE
    for _ in range(6):
        if (_cand / "mediascribe" / "web" / "app.py").is_file():
            REPO = str(_cand)
            break
        _cand = _cand.parent
    else:
        REPO = r"D:\MediaScribe"
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import FileResponse  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402
from starlette.staticfiles import StaticFiles  # noqa: E402

from mediascribe.web.app import create_app  # noqa: E402


async def _index(request):  # noqa: ANN001
    return FileResponse(str(DIST / "index.html"))


def build_static_app() -> Starlette:
    assets_dir = DIST / "assets"
    routes = [Route("/", _index), Route("/index.html", _index)]
    if assets_dir.is_dir():
        routes.insert(0, Mount("/assets", app=StaticFiles(directory=str(assets_dir)), name="assets"))
    return Starlette(routes=routes)


def build_app():
    """返回组合后的 ASGI3 应用 (async function → uvicorn 走 ASGI3 分支)。"""
    workspace = os.environ.get("MEDIASCRIBE_WORKSPACE") or str(Path(REPO) / "web-workspace")
    ms_app = create_app(workspace=Path(workspace))
    static_app = build_static_app()

    async def app(scope, receive, send):  # noqa: ANN001
        if scope["type"] == "http":
            path = scope["path"]
            if path == "/" or path == "/index.html" or path.startswith("/assets/"):
                await static_app(scope, receive, send)
                return
        await ms_app(scope, receive, send)

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description="B2 web-static 工作台壳 (FastAPI 托管 Vite+React dist)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    if not (DIST / "index.html").is_file():
        print(f"[B2] 缺少构建产物: {DIST / 'index.html'} —— 先跑 `npm install && npm run build`")
        return 1

    import uvicorn

    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
