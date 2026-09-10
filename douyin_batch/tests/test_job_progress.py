"""Tests for v3.2.0a WebSocket-friendly job progress module."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# JobProgress — emit + stage lifecycle
# ---------------------------------------------------------------------------


class TestJobProgress(unittest.TestCase):
    def test_initial_state(self):
        from mediascribe.progress import JobProgress

        job = JobProgress(job_id="abc", url="https://example.com/v")
        self.assertEqual(job.job_id, "abc")
        self.assertEqual(job.url, "https://example.com/v")
        self.assertFalse(job.cancelled)
        self.assertFalse(job.finished)
        self.assertIsNone(job.stage)
        self.assertEqual(job.stage_index, 0)
        self.assertEqual(job.stage_current, 0)

    def test_start_stage_emits_event(self):
        from mediascribe.progress import JobProgress

        job = JobProgress(job_id="j1", url="u")
        job.start_stage("download", total=10)
        self.assertEqual(job.stage, "download")
        self.assertEqual(job.stage_index, 0)
        self.assertEqual(job.stage_total, 10)
        ev = job.events.get_nowait()
        self.assertEqual(ev["event"], "stage_start")
        self.assertEqual(ev["stage"], "download")
        self.assertEqual(ev["total"], 10)
        self.assertEqual(ev["current"], 0)

    def test_start_stage_rejects_unknown(self):
        from mediascribe.progress import JobProgress

        job = JobProgress(job_id="j1", url="u")
        with self.assertRaises(ValueError):
            job.start_stage("bogus_stage", total=1)

    def test_advance_emits_progress(self):
        from mediascribe.progress import JobProgress

        job = JobProgress(job_id="j1", url="u")
        job.start_stage("transcribe", total=5)
        job.events.get_nowait()  # stage_start
        job.advance(2)
        ev = job.events.get_nowait()
        self.assertEqual(ev["event"], "stage_progress")
        self.assertEqual(ev["current"], 2)
        self.assertEqual(ev["total"], 5)
        self.assertEqual(job.stage_current, 2)

    def test_finish_stage(self):
        from mediascribe.progress import JobProgress

        job = JobProgress(job_id="j1", url="u")
        job.start_stage("merge", total=1)
        job.events.get_nowait()
        job.finish_stage()
        ev = job.events.get_nowait()
        self.assertEqual(ev["event"], "stage_done")
        self.assertEqual(ev["current"], 1)
        self.assertEqual(ev["total"], 1)

    def test_cancel_sets_flag_and_emits(self):
        from mediascribe.progress import JobProgress

        job = JobProgress(job_id="j1", url="u")
        job.cancel()
        self.assertTrue(job.cancelled)
        ev = job.events.get_nowait()
        self.assertEqual(ev["event"], "cancelled")
        self.assertTrue(ev["cancelled"])

    def test_fail_marks_finished(self):
        from mediascribe.progress import JobProgress

        job = JobProgress(job_id="j1", url="u")
        job.fail("boom")
        self.assertTrue(job.finished)
        self.assertEqual(job.error, "boom")
        ev = job.events.get_nowait()
        self.assertEqual(ev["event"], "failed")
        self.assertEqual(ev["error"], "boom")

    def test_succeed_marks_finished(self):
        from mediascribe.progress import JobProgress

        job = JobProgress(job_id="j1", url="u")
        job.succeed({"out_path": "/tmp/x.md"})
        self.assertTrue(job.finished)
        self.assertEqual(job.result, {"out_path": "/tmp/x.md"})
        ev = job.events.get_nowait()
        self.assertEqual(ev["event"], "succeeded")

    def test_to_dict(self):
        from mediascribe.progress import JobProgress

        job = JobProgress(job_id="j1", url="u")
        job.start_stage("transcribe", total=10)
        d = job.to_dict()
        self.assertEqual(d["job_id"], "j1")
        self.assertEqual(d["stage"], "transcribe")
        self.assertEqual(d["stage_index"], 2)
        self.assertEqual(d["stage_total"], 10)
        self.assertEqual(d["cancelled"], False)
        self.assertEqual(d["finished"], False)

    def test_stages_in_order(self):
        from mediascribe.progress import STAGES

        self.assertEqual(
            STAGES, ("download", "extract_audio", "transcribe", "merge")
        )


# ---------------------------------------------------------------------------
# ProgressRegistry
# ---------------------------------------------------------------------------


class TestProgressRegistry(unittest.TestCase):
    def test_create_get(self):
        from mediascribe.progress import ProgressRegistry

        r = ProgressRegistry()
        job = r.create("https://example.com")
        self.assertIsNotNone(r.get(job.job_id))
        self.assertIsNone(r.get("nonexistent"))

    def test_unique_ids(self):
        from mediascribe.progress import ProgressRegistry

        r = ProgressRegistry()
        a = r.create("u1")
        b = r.create("u2")
        self.assertNotEqual(a.job_id, b.job_id)

    def test_cancel(self):
        from mediascribe.progress import ProgressRegistry

        r = ProgressRegistry()
        job = r.create("u")
        self.assertTrue(r.cancel(job.job_id))
        self.assertTrue(r.get(job.job_id).cancelled)
        # Cancel unknown returns False
        self.assertFalse(r.cancel("nonexistent"))

    def test_list(self):
        from mediascribe.progress import ProgressRegistry

        r = ProgressRegistry()
        r.create("u1")
        r.create("u2")
        self.assertEqual(len(r.list()), 2)

    def test_purge_finished(self):
        import time as _t

        from mediascribe.progress import ProgressRegistry

        r = ProgressRegistry()
        old = r.create("u1")
        old.created_at = _t.time() - 7200
        old.finished = True
        fresh = r.create("u2")
        fresh.finished = True
        n = r.purge(older_than_seconds=3600)
        self.assertEqual(n, 1)
        self.assertIsNone(r.get(old.job_id))
        self.assertIsNotNone(r.get(fresh.job_id))


# ---------------------------------------------------------------------------
# P2-9 — bounded events queue
# ---------------------------------------------------------------------------


class TestBoundedEventsQueue(unittest.TestCase):
    """events 队列必须有界: 满时丢最旧, 终态事件永不丢。"""

    def test_queue_is_bounded_and_drops_oldest(self):
        from mediascribe.progress import EVENTS_QUEUE_MAXSIZE, JobProgress

        job = JobProgress(job_id="j1", url="u")
        self.assertEqual(job.events.maxsize, EVENTS_QUEUE_MAXSIZE)
        # 以非终态事件灌满队列再溢出 50 条。
        for i in range(EVENTS_QUEUE_MAXSIZE + 50):
            job.emit("stage_progress", current=i)
        self.assertLessEqual(job.events.qsize(), EVENTS_QUEUE_MAXSIZE)
        self.assertGreater(job.dropped_events, 0)
        # 队列里最旧的事件已被丢弃(第一看到的是被保留的较新事件)。
        first = job.events.get_nowait()
        self.assertEqual(first["event"], "stage_progress")
        self.assertGreaterEqual(first["current"], 50)

    def test_terminal_event_never_dropped(self):
        from mediascribe.progress import EVENTS_QUEUE_MAXSIZE, JobProgress

        job = JobProgress(job_id="j1", url="u")
        for i in range(EVENTS_QUEUE_MAXSIZE + 10):
            job.emit("stage_progress", current=i)
        job.succeed({"out_path": "x"})
        events = []
        while not job.events.empty():
            events.append(job.events.get_nowait()["event"])
        self.assertEqual(events[-1], "succeeded",
                         "终态事件必须保留在队尾(丢最旧策略)")


# ---------------------------------------------------------------------------
# with_progress — pipeline wrapper
# ---------------------------------------------------------------------------


class TestWithProgress(unittest.TestCase):
    def test_runs_through_all_stages_on_success(self):
        from mediascribe.progress import JobProgress, with_progress

        job = JobProgress(job_id="j1", url="u")
        result = mock.MagicMock()
        result.engine = "whisper"
        pipeline = mock.MagicMock()
        pipeline.transcribe.return_value = result

        runner = with_progress(pipeline, job, "u", out_path=Path("/tmp/x.md"))
        out = runner()

        self.assertEqual(out, result)
        self.assertTrue(job.finished)
        # Drain the events queue
        events = []
        while not job.events.empty():
            events.append(job.events.get_nowait())
        # We should see 4 stage_start + 4 stage_done + 1 succeeded = 9
        starts = [e for e in events if e["event"] == "stage_start"]
        dones = [e for e in events if e["event"] == "stage_done"]
        # download stage_start is emitted, but finish_stage is not
        # called for it — the pipeline.transcribe() call below is
        # what produces the audio.  Only the transcribe + merge
        # stages end with finish_stage().
        self.assertEqual([e["stage"] for e in starts],
                         ["download", "transcribe", "merge"])
        self.assertEqual([e["stage"] for e in dones],
                         ["transcribe", "merge"])
        self.assertTrue(any(e["event"] == "succeeded" for e in events))

    def test_cancellation_raises_and_marks_job(self):
        from mediascribe.progress import JobCancelled, JobProgress, with_progress

        job = JobProgress(job_id="j1", url="u")
        # Pre-cancel so the first stage boundary aborts immediately
        job.cancelled = True
        pipeline = mock.MagicMock()
        runner = with_progress(pipeline, job, "u")
        with self.assertRaises(JobCancelled):
            runner()
        # Job is still marked as finished in the sense that
        # cancellation_done was emitted.
        ev_types = []
        while not job.events.empty():
            ev_types.append(job.events.get_nowait()["event"])
        self.assertIn("stage_start", ev_types)
        self.assertIn("cancelled_done", ev_types)

    def test_pipeline_exception_records_error(self):
        from mediascribe.progress import JobProgress, with_progress

        job = JobProgress(job_id="j1", url="u")
        pipeline = mock.MagicMock()
        pipeline.transcribe.side_effect = RuntimeError("boom")
        runner = with_progress(pipeline, job, "u")
        with self.assertRaises(RuntimeError):
            runner()
        self.assertTrue(job.finished)
        self.assertIn("boom", job.error)


# ---------------------------------------------------------------------------
# JobCancelled
# ---------------------------------------------------------------------------


class TestJobCancelled(unittest.TestCase):
    def test_is_exception(self):
        from mediascribe.progress import JobCancelled

        with self.assertRaises(JobCancelled):
            raise JobCancelled("test")


if __name__ == "__main__":
    unittest.main()
