"""Read, export, and clear APIs for the local-only event store."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Dict

try:
    from fastapi import Depends
except ImportError:  # pragma: no cover - optional
    Depends = None  # type: ignore[assignment]


def _depends(dependency):
    if Depends is None:
        return dependency
    return Depends(dependency)


from mediascribe.web.security import _require_api_token

if TYPE_CHECKING:
    from fastapi import FastAPI


def _event_store(app: "FastAPI"):
    from mediascribe.events import EventStore

    store = getattr(app.state, "event_store", None)
    if store is None:
        store = EventStore()
        app.state.event_store = store
    return store


def _event_recorder(app: "FastAPI"):
    from mediascribe.events import EventRecorder

    recorder = getattr(app.state, "event_recorder", None)
    if recorder is None:
        recorder = EventRecorder(store=_event_store(app), surface="web")
        app.state.event_recorder = recorder
    return recorder


def _register_event_routes(app: "FastAPI") -> None:
    @app.get("/api/events", dependencies=[_depends(_require_api_token)])
    def list_events(limit: int = 100) -> Dict[str, Any]:
        bounded = max(1, min(int(limit), 1000))
        store = _event_store(app)
        return {"summary": store.summary(), "events": store.list_events(limit=bounded)}

    @app.get("/api/events/export", dependencies=[_depends(_require_api_token)])
    def export_events():
        from fastapi.responses import JSONResponse

        payload = _event_store(app).export_payload()
        filename = f"mediascribe-events-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.json"
        return JSONResponse(
            payload,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.delete("/api/events", dependencies=[_depends(_require_api_token)])
    def clear_events() -> Dict[str, int]:
        return {"cleared": _event_store(app).clear()}
