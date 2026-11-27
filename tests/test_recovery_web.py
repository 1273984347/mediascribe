"""Web recovery cards and selection-preserving retry attempts."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mediascribe.model_weights import ModelDownloadError
from mediascribe.models import SourceRef, TranscriptResult


class CodedError(RuntimeError):
    def __init__(self, code: str):
        super().__init__("private diagnostic /secret/path")
        self.code = code


def _valid_result(settings, source: str, output: Path) -> TranscriptResult:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("# Transcript\n\nrecovered text\n", encoding="utf-8")
    metadata_path = output.with_suffix(".json")
    metadata_path.write_text('{"engine":"whisper","model":"small"}', encoding="utf-8")
    return TranscriptResult(
        source=SourceRef(raw_input=source, kind="unknown", url=source),
        engine="whisper",
        model="small",
        text="recovered text",
        audio_path=settings.workspace_root / "audio.wav",
        transcript_path=output,
        metadata_path=metadata_path,
        metadata={"engine": "whisper", "model": "small", "title": "Recovered"},
        duration_seconds=2.0,
    )


class TestWebRecoveryRetry(unittest.TestCase):
    def setUp(self):
        try:
            import fastapi  # noqa: F401
        except ImportError:
            self.skipTest("fastapi not installed")
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app
        from mediascribe.web.pipeline_bridge import _PIPELINE_CACHE

        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.env = mock.patch.dict(
            os.environ,
            {
                "MEDIASCRIBE_ALLOWED_HOSTS": "example.com",
                "MEDIASCRIBE_WIKI": "0",
            },
        )
        self.env.start()
        _PIPELINE_CACHE.clear()
        self.app = create_app(workspace=self.workspace)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        from mediascribe.web.pipeline_bridge import _PIPELINE_CACHE

        self.client.__exit__(None, None, None)
        store = getattr(self.app.state, "job_store", None)
        if store is not None:
            store.close()
        _PIPELINE_CACHE.clear()
        self.env.stop()
        self.temp.cleanup()

    def _wait_terminal(self, job_id: str):
        deadline = time.time() + 5
        while time.time() < deadline:
            row = self.app.state.job_store.get(job_id) or {}
            if row.get("finished"):
                return row
            time.sleep(0.02)
        self.fail(f"job {job_id} did not reach terminal state")

    def _submit(self, **overrides):
        body = {
            "urls": ["https://example.com/video.mp4"],
            "engine": "whisper",
            "model": "small",
            "language": "en",
            "allow_model_download": True,
            "ocr_engine": "none",
            "bilingual": False,
        }
        body.update(overrides)
        response = self.client.post("/api/jobs", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["jobs"][0]["job_id"]

    def test_retry_preserves_request_and_output_with_new_attempt(self):
        settings_seen = []
        outputs = []

        class FakePipeline:
            def __init__(self, settings, **_kwargs):
                self.settings = settings
                self.transcriber = object()
                self._resource_notices = []
                settings_seen.append(settings)

            def transcribe(self, source, **kwargs):
                outputs.append(Path(kwargs["output"]))
                if len(outputs) == 1:
                    raise ModelDownloadError("private token /secret/path")
                return _valid_result(self.settings, source, Path(kwargs["output"]))

        with (
            mock.patch("mediascribe.pipeline.Pipeline", side_effect=FakePipeline),
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
        ):
            original_id = self._submit()
            original_row = self._wait_terminal(original_id)
            original = self.client.get(f"/api/jobs/{original_id}/result").json()
            self.assertEqual(original["error_code"], "MODEL_DOWNLOAD_FAILED")
            self.assertTrue(original["recovery"]["retryable"])
            self.assertEqual(original["recovery"]["scope"], "model")
            self.assertNotIn("/secret/path", json.dumps(original))
            self.assertTrue(original["attempt_id"])

            retry = self.client.post(f"/api/jobs/{original_id}/retry")
            self.assertEqual(retry.status_code, 200, retry.text)
            retry_job = retry.json()["jobs"][0]
            retry_id = retry_job["job_id"]
            self.assertNotEqual(retry_id, original_id)
            self.assertEqual(retry_job["retry_of"], original_id)
            self.assertNotEqual(retry_job["attempt_id"], original["attempt_id"])
            self._wait_terminal(retry_id)
            recovered = self.client.get(f"/api/jobs/{retry_id}/result").json()
            self.assertEqual(recovered["status"], "finished")
            self.assertIn("recovered text", recovered["markdown"])

        self.assertEqual(len(settings_seen), 2)
        for settings in settings_seen:
            self.assertEqual(settings.requested_engine, "whisper")
            self.assertEqual(settings.requested_model, "small")
            self.assertEqual(settings.language, "en")
            self.assertTrue(settings.allow_model_download)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], Path(original_row["out_path"]))
        history = self.client.get(f"/api/jobs/{original_id}/result").json()
        self.assertEqual(history["error_code"], "MODEL_DOWNLOAD_FAILED")
        self.assertEqual(history["error"], history["recovery"]["reason"])
        self.assertNotIn("/secret/path", json.dumps(history))

    def test_irrecoverable_error_rejects_retry_without_new_attempt(self):
        calls = []

        class FakePipeline:
            def __init__(self, settings, **_kwargs):
                self.settings = settings
                self.transcriber = object()
                self._resource_notices = []

            def transcribe(self, source, **kwargs):
                calls.append((source, kwargs["output"]))
                raise CodedError("INPUT_INVALID")

        with (
            mock.patch("mediascribe.pipeline.Pipeline", side_effect=FakePipeline),
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
        ):
            job_id = self._submit()
            self._wait_terminal(job_id)
            result = self.client.get(f"/api/jobs/{job_id}/result").json()
            self.assertFalse(result["recovery"]["retryable"])
            self.assertIn("edit_input", [a["id"] for a in result["recovery"]["actions"]])
            response = self.client.post(f"/api/jobs/{job_id}/retry")
            self.assertEqual(response.status_code, 409)
        self.assertEqual(len(calls), 1)

    def test_running_job_rejects_retry(self):
        release = threading.Event()

        class FakePipeline:
            def __init__(self, settings, **_kwargs):
                self.settings = settings
                self.transcriber = object()
                self._resource_notices = []

            def transcribe(self, source, **kwargs):
                release.wait(2)
                raise ModelDownloadError("network reset")

        with (
            mock.patch("mediascribe.pipeline.Pipeline", side_effect=FakePipeline),
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
        ):
            job_id = self._submit()
            try:
                response = self.client.post(f"/api/jobs/{job_id}/retry")
                self.assertEqual(response.status_code, 409)
            finally:
                release.set()
                self._wait_terminal(job_id)

    def test_submit_environment_error_contains_recovery(self):
        with mock.patch("mediascribe.config.find_spec", return_value=None):
            response = self.client.post(
                "/api/jobs", json={"urls": ["https://example.com/video.mp4"]}
            )
        self.assertEqual(response.status_code, 503)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "ENGINE_UNAVAILABLE")
        self.assertEqual(detail["recovery"]["scope"], "environment")
        self.assertIn("install_engine", [a["id"] for a in detail["recovery"]["actions"]])
        self.assertIn("ENGINE_UNAVAILABLE", detail["error"])

    def test_submit_input_error_contains_recovery(self):
        response = self.client.post(
            "/api/jobs", json={"urls": [str(self.workspace / "missing.wav")]}
        )
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "INPUT_INVALID")
        self.assertFalse(detail["recovery"]["retryable"])
        self.assertIn("edit_input", [a["id"] for a in detail["recovery"]["actions"]])

    def test_sync_transcribe_failure_item_contains_recovery(self):
        with (
            mock.patch("mediascribe.pipeline.Pipeline") as pipeline,
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
        ):
            pipeline.return_value.transcribe.side_effect = ModelDownloadError(
                "private token /secret/path"
            )
            response = self.client.post(
                "/api/transcribe", json={"urls": ["https://example.com/video.mp4"]}
            )
        self.assertEqual(response.status_code, 200)
        item = response.json()["results"][0]
        self.assertFalse(item["ok"])
        self.assertEqual(item["error_code"], "MODEL_DOWNLOAD_FAILED")
        self.assertTrue(item["recovery"]["retryable"])
        self.assertNotIn("/secret/path", json.dumps(item))

    def test_sync_transcribe_validation_error_contains_recovery(self):
        response = self.client.post(
            "/api/transcribe", json={"urls": [str(self.workspace / "missing.wav")]}
        )
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "INPUT_INVALID")
        self.assertIn("edit_input", [a["id"] for a in detail["recovery"]["actions"]])

    def test_unknown_job_retry_returns_404(self):
        response = self.client.post("/api/jobs/does-not-exist/retry")
        self.assertEqual(response.status_code, 404)

    def test_job_listing_exposes_recovery_and_attempt_identity(self):
        class FakePipeline:
            def __init__(self, settings, **_kwargs):
                self.settings = settings
                self.transcriber = object()
                self._resource_notices = []

            def transcribe(self, source, **kwargs):
                raise RuntimeError("private implementation detail")

        with (
            mock.patch("mediascribe.pipeline.Pipeline", side_effect=FakePipeline),
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
        ):
            job_id = self._submit()
            self._wait_terminal(job_id)
            jobs = self.client.get("/api/jobs").json()["jobs"]
        job = next(item for item in jobs if item["job_id"] == job_id)
        self.assertTrue(job["attempt_id"])
        self.assertEqual(job["error_code"], "UNKNOWN")
        self.assertEqual(job["recovery"]["category"], "UNKNOWN")
        self.assertFalse(job["recovery"]["retryable"])


if __name__ == "__main__":
    unittest.main()
