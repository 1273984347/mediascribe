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
        from mediascribe.observability import clear_observability
        clear_observability()

    def test_basic_span_lifecycle(self):
        from mediascribe.observability import OBSERVABILITY, get_tracer
        tracer = get_tracer()
        with tracer.start_as_current_span("test") as span:
            span.set_attribute("k", "v")
        self.assertEqual(len(OBSERVABILITY["spans"]), 1)
        rec = OBSERVABILITY["spans"][0]
        self.assertEqual(rec["name"], "test")
        self.assertEqual(rec["attributes"]["k"], "v")
        self.assertIsNotNone(rec["duration_ms"])

    def test_nested_spans_share_trace_id(self):
        from mediascribe.observability import OBSERVABILITY, get_tracer
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
        from mediascribe.observability import OBSERVABILITY, get_tracer
        tracer = get_tracer()
        with self.assertRaises(RuntimeError):
            with tracer.start_as_current_span("boom") as span:
                raise RuntimeError("kaboom")
        rec = OBSERVABILITY["spans"][-1]
        self.assertEqual(rec["status"], "ERROR")
        self.assertTrue(any(e["type"] == "exception" for e in rec["events"]))

    def test_add_event(self):
        from mediascribe.observability import OBSERVABILITY, get_tracer
        tracer = get_tracer()
        with tracer.start_as_current_span("evt") as span:
            span.add_event("checkpoint", {"step": 1})
        rec = OBSERVABILITY["spans"][-1]
        self.assertTrue(any(e.get("name") == "checkpoint" for e in rec["events"]))


class TestMeter(unittest.TestCase):

    def setUp(self):
        from mediascribe.observability import clear_observability
        clear_observability()

    def test_counter_records(self):
        from mediascribe.observability import OBSERVABILITY, get_meter
        meter = get_meter()
        c = meter.create_counter("v2t.requests")
        c.add(1, {"platform": "youtube"})
        c.add(2, {"platform": "bilibili"})
        self.assertEqual(len(OBSERVABILITY["metrics"]["v2t.requests"]), 2)
        self.assertEqual(OBSERVABILITY["metrics"]["v2t.requests"][0]["value"], 1)

    def test_histogram_records(self):
        from mediascribe.observability import OBSERVABILITY, get_meter
        meter = get_meter()
        h = meter.create_histogram("v2t.duration_ms")
        h.record(123.4, {"engine": "whisper"})
        self.assertEqual(OBSERVABILITY["metrics"]["v2t.duration_ms"][0]["value"],
                         123.4)


class TestClearObservability(unittest.TestCase):

    def test_clear_resets_state(self):
        from mediascribe.observability import (
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


class TestConcurrentSpanContexts(unittest.TestCase):
    """P2-15: 并发 asyncio task 的 span 栈经 ContextVar 隔离,
    child 的 parent/trace 不得串到别的 task 上。"""

    def setUp(self):
        from mediascribe.observability import clear_observability
        clear_observability()

    def test_concurrent_tasks_do_not_cross_contaminate(self):
        import asyncio

        from mediascribe.observability import OBSERVABILITY, get_tracer

        tracer = get_tracer()

        async def one(tag):
            with tracer.start_as_current_span(f"root-{tag}") as root:
                root.set_attribute("tag", tag)
                await asyncio.sleep(0.01)
                with tracer.start_as_current_span(f"child-{tag}"):
                    await asyncio.sleep(0.01)

        async def driver():
            await asyncio.gather(one("a"), one("b"))

        asyncio.run(driver())

        spans = OBSERVABILITY["spans"]
        self.assertEqual(len(spans), 4)
        by_id = {s["span_id"]: s for s in spans}
        for tag in ("a", "b"):
            child = next(s for s in spans if s["name"] == f"child-{tag}")
            parent = by_id[child["parent_id"]]
            self.assertEqual(parent["name"], f"root-{tag}")
            self.assertEqual(parent["trace_id"], child["trace_id"])

    def test_root_span_after_nested_context_is_clean(self):
        """嵌套 span 退出后,栈被正确恢复 — 后续根 span 的 parent 为空。"""
        from mediascribe.observability import OBSERVABILITY, get_tracer

        tracer = get_tracer()
        with tracer.start_as_current_span("outer"):
            with tracer.start_as_current_span("inner"):
                pass
        with tracer.start_as_current_span("after") as span:
            self.assertIsNone(span.parent_id)
        rec = OBSERVABILITY["spans"][-1]
        self.assertIsNone(rec["parent_id"])


class TestOptionalOtelUpgrade(unittest.TestCase):

    def test_install_returns_bool(self):
        from mediascribe.observability import install_opentelemetry_exporter
        # No assertion on return value: depends on whether
        # opentelemetry-sdk is installed in the test env.
        result = install_opentelemetry_exporter()
        self.assertIn(result, (True, False))


if __name__ == "__main__":
    unittest.main()
