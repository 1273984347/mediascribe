"""Tests for v3.2.0a WebSocket-friendly job progress module."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _valid_result(path: Path) -> SimpleNamespace:
    path.write_text("# Transcript\n\nhello\n", encoding="utf-8")
    return SimpleNamespace(
        engine="whisper",
        model="small",
        transcript_path=path,
        metadata={"engine": "whisper", "model": "small"},
        source=SimpleNamespace(display_name="clip"),
        duration_seconds=1.0,
    )


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
        self.assertFalse(job.progress_known)
        self.assertEqual(job.status, "queued")
        self.assertFalse(job.resumable)
        self.assertIsNone(job.resume_block_reason)
        self.assertFalse(job.cancel_requested)
        self.assertEqual(job.last_segment_index, -1)
        self.assertEqual(job.last_segment_end, 0.0)
        self.assertEqual(job.committed_segments, 0)
        self.assertEqual(job.resume_count, 0)

    def test_start_stage_emits_event(self):
        from mediascribe.progress import JobProgress

        job = JobProgress(job_id="j1", url="u")
        job.start_stage("download", total=10)
        self.assertEqual(job.stage, "download")
        self.assertEqual(job.stage_index, 2)
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
        job.start_stage("assemble", total=1)
        job.events.get_nowait()
        job.finish_stage()
        ev = job.events.get_nowait()
        self.assertEqual(ev["event"], "stage_done")
        self.assertEqual(ev["current"], 1)
        self.assertEqual(ev["total"], 1)

    def test_cancel_requests_then_waits_for_worker_ack(self):
        from mediascribe.progress import JobProgress

        job = JobProgress(job_id="j1", url="u")
        job.cancel()
        self.assertTrue(job.cancelled)
        self.assertFalse(job.finished)
        requested = job.events.get_nowait()
        self.assertEqual(requested["event"], "cancel_requested")

        job.cancel_done()
        self.assertTrue(job.finished)
        done = job.events.get_nowait()
        self.assertEqual(done["event"], "cancelled")

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
        job.start_stage("transcribe", total=10, known=False)
        job.result = {"out_path": "x.md"}
        d = job.to_dict()
        self.assertEqual(d["job_id"], "j1")
        self.assertEqual(d["stage"], "transcribe")
        self.assertEqual(d["stage_index"], 4)
        self.assertEqual(d["stage_total"], 10)
        self.assertFalse(d["progress_known"])
        self.assertEqual(d["cancelled"], False)
        self.assertEqual(d["finished"], False)
        self.assertEqual(d["status"], "queued")
        self.assertFalse(d["resumable"])
        self.assertIsNone(d["resume_block_reason"])
        self.assertFalse(d["cancel_requested"])
        self.assertEqual(d["last_segment_index"], -1)
        self.assertEqual(d["last_segment_end"], 0.0)
        self.assertEqual(d["committed_segments"], 0)
        self.assertEqual(d["resume_count"], 0)
        self.assertEqual(d["result"], {"out_path": "x.md"})

    def test_stages_in_order(self):
        from mediascribe.progress import STAGES

        self.assertEqual(
            STAGES,
            ("preflight", "model_prepare", "download", "extract_audio", "transcribe", "assemble"),
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

    def test_upsert_hydrates_projection_without_replaying_events(self):
        from mediascribe.progress import ProgressRegistry

        registry = ProgressRegistry()
        snapshot = {
            "job_id": "persisted",
            "url": "https://example.com/persisted",
            "created_at": 123.5,
            "attempt_id": "attempt-2",
            "retry_of": "attempt-1",
            "status": "interrupted",
            "stage": "transcribe",
            "stage_index": 4,
            "stage_current": 8,
            "stage_total": 10,
            "progress_known": True,
            "cancelled": False,
            "finished": True,
            "error": "interrupted",
            "error_code": "INTERRUPTED",
            "recovery": {"reason": "interrupted"},
            "result": {"out_path": "x.md"},
            "resumable": True,
            "resume_block_reason": None,
            "cancel_requested": False,
            "last_segment_index": 7,
            "last_segment_end": 12.5,
            "committed_segments": 8,
            "resume_count": 1,
        }

        job = registry.upsert(snapshot)

        self.assertIs(registry.get("persisted"), job)
        self.assertEqual(job.created_at, 123.5)
        for key, value in snapshot.items():
            if key not in {"job_id", "url", "created_at"}:
                self.assertEqual(getattr(job, key), value)
        self.assertTrue(job.events.empty())

        snapshot["status"] = "running"
        snapshot["finished"] = False
        updated = registry.upsert(snapshot)
        self.assertIs(updated, job)
        self.assertEqual(updated.status, "running")
        self.assertFalse(updated.finished)
        self.assertTrue(updated.events.empty())

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
        self.assertEqual(events[-1], "succeeded", "终态事件必须保留在队尾(丢最旧策略)")


# ---------------------------------------------------------------------------
# with_progress — pipeline wrapper
# ---------------------------------------------------------------------------


class TestWithProgress(unittest.TestCase):
    def test_runs_through_all_stages_on_success(self):
        from mediascribe.progress import JobProgress, with_progress

        job = JobProgress(job_id="j1", url="u")
        pipeline = mock.MagicMock()
        from mediascribe.progress import STAGES

        def fake_transcribe(url, **kwargs):
            callback = kwargs["progress_cb"]
            for stage in STAGES:
                callback(stage, 0.0)
                callback(stage, 100.0)
            return result

        pipeline.transcribe.side_effect = fake_transcribe

        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "x.md"
            result = _valid_result(artifact)
            runner = with_progress(pipeline, job, "u", out_path=artifact)
            out = runner()

        self.assertEqual(out, result)
        self.assertFalse(job.finished)
        # Drain the events queue
        events = []
        while not job.events.empty():
            events.append(job.events.get_nowait())
        starts = [e for e in events if e["event"] == "stage_start"]
        dones = [e for e in events if e["event"] == "stage_done"]
        self.assertEqual([e["stage"] for e in starts], list(STAGES))
        self.assertEqual([e["stage"] for e in dones], list(STAGES))
        self.assertTrue(all(e["progress_known"] is False for e in starts))
        self.assertFalse(any(e["event"] == "succeeded" for e in events))

    def test_cancellation_raises_and_marks_job(self):
        from mediascribe.progress import JobCancelled, JobProgress, with_progress

        job = JobProgress(job_id="j1", url="u")
        # Pre-cancel so the first stage boundary aborts immediately
        job.cancelled = True
        pipeline = mock.MagicMock()
        runner = with_progress(pipeline, job, "u")
        with self.assertRaises(JobCancelled):
            runner()
        self.assertFalse(job.finished)
        ev_types = []
        while not job.events.empty():
            ev_types.append(job.events.get_nowait()["event"])
        self.assertNotIn("cancelled", ev_types)
        self.assertNotIn("succeeded", ev_types)

    def test_first_measured_progress_upgrades_boundary_only_stage(self):
        import tempfile

        from mediascribe.progress import JobProgress, with_progress

        job = JobProgress(job_id="j", url="u")
        job.start_stage("transcribe", total=1, known=False)
        while not job.events.empty():
            job.events.get_nowait()

        class FakePipeline:
            def transcribe(self, url, **kwargs):
                kwargs["progress_cb"]("transcribe", 50.0)
                return _valid_result(Path(tempfile.mkdtemp()) / "x.md")

        with_progress(FakePipeline(), job, "u")()
        self.assertEqual(job.stage_total, 100)
        self.assertTrue(job.progress_known)
        event = job.events.get_nowait()
        while not job.events.empty():
            event = job.events.get_nowait()
        self.assertEqual(event["event"], "stage_progress")
        self.assertEqual(event["current"], 50)
        self.assertEqual(event["total"], 100)
        self.assertTrue(event["progress_known"])

    def test_with_progress_forwards_checkpoint_callbacks_and_resume_start(self):
        import tempfile

        from mediascribe.progress import JobProgress, with_progress

        job = JobProgress(job_id="j", url="u")
        seen = {}

        class FakePipeline:
            def transcribe(self, url, **kwargs):
                seen.update(kwargs)
                kwargs["audio_path_cb"](Path("audio.wav"))
                kwargs["decode_params_cb"]({"initial_prompt": "terms"})
                kwargs["segment_cb"]({"text": "hello", "start": 0.0, "end": 1.0})
                return _valid_result(Path(tempfile.mkdtemp()) / "x.md")

        runner = with_progress(
            FakePipeline(),
            job,
            "u",
            segment_cb=lambda segment: None,
            resume_start=4.0,
            audio_path_cb=lambda path: None,
            decode_params_cb=lambda payload: None,
        )
        runner()
        assert seen["resume_start"] == 4.0
        assert callable(seen["segment_cb"])
        assert callable(seen["audio_path_cb"])
        assert callable(seen["decode_params_cb"])

    def test_pipeline_exception_propagates_without_terminal_write(self):
        from mediascribe.progress import JobProgress, with_progress

        job = JobProgress(job_id="j1", url="u")
        pipeline = mock.MagicMock()
        pipeline.transcribe.side_effect = RuntimeError("boom")
        runner = with_progress(pipeline, job, "u")
        with self.assertRaises(RuntimeError):
            runner()
        self.assertFalse(job.finished)
        self.assertIsNone(job.error)


# ---------------------------------------------------------------------------
# JobCancelled
# ---------------------------------------------------------------------------


class TestJobCancelled(unittest.TestCase):
    def test_is_exception(self):
        from mediascribe.progress import JobCancelled

        with self.assertRaises(JobCancelled):
            raise JobCancelled("test")


###########################################################################
# 迁入自 tests/test_coverage_gaps_3.py —— TestProgressRegistryHelpers(mediascribe.progress)
###########################################################################


class TestProgressRegistryHelpers:
    def test_purge_does_not_remove_finished_recently(self) -> None:
        from mediascribe.progress import ProgressRegistry

        reg = ProgressRegistry()
        job = reg.create("https://example.com/v")
        job.succeed(result={"text": "ok"})
        # Default purge(older_than_seconds=3600) keeps fresh entries
        assert reg.purge() == 0
        assert reg.get(job.job_id) is not None

    def test_purge_removes_old_finished(self) -> None:
        from mediascribe.progress import ProgressRegistry

        reg = ProgressRegistry()
        job = reg.create("https://example.com/v")
        job.succeed(result={"text": "ok"})
        # Backdate created_at so older_than_seconds=0 catches it
        for j in reg._jobs.values():
            j.finished = True
            j.created_at = j.created_at - 100
        # older_than_seconds=0 catches everything old
        assert reg.purge(older_than_seconds=0) >= 1


class TestWithProgressCancelEvent(unittest.TestCase):
    """R-1: cancel_event 在 stage 边界预检,并透传给 pipeline.transcribe。"""

    def test_preset_event_blocks_before_transcribe(self):
        import threading

        from mediascribe.progress import JobCancelled, JobProgress, with_progress

        job = JobProgress(job_id="j1", url="u")
        evt = threading.Event()
        evt.set()
        called = []

        class FakePipeline:
            def transcribe(self, url, **kw):
                called.append(kw)
                return "OK"

        runner = with_progress(FakePipeline(), job, "u", cancel_event=evt)
        with self.assertRaises(JobCancelled):
            runner()
        self.assertEqual(called, [], "取消预置位时转录不得启动")

    def test_event_passed_into_transcribe_kwargs(self):
        import threading

        from mediascribe.progress import JobProgress, with_progress

        job = JobProgress(job_id="j2", url="u")
        evt = threading.Event()
        seen = []

        with tempfile.TemporaryDirectory() as td:
            result = _valid_result(Path(td) / "result.md")

            class FakePipeline:
                def transcribe(self, url, **kw):
                    seen.append(kw)
                    return result

            runner = with_progress(FakePipeline(), job, "u", cancel_event=evt)
            runner()
        self.assertIs(seen[0].get("cancel_event"), evt)


if __name__ == "__main__":
    unittest.main()
