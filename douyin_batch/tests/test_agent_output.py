"""
Unit tests for the agent_output module (JSON output for AI agents).

These tests are run by `run_tests.py` automatically via unittest discovery.
"""
import io
import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from douyin_batch.agent_output import SCHEMA_VERSION, AgentOutput


class TestAgentOutput(unittest.TestCase):
    """Test the structured JSON output format used by AI agents."""

    def setUp(self):
        self.out = AgentOutput(command="douyin_batch_v3")
        self.out.set_config({"max_videos": 5, "workers": 1, "headless": True})
        self.out.set_user_url("https://www.douyin.com/user/test")
        self.out.add_video({
            "video_id": "v1", "url": "https://...", "status": "success",
            "stage": None, "transcript": "out/v1.md", "audio": "out/v1.mp4", "error": None
        })
        self.out.add_video({
            "video_id": "v2", "url": "https://...", "status": "failed",
            "stage": "download", "transcript": None, "audio": None,
            "error": "404 not found"
        })
        self.out.set_summary_report("out/reports/summary.md")
        self.out.add_error("test", "test error")
        self.out.finish(ok=False)

    def test_01_schema_version(self):
        d = self.out.to_dict()
        self.assertEqual(d["schema"], SCHEMA_VERSION)
        self.assertTrue(d["schema"].startswith("video2text.agent-output/"))
        self.assertIn("v1", SCHEMA_VERSION, "schema must be stable v1")

    def test_02_required_fields(self):
        d = self.out.to_dict()
        required = [
            "schema", "ok", "command", "version", "started_at", "finished_at",
            "elapsed_seconds", "config", "user_url", "videos", "stats",
            "summary_report", "errors",
        ]
        for k in required:
            self.assertIn(k, d, f"Missing field: {k}")

    def test_03_stats_calculation(self):
        d = self.out.to_dict()
        self.assertEqual(d["stats"]["total"], 2)
        self.assertEqual(d["stats"]["success"], 1)
        self.assertEqual(d["stats"]["failed"], 1)
        self.assertEqual(d["stats"]["skipped"], 0)

    def test_04_json_serialisation(self):
        buf = io.StringIO()
        self.out.emit(stream=buf)
        serialised = buf.getvalue()
        # Must parse cleanly
        parsed = json.loads(serialised)
        self.assertEqual(parsed["schema"], SCHEMA_VERSION)
        self.assertGreater(len(serialised), 100, "JSON should not be empty")

    def test_05_save_to_file(self):
        test_path = PROJECT_ROOT / "test_agent_output.json"
        try:
            self.out.save(test_path)
            self.assertTrue(test_path.exists())
            with open(test_path, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["schema"], SCHEMA_VERSION)
        finally:
            test_path.unlink(missing_ok=True)

    def test_06_all_status_values_accepted(self):
        for status in ["success", "failed", "skipped"]:
            o = AgentOutput(command="test")
            o.add_video({"video_id": "x", "status": status, "url": "", "stage": None,
                         "transcript": None, "audio": None, "error": None})
            o.finish(ok=True)
            s = o.to_dict()["stats"]
            self.assertEqual(s[status], 1, f"Status {status} not counted: {s}")

    def test_07_empty_output(self):
        empty = AgentOutput(command="empty")
        empty.finish(ok=True)
        buf = io.StringIO()
        empty.emit(stream=buf)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(
            parsed["stats"],
            {"total": 0, "success": 0, "failed": 0, "skipped": 0, "partial": 0},
        )
        self.assertEqual(parsed["videos"], [])
        self.assertEqual(parsed["errors"], [])

    def test_08_ok_flag_propagates(self):
        o = AgentOutput(command="test")
        o.finish(ok=True)
        self.assertTrue(o.to_dict()["ok"])
        o.finish(ok=False)
        self.assertFalse(o.to_dict()["ok"])

    def test_09_config_persisted(self):
        d = self.out.to_dict()
        self.assertEqual(d["config"]["max_videos"], 5)
        self.assertEqual(d["config"]["workers"], 1)
        self.assertTrue(d["config"]["headless"])

    def test_10_user_url_persisted(self):
        d = self.out.to_dict()
        self.assertEqual(d["user_url"], "https://www.douyin.com/user/test")

    def test_11_summary_report_persisted(self):
        d = self.out.to_dict()
        self.assertEqual(d["summary_report"], "out/reports/summary.md")

    def test_12_errors_recorded(self):
        d = self.out.to_dict()
        self.assertEqual(len(d["errors"]), 1)
        self.assertEqual(d["errors"][0]["kind"], "test")
        self.assertIn("at", d["errors"][0])
        self.assertIn("message", d["errors"][0])

    def test_13_chinese_in_output(self):
        """Output should preserve Unicode (Chinese, emoji, etc.) for international use."""
        o = AgentOutput(command="test")
        o.add_video({
            "video_id": "v1", "url": "", "status": "success",
            "stage": None, "transcript": "测试字幕.md",
            "audio": "音频.mp4", "error": "测试错误"
        })
        o.finish(ok=True)
        buf = io.StringIO()
        o.emit(stream=buf)
        text = buf.getvalue()
        self.assertIn("测试字幕.md", text)
        self.assertIn("测试错误", text)

    def test_14_schema_version_is_stable_v1(self):
        """Schema must be stable v1 for AI agent compatibility."""
        self.assertEqual(SCHEMA_VERSION, "video2text.agent-output/v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
