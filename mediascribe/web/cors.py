"""CORS 来源解析 —— 默认来源与环境变量覆盖。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import os
from typing import List, Optional


def _default_cors_origins() -> List[str]:
    """Browser extension + common local dashboard origins.

    Wildcard (``*``) is intentionally *not* the default — the Web UI is
    a JSON API and we want explicit, auditable origins in production.

    These are the *exact* origins that the CORS middleware will match
    on.  For pattern matching (e.g. ``http://localhost:*`` and
    ``chrome-extension://*``) we use the regex returned by
    :func:`_default_cors_origin_regex`.
    """
    return [
        # Local development dashboards with no explicit port.
        "http://localhost",
        "http://127.0.0.1",
        # host.docker.internal — the docker-compose workflow (no port).
        "http://host.docker.internal",
    ]


_DEFAULT_CORS_ORIGIN_REGEX = (
    r"^https?://(localhost|127\.0\.0\.1|host\.docker\.internal)(:\d+)?$"
    r"|^chrome-extension://[a-z]+$"
    r"|^moz-extension://[a-f0-9-]{36,}$"
    # P2-2: ``file://`` 分支已删除 — 任意本地页面都能带着用户凭据场景
    # 打跨站请求。确有需要时经 ``MEDIASCRIBE_CORS_ORIGINS`` 显式配置。
)


def _resolve_cors_origins() -> tuple[List[str], Optional[str]]:
    """Return ``(origins, regex)`` honouring the env override."""
    override = os.environ.get("MEDIASCRIBE_CORS_ORIGINS", "").strip()
    if not override:
        return _default_cors_origins(), _DEFAULT_CORS_ORIGIN_REGEX
    origins = [o.strip() for o in override.split(",") if o.strip()]
    # When the user opts in via env, drop the regex to keep behaviour
    # explicit — they can always add their own regex patterns.
    return origins, None
