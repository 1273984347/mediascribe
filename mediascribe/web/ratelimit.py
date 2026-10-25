"""进程内滑动窗口限流。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
fastapi 是可选依赖 —— 缺失时限流类仍可用, 只是抛 429 的那层不可用。
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict

try:
    from fastapi import HTTPException, status
except ImportError:  # pragma: no cover - optional
    # 只在函数体里用(抛 429), 不在默认参数位置 ⇒ 置 None 即可, 模块仍可导入。
    HTTPException = status = None  # type: ignore[assignment,misc]


@dataclass
class _RateLimiter:
    """Sliding-window rate limiter keyed by client identity.

    Thread-safe (a lock guards the underlying map).  Each check prunes
    the calling client's stale timestamps; **expired buckets for absent
    clients are swept periodically** — at most once per
    ``sweep_interval`` seconds (see :meth:`_RateLimiter.sweep`) — so
    memory stays bounded by "clients active within the last window +
    sweep interval".

    Client identity is the direct peer address (``request.client.host``).
    When running behind a reverse proxy that terminates the connection
    (uvicorn ``--proxy-headers`` / nginx), every client shares the
    proxy's single bucket, so the limiter degrades to a *global* limit —
    deployers behind a proxy should raise ``MEDIASCRIBE_RATE_LIMIT``
    accordingly or move limiting into the proxy.
    """

    max_requests: int = 10
    window_seconds: float = 60.0
    enabled: bool = True
    sweep_interval: float = 60.0
    _buckets: Dict[str, Deque[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_sweep: float = 0.0

    def _sweep_locked(self, cutoff: float) -> int:
        """Drop every bucket whose newest timestamp left the window.

        Caller must hold ``_lock``.
        """
        dead = [k for k, b in self._buckets.items() if not b or b[-1] <= cutoff]
        for k in dead:
            self._buckets.pop(k, None)
        return len(dead)

    def sweep(self) -> int:
        """Periodic full-map cleanup of expired buckets (P2-1).

        Called automatically from :meth:`check` at most once per
        ``sweep_interval`` seconds; exposed separately for tests and
        ops tooling.  Returns the number of buckets removed.
        """
        now = time.monotonic()
        with self._lock:
            self._last_sweep = now
            return self._sweep_locked(now - self.window_seconds)

    def check(self, client_id: str) -> tuple[bool, int, float]:
        """Return ``(allowed, remaining, retry_after_seconds)``.

        ``retry_after_seconds`` is only meaningful when ``allowed`` is
        ``False``; otherwise it is ``0``.
        """
        if not self.enabled or self.max_requests <= 0:
            return True, self.max_requests, 0.0
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            # P2-1: 周期性全表清理 — 只 prune 当前 client 的时间戳不够,
            # 离席 client 的空 bucket 永不回收, 长跑进程内存无界增长。
            if now - self._last_sweep >= self.sweep_interval:
                self._last_sweep = now
                self._sweep_locked(cutoff)
            bucket = self._buckets.get(client_id)
            if bucket is None:
                bucket = deque()
                self._buckets[client_id] = bucket
            # Prune timestamps that fell out of the window.
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                # 收敛到 [0, window]: 浮点加减可能产生 60.00000000000006
                # 这类越界值(Windows 计时打平), 语义上重试等待也不会超窗。
                retry_after = min(
                    max(0.0, bucket[0] + self.window_seconds - now),
                    self.window_seconds,
                )
                return False, 0, retry_after
            bucket.append(now)
            return True, self.max_requests - len(bucket), 0.0

    def reset(self) -> None:
        """Clear all state — exposed for tests."""
        with self._lock:
            self._buckets.clear()


def _build_rate_limiter() -> _RateLimiter:
    """Build a rate limiter from the environment.

    * ``MEDIASCRIBE_RATE_LIMIT`` — integer max requests per minute
      (default ``10``).  ``0`` disables the limiter entirely.
    * ``MEDIASCRIBE_RATE_LIMIT_WINDOW`` — float window in seconds
      (default ``60``).  This is a free-form knob in case users want
      "5 requests per 10 seconds" style limits.
    """
    try:
        max_req = int(os.environ.get("MEDIASCRIBE_RATE_LIMIT", "10"))
    except ValueError:
        max_req = 10
    try:
        window = float(os.environ.get("MEDIASCRIBE_RATE_LIMIT_WINDOW", "60"))
    except ValueError:
        window = 60.0
    return _RateLimiter(
        max_requests=max_req,
        window_seconds=window,
        enabled=max_req > 0,
    )


def _enforce_rate_limit(
    limiter: _RateLimiter,
    request,  # ``fastapi.Request`` is optional — avoid hard import.
) -> None:
    """Raise ``429`` when the client exceeds its quota."""
    client_ip = "unknown"
    try:
        client_ip = request.client.host if request.client else "unknown"
    except Exception:  # pragma: no cover - defensive
        client_ip = "unknown"
    allowed, remaining, retry_after = limiter.check(client_ip)
    if not allowed:
        # ``Retry-After`` per RFC 6585 must be an integer number of seconds.
        retry_int = max(1, int(round(retry_after)))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"rate limit exceeded ({limiter.max_requests} req / "
                f"{limiter.window_seconds:g}s); retry in {retry_int}s"
            ),
            headers={
                "Retry-After": str(retry_int),
                "X-RateLimit-Limit": str(limiter.max_requests),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Window": str(int(limiter.window_seconds)),
            },
        )
