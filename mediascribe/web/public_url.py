"""对外公开的服务端 origin 推导(INSTALL.md 用)。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查用
    from fastapi import Request


def _public_base_url(request: "Request") -> str:
    """决定 INSTALL.md 中展示的服务端 origin (P2-10)。

    优先 ``MEDIASCRIBE_PUBLIC_BASE_URL``; 否则取 ``request.base_url`` 但
    只保留 scheme + host(:port) — Host 头可被客户端伪造, 不能把
    userinfo / 任意字符原样反射进文档。
    """
    env_base = os.environ.get("MEDIASCRIBE_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if env_base:
        return env_base
    try:
        parts = urlsplit(str(request.base_url))
        scheme = parts.scheme.lower() if parts.scheme.lower() in ("http", "https") else "http"
        host = parts.hostname or "127.0.0.1"
        # 只放行 hostname 字符集, 防止 Host 头把引号/尖括号等注入文档。
        if not all(c.isalnum() or c in "._-[]" for c in host):
            host = "127.0.0.1"
        try:
            port = parts.port
        except ValueError:
            port = None
        netloc = f"{host}:{port}" if port else host
        return f"{scheme}://{netloc}"
    except Exception:  # pragma: no cover - defensive
        return "http://127.0.0.1:8000"
