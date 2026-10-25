"""GPU 健康探针(1 秒缓存)。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

_gpu_health_cache: Dict[str, Any] = {"ts": 0.0, "value": None}


_gpu_health_lock = threading.Lock()


def _cached_gpu_health(ttl_seconds: float = 1.0) -> Dict[str, Any]:
    """Return ``gpu_health()`` cached for ``ttl_seconds``.

    The actual probe runs outside the lock so a slow probe does not
    block other readers; we re-acquire the lock only to publish the
    fresh value.
    """
    now = time.monotonic()
    with _gpu_health_lock:
        cached: Optional[Dict[str, Any]] = _gpu_health_cache["value"]
        ts = _gpu_health_cache["ts"]
    if cached is not None and (now - ts) < ttl_seconds:
        return cached
    # Local import — keeps the module importable without torch.
    from mediascribe.pipeline import gpu_health

    fresh = gpu_health()
    with _gpu_health_lock:
        _gpu_health_cache["ts"] = time.monotonic()
        _gpu_health_cache["value"] = fresh
    return fresh


def _reset_gpu_health_cache() -> None:
    """Test hook — clear the cached GPU health value."""
    with _gpu_health_lock:
        _gpu_health_cache["ts"] = 0.0
        _gpu_health_cache["value"] = None
