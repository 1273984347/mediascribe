"""
Unit tests for the MCP server.
"""
import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestMCPServer(unittest.TestCase):
    """Test the MCP server's JSON-RPC handling and tool dispatch."""

    def _request(self, method, params=None, req_id=1):
        from video2text.mcp_server import _handle_request
        req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        return _handle_request(req)

    # ---- Protocol ----

    def test_initialize(self):
        r = self._request("initialize")
        self.assertIn("result", r)
        self.assertEqual(r["result"]["serverInfo"]["name"], "video2text")
        self.assertIn("capabilities", r["result"])

    def test_initialized_notification(self):
        r = self._request("notifications/initialized")
        self.assertIsNone(r, "notifications must not produce a response")

    def test_tools_list(self):
        r = self._request("tools/list")
        self.assertIn("result", r)
        tools = r["result"]["tools"]
        self.assertGreater(len(tools), 0)
        names = {t["name"] for t in tools}
        for required in ("transcribe_video", "batch_transcribe_creator",
                         "get_cache_stats", "validate_url", "sanitize_filename",
                         "detect_platform", "get_transcript"):
            self.assertIn(required, names, f"tool {required} missing")

    def test_each_tool_has_schema(self):
        r = self._request("tools/list")
        for tool in r["result"]["tools"]:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_ping(self):
        r = self._request("ping")
        self.assertIn("result", r)

    def test_unknown_method_returns_error(self):
        r = self._request("nonexistent/method")
        self.assertIn("error", r)
        self.assertEqual(r["error"]["code"], -32601)

    # ---- Tools ----

    def test_tool_validate_url_safe(self):
        r = self._request("tools/call", {"name": "validate_url", "arguments": {"url": "https://www.bilibili.com"}})
        self.assertFalse(r["result"]["isError"])
        import json as _json
        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertTrue(body["safe"])

    def test_tool_validate_url_unsafe(self):
        r = self._request("tools/call", {"name": "validate_url", "arguments": {"url": "javascript:alert(1)"}})
        self.assertFalse(r["result"]["isError"])
        import json as _json
        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertFalse(body["safe"])

    def test_tool_sanitize_filename(self):
        r = self._request("tools/call", {"name": "sanitize_filename", "arguments": {"name": "test<>:\"|?*file.mp4"}})
        self.assertFalse(r["result"]["isError"])
        import json as _json
        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertNotIn("<", body["safe"])
        self.assertNotIn(">", body["safe"])

    def test_tool_detect_platform_bilibili(self):
        r = self._request("tools/call", {"name": "detect_platform", "arguments": {"url": "https://www.bilibili.com/video/BV1xxx"}})
        import json as _json
        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(body["platform"], "bilibili")

    def test_tool_detect_platform_douyin(self):
        r = self._request("tools/call", {"name": "detect_platform", "arguments": {"url": "https://v.douyin.com/xxx"}})
        import json as _json
        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertIn(body["platform"], ("douyin", "unknown"))

    def test_tool_unknown_tool_returns_error_in_result(self):
        """MCP spec: tool errors come back via isError=true, not JSON-RPC error."""
        r = self._request("tools/call", {"name": "no_such_tool", "arguments": {}})
        self.assertIn("result", r)
        self.assertTrue(r["result"]["isError"])

    def test_tool_get_transcript_missing_file(self):
        # The path is relative + inside the default transcript root.
        r = self._request("tools/call", {"name": "get_transcript", "arguments": {"path": "output/transcripts/__no_such_file__.md"}})
        self.assertFalse(r["result"]["isError"])
        import json as _json
        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertFalse(body["exists"])

    def test_tool_get_transcript_blocks_path_traversal(self):
        """Regression: a malicious caller must not be able to read /etc/passwd."""
        r = self._request("tools/call", {"name": "get_transcript", "arguments": {"path": "/no/such/file.md"}})
        self.assertFalse(r["result"]["isError"])
        import json as _json
        body = _json.loads(r["result"]["content"][0]["text"])
        # The path is outside the default transcript root, so we must
        # report the containment violation, NOT "file not found".
        self.assertIn("escapes", body.get("error", "").lower())
        # Crucially, the file content must not be present.
        self.assertNotIn("content", body)

    def test_tool_get_transcript_requires_path(self):
        r = self._request("tools/call", {"name": "get_transcript", "arguments": {}})
        self.assertFalse(r["result"]["isError"])
        import json as _json
        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertIn("required", body.get("error", "").lower())

    def test_tool_get_cache_stats(self):
        r = self._request("tools/call", {"name": "get_cache_stats", "arguments": {}})
        self.assertFalse(r["result"]["isError"])
        import json as _json
        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertIn("total", body)
        self.assertIn("success", body)
        self.assertIn("failed", body)

    # ---- Tool: transcribe_video (regression for BUG-1) ----

    def _fake_transcript_result(self, *, audio="output/audio/test.wav",
                                transcript="output/transcripts/test.md",
                                video="output/downloads/test.mp4",
                                engine="whisper", model="small"):
        """Build a stand-in for the real TranscriptResult dataclass."""
        from dataclasses import dataclass, field
        from pathlib import Path as _P
        from typing import Optional as _O

        @dataclass
        class _Stub:
            audio_path: _P = field(default_factory=lambda: _P(audio))
            transcript_path: _P = field(default_factory=lambda: _P(transcript))
            video_path: _O[_P] = field(default_factory=lambda: _P(video) if video else None)
            engine: str = field(default_factory=lambda: engine)
            model: str = field(default_factory=lambda: model)
        return _Stub()

    def test_transcribe_video_uses_real_pipeline_api(self):
        """Regression for BUG-1: the tool must build ``Settings`` and call
        ``Pipeline.transcribe()`` — not the invented ``Pipeline.run()``."""
        from unittest.mock import MagicMock, patch

        fake_result = self._fake_transcript_result()
        fake_pipeline = MagicMock()
        fake_pipeline.transcribe.return_value = fake_result

        # Make Settings / Pipeline pickable but cheap to import.
        with patch("video2text.config.Settings") as MockSettings, \
             patch("video2text.pipeline.Pipeline", return_value=fake_pipeline) as MockPipeline:
            MockSettings.return_value = MagicMock(name="SettingsInstance")
            r = self._request("tools/call", {
                "name": "transcribe_video",
                "arguments": {
                    "source": "https://www.bilibili.com/video/BV1abc",
                    "language": "zh",
                    "whisper_model": "small",
                },
            })

        # The tool call must succeed (no isError) and the parsed body must
        # expose a coherent shape.
        self.assertFalse(r["result"]["isError"], r["result"])
        import json as _json
        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(body["source"], "https://www.bilibili.com/video/BV1abc")
        # Path may be normalized to the host's native separator.
        self.assertTrue(body["transcript"].endswith("output/transcripts/test.md")
                        or body["transcript"].endswith("output\\transcripts\\test.md"),
                        body["transcript"])
        self.assertTrue(body["audio"].endswith("output/audio/test.wav")
                        or body["audio"].endswith("output\\audio\\test.wav"),
                        body["audio"])
        self.assertEqual(body["engine"], "whisper")
        self.assertEqual(body["model"], "small")
        self.assertEqual(body["language"], "zh")

        # The mock must have been called correctly:
        # - Settings was constructed with the expected workspace_root + model
        # - Pipeline was built with that Settings
        # - Pipeline.transcribe was called once with source_input + language
        MockSettings.assert_called_once()
        kwargs = MockSettings.call_args.kwargs
        self.assertEqual(kwargs["model"], "small")
        MockPipeline.assert_called_once()
        self.assertIs(MockPipeline.call_args.kwargs["settings"], MockSettings.return_value)
        fake_pipeline.transcribe.assert_called_once()
        call_kwargs = fake_pipeline.transcribe.call_args.kwargs
        self.assertEqual(
            call_kwargs["source_input"],
            "https://www.bilibili.com/video/BV1abc",
        )
        self.assertEqual(call_kwargs["language"], "zh")

    def test_transcribe_video_missing_source_raises(self):
        """The tool must surface a structured error when no source is given."""
        r = self._request("tools/call", {
            "name": "transcribe_video",
            "arguments": {"language": "en"},
        })
        # The MCP dispatcher wraps exceptions as isError=true.
        self.assertTrue(r["result"]["isError"])
        self.assertIn("source", r["result"]["content"][0]["text"].lower())

    def test_transcribe_video_language_auto_normalised_to_none(self):
        """``language='auto'`` is a UI affordance — internally it's ``None``."""
        from unittest.mock import MagicMock, patch

        fake_result = self._fake_transcript_result()
        fake_pipeline = MagicMock()
        fake_pipeline.transcribe.return_value = fake_result

        with patch("video2text.config.Settings") as MockSettings, \
             patch("video2text.pipeline.Pipeline", return_value=fake_pipeline):
            MockSettings.return_value = MagicMock()
            self._request("tools/call", {
                "name": "transcribe_video",
                "arguments": {"source": "https://example.com/v", "language": "auto"},
            })

        call_kwargs = fake_pipeline.transcribe.call_args.kwargs
        self.assertIsNone(call_kwargs["language"])
        # And the public ``language`` field in the result stays human-friendly.
        # (we re-invoke to inspect the body)
        with patch("video2text.config.Settings") as MockSettings2, \
             patch("video2text.pipeline.Pipeline", return_value=fake_pipeline):
            MockSettings2.return_value = MagicMock()
            r = self._request("tools/call", {
                "name": "transcribe_video",
                "arguments": {"source": "https://example.com/v", "language": "auto"},
            })
        import json as _json
        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(body["language"], "auto")


class TestMCPServerStdio(unittest.TestCase):
    """Test the stdio JSON-RPC server by simulating a JSON-RPC client."""

    def test_stdio_round_trip(self):
        import io

        from video2text.mcp_server import _run_stdio

        # Build a request payload
        req = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        stdin = io.StringIO(json.dumps(req) + "\n")
        stdout = io.StringIO()
        sys.stdin = stdin
        sys.stdout = stdout
        try:
            _run_stdio()
        finally:
            sys.stdin = sys.__stdin__
            sys.stdout = sys.__stdout__
        out = stdout.getvalue()
        # ping returns {} (empty result) per our impl
        self.assertIn('"result"', out)
        # Must not have crashed
        self.assertNotIn('"error"', out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
