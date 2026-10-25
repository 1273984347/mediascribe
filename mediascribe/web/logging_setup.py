"""日志级别设置 —— 从环境变量应用 root logger 级别(幂等)。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import logging
import os


def _configure_logging_from_env() -> None:
    """把 ``MEDIASCRIBE_LOG_LEVEL`` 应用到 root logger(幂等)。

    docker-compose / Dockerfile 已经导出 ``MEDIASCRIBE_LOG_LEVEL=info``,
    但此前代码从不读取 — 死配置。无法识别的取值直接忽略, 保持默认级别。
    """
    raw = os.environ.get("MEDIASCRIBE_LOG_LEVEL", "").strip().upper()
    if not raw:
        return
    level = getattr(logging, raw, None)
    if isinstance(level, int):
        logging.getLogger().setLevel(level)
