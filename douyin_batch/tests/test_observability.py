"""
Unit tests for the observability layer.

The mini-SDK is exercised in isolation; the optional real-OTel
upgrade is skipped when ``opentelemetry-sdk`` is not installed.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


class TestSpanAndContext(unittest.TestCase):

    def setUp(self):
        from video2text.observability import clear_observability
        clear_observability()

    def test_basic_span_lifecycle(self):
        from video2text.observability import OBSERVABILITY, get_tracer
        tracer = get_tracer()
        with tracer.start_as_current_span("test") as span:
            span.set_attribute("k", "v")
        self.assertEqual(len(OBSERVABILITY["spans"]), 1)
        rec = OBSERVABILITY["spans"][0]
        self.assertEqual(rec["name"], "test")
        self.assertEqual(rec["attributes"]["k"], "v")
        self.assertIsNotNone(rec["duration_ms"])

    def test_nested_spans_share_trace_id(self):
        from video2text.observability import OBSERVABILITY, get_tracer
        tracer = get_tracer()
        with tracer.start_as_current_span("parent") as p:
            with tracer.start_as_current_span("child") as c:
                pass
        # Both spans recorded
        names = sorted(s["name"] for s in OBSERVABILITY["spans"])
        self.assertEqual(names, ["child", "parent"])
        # Same trace_id
        ids = {s["trace_id"] for s in OBSERVABILITY["spans"]}
        self.assertEqual(len(ids), 1)
        # Parent linkage correct
        child = next(s for s in OBSERVABILITY["spans"] if s["name"] == "child")
        self.assertIsNotNone(child["parent_id"])

    def test_exception_marked_as_error(self):
        from video2text.observability import OBSERVABILITY, get_tracer
        tracer = get_tracer()
        with self.assertRaises(RuntimeError):
            with tracer.start_as_current_span("boom") as span:
                raise RuntimeError("kaboom")
        rec = OBSERVABILITY["spans"][-1]
        self.assertEqual(rec["status"], "ERROR")
        self.assertTrue(any(e["type"] == "exception" for e in rec["events"]))

    def test_add_event(self):
        from video2text.observability import OBSERVABILITY, get_tracer
        tracer = get_tracer()
        with tracer.start_as_current_span("evt") as span:
            span.add_event("checkpoint", {"step": 1})
        rec = OBSERVABILITY["spans"][-1]
        self.assertTrue(any(e.get("name") == "checkpoint" for e in rec["events"]))


class TestMeter(unittest.TestCase):

    def setUp(self):
        from video2text.observability import clear_observability
        clear_observability()

    def test_counter_records(self):
        from video2text.observability import OBSERVABILITY, get_meter
        meter = get_meter()
        c = meter.create_counter("v2t.requests")
        c.add(1, {"platform": "youtube"})
        c.add(2, {"platform": "bilibili"})
        self.assertEqual(len(OBSERVABILITY["metrics"]["v2t.requests"]), 2)
        self.assertEqual(OBSERVABILITY["metrics"]["v2t.requests"][0]["value"], 1)

    def test_histogram_records(self):
        from video2text.observability import OBSERVABILITY, get_meter
        meter = get_meter()
        h = meter.create_histogram("v2t.duration_ms")
        h.record(123.4, {"engine": "whisper"})
        self.assertEqual(OBSERVABILITY["metrics"]["v2t.duration_ms"][0]["value"],
                         123.4)


class TestClearObservability(unittest.TestCase):

    def test_clear_resets_state(self):
        from video2text.observability import (
            OBSERVABILITY,
            clear_observability,
            get_meter,
            get_tracer,
        )
        with get_tracer().start_as_current_span("x"):
            pass
        get_meter().create_counter("c").add(1)
        self.assertGreater(len(OBSERVABILITY["spans"]), 0)
        clear_observability()
        self.assertEqual(len(OBSERVABILITY["spans"]), 0)
        self.assertEqual(len(OBSERVABILITY["metrics"]), 0)


class TestOptionalOtelUpgrade(unittest.TestCase):

    def test_install_returns_bool(self):
        from video2text.observability import install_opentelemetry_exporter
        # No assertion on return value: depends on whether
        # opentelemetry-sdk is installed in the test env.
        result = install_opentelemetry_exporter()
        self.assertIn(result, (True, False))


if __name__ == "__main__":
    unittest.main()
