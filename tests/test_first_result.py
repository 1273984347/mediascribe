"""First valid transcript result contract across CLI and Web."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mediascribe.models import SourceRef, TranscriptResult
from mediascribe.progress import ProgressRegistry
from mediascribe.web.job_runner import _store_job_result


def _result(tmp: Path) -> TranscriptResult:
    transcript = tmp / "result.md"
    transcript.write_text("# Transcript\n\nhello world\n", encoding="utf-8")
    metadata = tmp / "result.json"
    metadata.write_text(
        json.dumps({"engine": "faster-whisper", "model": "small"}), encoding="utf-8"
    )
    source = SourceRef(raw_input="clip.wav", kind="audio", path=tmp / "clip.wav")
    return TranscriptResult(
        source=source,
        engine="faster-whisper",
        model="small",
        text="hello world",
        audio_path=source.path,
        transcript_path=transcript,
        metadata_path=metadata,
        metadata={"title": "First transcript", "engine": "faster-whisper", "model": "small"},
        duration_seconds=12.5,
    )


class TestProgressResultValidation(unittest.TestCase):
    def test_progress_never_marks_invalid_result_successful(self):
        from mediascribe.progress import with_progress

        with tempfile.TemporaryDirectory() as td:
            result = _result(Path(td))
            result.transcript_path.write_text("", encoding="utf-8")
            registry = ProgressRegistry()
            from mediascribe.web.job_control import JobControl
            from mediascribe.web.job_store import JobStore

            control = JobControl(JobStore(Path(td) / "jobs.sqlite3"), registry)
            job = control.submit(
                "clip.wav", request={}, selection={}, out_path=result.transcript_path
            )
            control.start_attempt(job.job_id)
            pipeline = SimpleNamespace(transcribe=lambda *a, **kw: result)
            runner = with_progress(pipeline, job, "clip.wav", out_path=result.transcript_path)
            with self.assertRaises(Exception) as exc:
                runner()
            self.assertEqual(
                getattr(exc.exception, "code", None),
                "RESULT_INVALID",
                f"unexpected exception: {exc.exception!r}",
            )
            self.assertFalse(job.finished)
            self.assertIsNone(job.error)
            events = []
            while not job.events.empty():
                events.append(job.events.get_nowait())
            self.assertNotIn("succeeded", [event["event"] for event in events])


class TestStoredResult(unittest.TestCase):
    def test_store_contains_validated_summary(self):
        with tempfile.TemporaryDirectory() as td:
            result = _result(Path(td))
            registry = ProgressRegistry()
            from mediascribe.web.job_control import JobControl
            from mediascribe.web.job_store import JobStore

            control = JobControl(JobStore(Path(td) / "jobs.sqlite3"), registry)
            job = control.submit(
                "clip.wav", request={}, selection={}, out_path=result.transcript_path
            )
            control.start_attempt(job.job_id)
            results = {}
            _store_job_result(
                lambda: result,
                job.job_id,
                result.transcript_path,
                "clip.wav",
                results,
                threading.Lock(),
                registry,
                control,
            )
            stored = results[job.job_id]
            self.assertTrue(stored["markdown"].strip())
            self.assertEqual(stored["model"], "small")
            self.assertEqual(stored["input"], "clip")
            self.assertEqual(stored["duration_seconds"], 12.5)
            self.assertEqual(stored["artifact"]["name"], "result.md")
            self.assertNotIn("path", stored["artifact"])
            self.assertNotIn(str(result.transcript_path), json.dumps(stored["artifact"]))

    def test_store_rejects_empty_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            result = _result(Path(td))
            result.transcript_path.write_text("  \n", encoding="utf-8")
            registry = ProgressRegistry()
            from mediascribe.web.job_control import JobControl
            from mediascribe.web.job_store import JobStore

            control = JobControl(JobStore(Path(td) / "jobs.sqlite3"), registry)
            job = control.submit(
                "clip.wav", request={}, selection={}, out_path=result.transcript_path
            )
            control.start_attempt(job.job_id)
            results = {}
            _store_job_result(
                lambda: result,
                job.job_id,
                result.transcript_path,
                "clip.wav",
                results,
                threading.Lock(),
                registry,
                control,
            )
            self.assertEqual(results[job.job_id]["error_code"], "RESULT_INVALID")
            self.assertFalse(results[job.job_id].get("markdown"))

    def test_store_rejects_missing_selection_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            result = _result(Path(td))
            result.metadata = {"title": "First transcript"}
            registry = ProgressRegistry()
            from mediascribe.web.job_control import JobControl
            from mediascribe.web.job_store import JobStore

            control = JobControl(JobStore(Path(td) / "jobs.sqlite3"), registry)
            job = control.submit(
                "clip.wav", request={}, selection={}, out_path=result.transcript_path
            )
            control.start_attempt(job.job_id)
            results = {}
            _store_job_result(
                lambda: result,
                job.job_id,
                result.transcript_path,
                "clip.wav",
                results,
                threading.Lock(),
                registry,
                control,
            )
            self.assertEqual(results[job.job_id]["error_code"], "RESULT_INVALID")

    def test_store_rejects_metadata_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            result = _result(Path(td))
            result.metadata["model"] = "medium"
            registry = ProgressRegistry()
            from mediascribe.web.job_control import JobControl
            from mediascribe.web.job_store import JobStore

            control = JobControl(JobStore(Path(td) / "jobs.sqlite3"), registry)
            job = control.submit(
                "clip.wav", request={}, selection={}, out_path=result.transcript_path
            )
            control.start_attempt(job.job_id)
            results = {}
            _store_job_result(
                lambda: result,
                job.job_id,
                result.transcript_path,
                "clip.wav",
                results,
                threading.Lock(),
                registry,
                control,
            )
            self.assertEqual(results[job.job_id]["error_code"], "RESULT_INVALID")


class TestCliSummary(unittest.TestCase):
    def test_next_steps_prints_actual_selection_duration_and_path(self):
        from mediascribe.__main__ import _print_next_steps

        with tempfile.TemporaryDirectory() as td:
            result = _result(Path(td))
            with mock.patch("builtins.print") as output:
                _print_next_steps(result)
            text = "\n".join(str(call.args[0]) for call in output.call_args_list)
            self.assertIn("faster-whisper", text)
            self.assertIn("small", text)
            self.assertIn("12.5", text)
            self.assertIn("result.md", text)


class TestWebResultActions(unittest.TestCase):
    def setUp(self):
        if not hasattr(__import__("mediascribe.web.app", fromlist=["create_app"]), "create_app"):
            self.skipTest("fastapi not installed")
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ,
            {
                "MEDIASCRIBE_WORKSPACE": self.temp.name,
                "MEDIASCRIBE_WIKI": "0",
                "MEDIASCRIBE_INTERNAL_TEST": "0",
                "MEDIASCRIBE_EVENT_DB": str(Path(self.temp.name) / "events.sqlite3"),
            },
        )
        self.env.start()
        self.app = create_app()
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def _plant_success(self):
        result = _result(Path(self.temp.name))
        job = self.app.state.jobs.create("clip.wav")
        job.succeed({"engine": result.engine})
        self.app.state.job_results[job.job_id] = {
            "markdown": result.transcript_path.read_text(encoding="utf-8"),
            "engine": result.engine,
            "model": result.model,
            "title": "First transcript",
            "input": result.source.display_name,
            "duration_seconds": result.duration_seconds,
            "artifact_path": str(result.transcript_path),
            "artifact": {
                "name": result.transcript_path.name,
                "url": f"/api/jobs/{job.job_id}/artifact",
            },
        }
        return job, result

    def test_result_and_artifact_actions(self):
        job, result = self._plant_success()
        response = self.client.get(f"/api/jobs/{job.job_id}/result")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["summary"]["model"], "small")
        self.assertEqual(data["summary"]["duration_seconds"], 12.5)
        self.assertEqual(data["artifact"]["name"], "result.md")
        artifact = self.client.get(f"/api/jobs/{job.job_id}/artifact")
        self.assertEqual(artifact.status_code, 200)
        self.assertEqual(artifact.content, result.transcript_path.read_bytes())
        self.assertIn("text/markdown", artifact.headers["content-type"])

    def test_artifact_opened_persists_only_whitelisted_local_event(self):
        from mediascribe.events import EventStore

        job, _ = self._plant_success()
        store = EventStore(Path(self.temp.name) / "events.sqlite3")
        self.assertEqual(store.list_events(), [])
        with self.assertNoLogs("mediascribe.web.route_jobs", level=logging.INFO):
            response = self.client.post(
                f"/api/jobs/{job.job_id}/events", json={"event": "artifact_opened"}
            )
        self.assertEqual(response.status_code, 204)
        rows = store.list_events()
        self.assertEqual({row["event"] for row in rows}, {"first_run_started", "artifact_opened"})
        opened = [row for row in rows if row["event"] == "artifact_opened"]
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["surface"], "web")
        self.assertEqual(opened[0]["properties"], {"action": "open", "task_id": job.job_id})
        serialized = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn("clip.wav", serialized)
        self.assertNotIn("First transcript", serialized)

    def test_artifact_event_rejects_extra_content(self):
        job, _ = self._plant_success()
        response = self.client.post(
            f"/api/jobs/{job.job_id}/events",
            json={"event": "artifact_opened", "path": "/private/clip.wav", "text": "secret"},
        )
        self.assertEqual(response.status_code, 422)

    def test_no_speech_has_distinct_status(self):
        from mediascribe.pipeline_stages import NoSpeechError

        job = self.app.state.jobs.create("clip.wav")
        job.fail(str(NoSpeechError()))
        self.app.state.job_results[job.job_id] = {
            "markdown": "",
            "error": str(NoSpeechError()),
            "error_code": "NO_SPEECH",
        }
        data = self.client.get(f"/api/jobs/{job.job_id}/result").json()
        self.assertEqual(data["status"], "no_speech")
        self.assertEqual(data["error_code"], "NO_SPEECH")
