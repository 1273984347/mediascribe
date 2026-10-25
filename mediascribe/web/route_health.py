"""``/api/health`` 路由 —— 存活探针 + GPU 状态。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Dict

from mediascribe.web.gpu_health import _cached_gpu_health

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查用
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _register_health_routes(app: "FastAPI") -> None:
    """Route registrar — create_app 装配时调用(2026-10-03 审查拆分, 路由逐字搬移)。"""
    from mediascribe.pipeline import resolve_device

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        # v3.2.0c-fix: opportunistic purge of finished jobs older than
        # 1 hour, run on each health probe (dashboard polls every 5s).
        # ``ProgressRegistry.purge`` was previously never invoked, so
        # the registry leaked memory in long-running deployments.
        # Piggybacking on /api/health matches the GPU-cache pattern —
        # cheap, idempotent, no extra background task.
        # v3.2.0c-fix (R4): purge only cleans ``ProgressRegistry._jobs``;
        # ``app.state.job_results`` (the markdown store) must be
        # sync-cleaned for the same victim job_ids, otherwise REST
        # ``/api/jobs/{id}/result`` 404s after the 1-hour TTL while the
        # markdown payload still leaks in memory.
        try:
            # v3.2.0c-fix (P3-2 + R6): purge + orphan cleanup run UNDER
            # the same lock that _run_job_safely acquires for its
            # check-and-write. This makes purge atomic with respect to
            # the worker's registry-check, closing the TOCTOU window
            # where a worker could see the job as live (check passes)
            # then write an orphan entry while purge is mid-flight.
            with app.state.jobs_state_lock:
                app.state.jobs.purge(older_than_seconds=3600)
                # Sync-clean orphaned results: anything in job_results
                # that is no longer in the registry is dead data.
                live_ids = {j.job_id for j in app.state.jobs.list()}
                orphan_ids = [k for k in app.state.job_results if k not in live_ids]
                for orphan_id in orphan_ids:
                    app.state.job_results.pop(orphan_id, None)
        except Exception as exc:  # v3.2.0c-fix (R2): surface purge failures
            # Pre-R6 the swallowed surface was smaller; R6 widened it
            # to cover the new lock acquisition + orphan cleanup loop.
            # A recurring purge failure (e.g. future bug in
            # ProgressRegistry.purge) silently halts memory
            # reclamation with zero operator signal — log so the
            # dashboard can alert on recurring warnings.
            logger.warning("/api/health purge failed: %r", exc)
        # v3.2.0c Tier 2: GPU probe is cached for 1 s — the dashboard
        # polls every few seconds, and each ``torch.cuda.*`` probe
        # costs ~30 ms.
        gpu = _cached_gpu_health(ttl_seconds=1.0)
        return {
            "ok": True,
            "version": app.version,
            "auth_enabled": app.state.api_token_configured,
            "rate_limit_enabled": app.state.rate_limiter.enabled,
            "rate_limit_max": app.state.rate_limiter.max_requests,
            "rate_limit_window": app.state.rate_limiter.window_seconds,
            "device_hint": resolve_device(os.environ.get("MEDIASCRIBE_DEVICE", "auto")),
            "gpu": gpu,
        }
