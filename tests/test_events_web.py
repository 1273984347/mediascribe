"""Web read/export/clear APIs for local-only events."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestEventsWebApi(unittest.TestCase):
    def setUp(self):
        try:
            import fastapi  # noqa: F401
        except ImportError:
            self.skipTest("fastapi not installed")
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ,
            {
                "MEDIASCRIBE_WORKSPACE": self.temp.name,
                "MEDIASCRIBE_WIKI": "0",
                "MEDIASCRIBE_EVENT_DB": str(Path(self.temp.name) / "events.sqlite3"),
            },
        )
        self.env.start()
        self.app = create_app(workspace=Path(self.temp.name))
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        store = getattr(self.app.state, "job_store", None)
        if store is not None:
            store.close()
        self.env.stop()
        self.temp.cleanup()

    def _store(self):
        from mediascribe.events import EventStore

        return EventStore(Path(self.temp.name) / "events.sqlite3")

    def test_list_returns_summary_and_whitelisted_events(self):
        self._store().record(
            "artifact_opened",
            surface="web",
            properties={"action": "open", "task_id": "job123"},
        )
        response = self.client.get("/api/events")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["summary"]["counts_by_event"], {"artifact_opened": 1})
        self.assertEqual(data["events"][0]["properties"], {"action": "open", "task_id": "job123"})
        self.assertNotIn("url", data["events"][0])
        self.assertNotIn("path", data["events"][0])

    def test_export_returns_downloadable_json(self):
        self._store().record("artifact_opened", surface="web", properties={"action": "download"})
        response = self.client.get("/api/events/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.json()["events"][0]["event"], "artifact_opened")

    def test_clear_removes_events_but_preserves_identity_and_files(self):
        artifact = Path(self.temp.name) / "keep.md"
        artifact.write_text("keep", encoding="utf-8")
        store = self._store()
        store.record("artifact_opened", surface="web", properties={"action": "copy"})
        install = store.install_id()
        response = self.client.delete("/api/events")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cleared"], 1)
        self.assertEqual(store.list_events(), [])
        self.assertEqual(store.install_id(), install)
        self.assertEqual(artifact.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
