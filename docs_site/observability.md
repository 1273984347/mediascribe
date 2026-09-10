# Observability

MediaScribe ships a tiny **OpenTelemetry-compatible** SDK at
`mediascribe.observability` that:

* imports cleanly on any Python 3.8+ install with zero extra deps,
* exposes `tracer.start_as_current_span(name)` and
  `meter.create_counter(name)` that work the same way the real
  OTel ones do,
* records spans / metrics to an in-memory registry so tests
  can assert on them, and
* can be **upgraded** to a real exporter by calling
  `install_opentelemetry_exporter()`, which switches the
  tracer / meter backends to `opentelemetry-sdk` if it is
  installed.

## Quick start

```python
from mediascribe.observability import (
    get_tracer, get_meter, OBSERVABILITY,
)

tracer = get_tracer()
meter = get_meter()
counter = meter.create_counter("v2t.requests")
hist = meter.create_histogram("v2t.duration_ms")

with tracer.start_as_current_span("transcribe") as span:
    span.set_attribute("url", url)
    span.set_attribute("engine", engine)
    counter.add(1, {"platform": "youtube"})
    t0 = time.time()
    # ... do the work ...
    hist.record((time.time() - t0) * 1000, {"engine": engine})
```

## Inspect recorded spans

```python
for span in OBSERVABILITY["spans"]:
    print(span["name"], span["duration_ms"], span["status"])
```

## Upgrading to real OpenTelemetry

```python
from mediascribe.observability import install_opentelemetry_exporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

ok = install_opentelemetry_exporter(OTLPSpanExporter(endpoint="localhost:4317"))
print("OTel wired up:", ok)
```

If `opentelemetry-sdk` is not installed, the function returns
`False` and the mini-SDK keeps running (good for dev / tests).

## API parity

| Mini-SDK | OTel SDK | Notes |
|----------|----------|-------|
| `tracer.start_as_current_span(name)` | same | context manager |
| `span.set_attribute(k, v)` | same | |
| `span.set_status("OK" / "ERROR")` | same | description optional |
| `span.add_event(name, attributes)` | same | |
| `span.record_exception(exc)` | same | |
| `meter.create_counter(name).add(...)` | same | |
| `meter.create_histogram(name).record(...)` | same | |

## Limitations

* No baggage propagation.
* No sampling controls.
* No async context propagation.

For production observability, install the real `opentelemetry-sdk`
and call `install_opentelemetry_exporter()`.  The rest of the
codebase does not need to change.
