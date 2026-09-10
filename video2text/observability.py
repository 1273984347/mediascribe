"""
Lightweight observability layer for Video2Text.

The real OpenTelemetry SDKs are heavy (megabytes of installed
dependencies) and not everyone wants them.  This module provides
a minimal **API-compatible no-op** implementation that:

* imports cleanly on any Python 3.8+ install with zero extra deps,
* exposes ``tracer.start_as_current_span(name)`` and
  ``meter.create_counter(name)`` that work the same way the real
  OTel ones do,
* records spans / metrics to an in-memory ``OBSERVABILITY`` dict so
  tests can assert on them, and
* can be **upgraded** to a real exporter by calling
  :func:`install_opentelemetry_exporter`, which switches the
  tracer / meter backends to ``opentelemetry-sdk`` if it is
  installed.

Usage:

.. code-block:: python

    from video2text.observability import (
        OBSERVABILITY, get_tracer, get_meter, install_opentelemetry_exporter,
    )

    tracer = get_tracer()
    with tracer.start_as_current_span("transcribe") as span:
        span.set_attribute("url", url)
        # ... do work ...

    # Print recorded spans
    for span in OBSERVABILITY["spans"]:
        print(span["name"], span["duration_ms"])

The OTel API is open and stable, so this mini-SDK is a faithful
subset.  When a user installs the full ``opentelemetry-sdk`` and
``opentelemetry-exporter-otlp`` packages and calls
:func:`install_opentelemetry_exporter`, the same calls start
shipping to their collector / Jaeger / Tempo backend.
"""
from __future__ import annotations

import contextvars
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 1. In-memory storage
# ---------------------------------------------------------------------------
OBSERVABILITY: Dict[str, Any] = {
    "spans": [],          # list of dicts
    "metrics": {},        # name -> [{value, attributes, ts}]
    "traces": [],         # list of finished root spans (trace_ids)
    "_lock": RLock(),
}

_MAX_RECORDS = int(os.environ.get("VIDEO2TEXT_OBS_MAX_RECORDS", "1000"))


def clear_observability() -> None:
    """Forget all recorded spans / metrics.  Useful between tests."""
    with OBSERVABILITY["_lock"]:
        OBSERVABILITY["spans"].clear()
        OBSERVABILITY["metrics"].clear()
        OBSERVABILITY["traces"].clear()


# ---------------------------------------------------------------------------
# 2. Span
# ---------------------------------------------------------------------------
@dataclass
class _Span:
    """An in-memory span.  Mirrors the OTel Span API subset we use."""
    name: str
    trace_id: str
    span_id: str
    parent_id: Optional[str]
    start_time: float
    end_time: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    status: str = "UNSET"
    events: List[Dict[str, Any]] = field(default_factory=list)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, status: str, description: Optional[str] = None) -> None:
        self.status = status
        if description:
            self.events.append({"type": "status", "status": status,
                                "description": description})

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        self.events.append({"type": "event", "name": name,
                            "attributes": attributes or {},
                            "ts": datetime.now(timezone.utc).isoformat()})

    def record_exception(self, exc: BaseException) -> None:
        self.events.append({
            "type": "exception",
            "name": exc.__class__.__name__,
            "message": str(exc),
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        self.status = "ERROR"

    def to_dict(self) -> Dict[str, Any]:
        dur = (
            (self.end_time - self.start_time) * 1000.0
            if self.end_time is not None else None
        )
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "start": datetime.fromtimestamp(self.start_time, tz=timezone.utc).isoformat(),
            "duration_ms": dur,
            "attributes": dict(self.attributes),
            "status": self.status,
            "events": list(self.events),
        }


# P2-15: 当前 span 栈改用 contextvars.ContextVar — 原先的模块级
# 单槽 ``_ACTIVE_SPAN: List[Optional[_Span]]`` 在并发 run 下互相串
# parent/trace_id(后进的 span 污染先进行的 trace)。栈值用不可变
# tuple,每个 context(线程 / asyncio task)拿到独立副本;跨
# context 关闭 span 时 ``reset(token)`` 会失败,回退到进入时快照。
_ACTIVE_SPANS: contextvars.ContextVar[Tuple[_Span, ...]] = (
    contextvars.ContextVar("video2text_active_span_stack", default=())
)


def _current_span() -> Optional[_Span]:
    stack = _ACTIVE_SPANS.get()
    return stack[-1] if stack else None


# ---------------------------------------------------------------------------
# 3. Tracer
# ---------------------------------------------------------------------------
class Tracer:
    """A minimal Tracer; mirrors ``opentelemetry.trace.Tracer``."""

    def start_as_current_span(self, name: str, **kwargs: Any) -> "_SpanCM":
        parent = _current_span()
        trace_id = parent.trace_id if parent else uuid.uuid4().hex
        parent_id = parent.span_id if parent else None
        span = _Span(
            name=name,
            trace_id=trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_id=parent_id,
            start_time=time.time(),
        )
        return _SpanCM(span)


class _SpanCM:
    """Context manager returned by ``Tracer.start_as_current_span``."""
    def __init__(self, span: _Span) -> None:
        self._span = span
        self._token: Optional[Any] = None
        self._prev_stack: Tuple[_Span, ...] = ()

    def __enter__(self) -> _Span:
        self._prev_stack = _ACTIVE_SPANS.get()
        self._token = _ACTIVE_SPANS.set(self._prev_stack + (self._span,))
        return self._span

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._span.end_time = time.time()
        if exc is not None:
            self._span.record_exception(exc)
        with OBSERVABILITY["_lock"]:
            OBSERVABILITY["spans"].append(self._span.to_dict())
            if self._span.parent_id is None:
                OBSERVABILITY["traces"].append(self._span.to_dict())
            # cap the in-memory ring
            if len(OBSERVABILITY["spans"]) > _MAX_RECORDS:
                OBSERVABILITY["spans"] = OBSERVABILITY["spans"][-_MAX_RECORDS:]
        # P2-15: 优先 reset(token)(严格恢复进入前状态);若 __enter__
        # 与 __exit__ 不在同一 context(async task 边界),回退快照。
        if self._token is not None:
            try:
                _ACTIVE_SPANS.reset(self._token)
            except ValueError:
                _ACTIVE_SPANS.set(self._prev_stack)
            self._token = None
        return False  # don't suppress


# ---------------------------------------------------------------------------
# 4. Meter (counters, histograms)
# ---------------------------------------------------------------------------
class _Counter:
    def __init__(self, name: str) -> None:
        self.name = name

    def add(self, value: float, attributes: Optional[Dict[str, Any]] = None) -> None:
        with OBSERVABILITY["_lock"]:
            OBSERVABILITY["metrics"].setdefault(self.name, []).append({
                "value": value,
                "attributes": attributes or {},
                "ts": datetime.now(timezone.utc).isoformat(),
            })


class _Histogram:
    def __init__(self, name: str) -> None:
        self.name = name

    def record(self, value: float, attributes: Optional[Dict[str, Any]] = None) -> None:
        with OBSERVABILITY["_lock"]:
            OBSERVABILITY["metrics"].setdefault(self.name, []).append({
                "value": value,
                "attributes": attributes or {},
                "ts": datetime.now(timezone.utc).isoformat(),
            })


class Meter:
    def create_counter(self, name: str) -> _Counter:
        return _Counter(name)

    def create_histogram(self, name: str) -> _Histogram:
        return _Histogram(name)


# ---------------------------------------------------------------------------
# 5. Public API
# ---------------------------------------------------------------------------
_TRACER: Optional[Tracer] = None
_METER: Optional[Meter] = None


def get_tracer() -> Tracer:
    """Return the project tracer.  Lazily constructs the singleton."""
    global _TRACER
    if _TRACER is None:
        _TRACER = Tracer()
    return _TRACER


def get_meter() -> Meter:
    """Return the project meter."""
    global _METER
    if _METER is None:
        _METER = Meter()
    return _METER


# ---------------------------------------------------------------------------
# 6. Optional real-OTel upgrade
# ---------------------------------------------------------------------------
_REAL_OTEL_AVAILABLE = False
try:
    from opentelemetry import trace as _ot_trace  # noqa: F401
    from opentelemetry.sdk.trace import TracerProvider  # noqa: F401
    _REAL_OTEL_AVAILABLE = True
except ImportError:
    pass


def install_opentelemetry_exporter(exporter: Any = None) -> bool:
    """If the real ``opentelemetry-sdk`` is installed, switch the
    tracer / meter to it.  Returns True on success, False otherwise.

    The ``exporter`` argument is forwarded to ``BatchSpanProcessor``
    and accepts any ``opentelemetry.sdk.trace.export`` subclass.
    When omitted, spans are still produced but only printed to
    stdout (useful for local debugging).
    """
    if not _REAL_OTEL_AVAILABLE:
        return False
    global _TRACER, _METER
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.trace import TracerProvider

        trace.set_tracer_provider(TracerProvider())
        if exporter is not None:
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            trace.get_tracer_provider().add_span_processor(
                BatchSpanProcessor(exporter)
            )
        metrics.set_meter_provider(MeterProvider())

        # Wrap the real OTel tracer / meter so the rest of the
        # codebase can keep using ``with tracer.start_as_current_span(...)``
        # without knowing which backend is active.
        ot_tracer = trace.get_tracer("video2text")
        ot_meter = metrics.get_meter("video2text")

        class _Adapter:
            def start_as_current_span(self, name, **kw):
                return ot_tracer.start_as_current_span(name, **kw)
        class _MeterAdapter:
            def create_counter(self, name):
                return ot_meter.create_counter(name)
            def create_histogram(self, name):
                return ot_meter.create_histogram(name)
        _TRACER = _Adapter()  # type: ignore[assignment]
        _METER = _MeterAdapter()  # type: ignore[assignment]
        return True
    except Exception:
        return False
