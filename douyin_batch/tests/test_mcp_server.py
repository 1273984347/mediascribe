"""
Unit tests for the MCP server.
"""

import json
import os
import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestMCPServer(unittest.TestCase):
    """Test the MCP server's JSON-RPC handling and tool dispatch."""

    def _request(self, method, params=None, req_id=1):
        from mediascribe.mcp_server import _handle_request

        req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        return _handle_request(req)

    # ---- Protocol ----

    def test_initialize(self):
        r = self._request("initialize")
        self.assertIn("result", r)
        self.assertEqual(r["result"]["serverInfo"]["name"], "mediascribe")
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
        for required in (
            "transcribe_video",
            "batch_transcribe_creator",
            "get_cache_stats",
            "validate_url",
            "sanitize_filename",
            "detect_platform",
            "get_transcript",
        ):
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
        r = self._request(
            "tools/call", {"name": "validate_url", "arguments": {"url": "https://www.bilibili.com"}}
        )
        self.assertFalse(r["result"]["isError"])
        import json as _json

        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertTrue(body["safe"])

    def test_tool_validate_url_unsafe(self):
        r = self._request(
            "tools/call", {"name": "validate_url", "arguments": {"url": "javascript:alert(1)"}}
        )
        self.assertFalse(r["result"]["isError"])
        import json as _json

        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertFalse(body["safe"])

    def test_tool_sanitize_filename(self):
        r = self._request(
            "tools/call",
            {"name": "sanitize_filename", "arguments": {"name": 'test<>:"|?*file.mp4'}},
        )
        self.assertFalse(r["result"]["isError"])
        import json as _json

        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertNotIn("<", body["safe"])
        self.assertNotIn(">", body["safe"])

    def test_tool_detect_platform_bilibili(self):
        r = self._request(
            "tools/call",
            {
                "name": "detect_platform",
                "arguments": {"url": "https://www.bilibili.com/video/BV1xxx"},
            },
        )
        import json as _json

        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(body["platform"], "bilibili")

    def test_tool_detect_platform_douyin(self):
        r = self._request(
            "tools/call",
            {"name": "detect_platform", "arguments": {"url": "https://v.douyin.com/xxx"}},
        )
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
        r = self._request(
            "tools/call",
            {
                "name": "get_transcript",
                "arguments": {"path": "output/transcripts/__no_such_file__.md"},
            },
        )
        self.assertFalse(r["result"]["isError"])
        import json as _json

        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertFalse(body["exists"])

    def test_tool_get_transcript_blocks_path_traversal(self):
        """Regression: a malicious caller must not be able to read /etc/passwd."""
        r = self._request(
            "tools/call", {"name": "get_transcript", "arguments": {"path": "/no/such/file.md"}}
        )
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

    def _fake_transcript_result(
        self,
        *,
        audio="output/audio/test.wav",
        transcript="output/transcripts/test.md",
        video="output/downloads/test.mp4",
        engine="whisper",
        model="small",
    ):
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
        with patch("mediascribe.config.Settings") as MockSettings, patch(
            "mediascribe.pipeline.Pipeline", return_value=fake_pipeline
        ) as MockPipeline:
            MockSettings.return_value = MagicMock(name="SettingsInstance")
            r = self._request(
                "tools/call",
                {
                    "name": "transcribe_video",
                    "arguments": {
                        "source": "https://www.bilibili.com/video/BV1abc",
                        "language": "zh",
                        "whisper_model": "small",
                    },
                },
            )

        # The tool call must succeed (no isError) and the parsed body must
        # expose a coherent shape.
        self.assertFalse(r["result"]["isError"], r["result"])
        import json as _json

        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(body["source"], "https://www.bilibili.com/video/BV1abc")
        # Path may be normalized to the host's native separator.
        self.assertTrue(
            body["transcript"].endswith("output/transcripts/test.md")
            or body["transcript"].endswith("output\\transcripts\\test.md"),
            body["transcript"],
        )
        self.assertTrue(
            body["audio"].endswith("output/audio/test.wav")
            or body["audio"].endswith("output\\audio\\test.wav"),
            body["audio"],
        )
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
        r = self._request(
            "tools/call",
            {
                "name": "transcribe_video",
                "arguments": {"language": "en"},
            },
        )
        # The MCP dispatcher wraps exceptions as isError=true.
        self.assertTrue(r["result"]["isError"])
        self.assertIn("source", r["result"]["content"][0]["text"].lower())

    def test_transcribe_video_language_auto_normalised_to_none(self):
        """``language='auto'`` is a UI affordance — internally it's ``None``."""
        from unittest.mock import MagicMock, patch

        fake_result = self._fake_transcript_result()
        fake_pipeline = MagicMock()
        fake_pipeline.transcribe.return_value = fake_result

        with patch("mediascribe.config.Settings") as MockSettings, patch(
            "mediascribe.pipeline.Pipeline", return_value=fake_pipeline
        ):
            MockSettings.return_value = MagicMock()
            self._request(
                "tools/call",
                {
                    "name": "transcribe_video",
                    "arguments": {"source": "https://example.com/v", "language": "auto"},
                },
            )

        call_kwargs = fake_pipeline.transcribe.call_args.kwargs
        self.assertIsNone(call_kwargs["language"])
        # And the public ``language`` field in the result stays human-friendly.
        # (we re-invoke to inspect the body)
        with patch("mediascribe.config.Settings") as MockSettings2, patch(
            "mediascribe.pipeline.Pipeline", return_value=fake_pipeline
        ):
            MockSettings2.return_value = MagicMock()
            r = self._request(
                "tools/call",
                {
                    "name": "transcribe_video",
                    "arguments": {"source": "https://example.com/v", "language": "auto"},
                },
            )
        import json as _json

        body = _json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(body["language"], "auto")


class TestMCPServerStdio(unittest.TestCase):
    """Test the stdio JSON-RPC server by simulating a JSON-RPC client."""

    def test_stdio_round_trip(self):
        import io

        from mediascribe.mcp_server import _run_stdio

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

    def test_stdio_survives_malformed_request(self):
        """P1-4④: 畸形请求返回 error 对象, 主循环继续服务后续请求。"""
        import io

        from mediascribe.mcp_server import _run_stdio

        lines = (
            json.dumps([1, 2, 3])
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
            + "\n"
        )
        stdin = io.StringIO(lines)
        stdout = io.StringIO()
        sys.stdin = stdin
        sys.stdout = stdout
        try:
            _run_stdio()
        finally:
            sys.stdin = sys.__stdin__
            sys.stdout = sys.__stdout__
        out = stdout.getvalue()
        self.assertIn('"error"', out.splitlines()[0])
        self.assertIn('"result"', out.splitlines()[1])


# ---------------------------------------------------------------------------
# P1-4⑤ — malformed request objects
# ---------------------------------------------------------------------------
class TestMCPRequestHardening(unittest.TestCase):
    def _request(self, method, params=None, req_id=1):
        from mediascribe.mcp_server import _handle_request

        req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        return _handle_request(req)

    def test_non_dict_request_returns_error(self):
        from mediascribe.mcp_server import _handle_request

        r = _handle_request([1, 2, 3])
        self.assertIn("error", r)
        self.assertEqual(r["error"]["code"], -32600)

    def test_non_dict_params_returns_error(self):
        r = self._request("tools/call", ["not", "a", "dict"])
        self.assertIn("error", r)
        self.assertEqual(r["error"]["code"], -32602)

    def test_safe_handle_request_never_raises(self):
        from mediascribe.mcp_server import _safe_handle_request

        for bad in ([], "string", 42, None, {"method": "tools/call", "params": 7}):
            r = _safe_handle_request(bad)
            self.assertIsInstance(r, dict, repr(bad))


# ---------------------------------------------------------------------------
# P1-4①②③ — HTTP transport security (token / Host allowlist / body cap)
# ---------------------------------------------------------------------------
class TestMCPHttpTransportSecurity(unittest.TestCase):
    """Spin up the real hardened HTTP server on an ephemeral port."""

    def setUp(self):
        from mediascribe.mcp_server import _make_http_server

        self._saved_token = os.environ.pop("MEDIASCRIBE_MCP_TOKEN", None)
        self._saved_hosts = os.environ.pop("MEDIASCRIBE_MCP_ALLOWED_HOSTS", None)
        self.server = _make_http_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        os.environ.pop("MEDIASCRIBE_MCP_TOKEN", None)
        os.environ.pop("MEDIASCRIBE_MCP_ALLOWED_HOSTS", None)
        if self._saved_token is not None:
            os.environ["MEDIASCRIBE_MCP_TOKEN"] = self._saved_token
        if self._saved_hosts is not None:
            os.environ["MEDIASCRIBE_MCP_ALLOWED_HOSTS"] = self._saved_hosts

    def _post(self, body: bytes, headers=None, host_header=None):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            if host_header is not None:
                conn.putrequest("POST", "/", skip_host=True, skip_accept_encoding=True)
                conn.putheader("Host", host_header)
            else:
                conn.putrequest("POST", "/", skip_accept_encoding=True)
            for key, value in (headers or {}).items():
                conn.putheader(key, value)
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body)))
            conn.endheaders()
            conn.send(body)
            resp = conn.getresponse()
            return resp.status, resp.read().decode("utf-8", "replace")
        finally:
            conn.close()

    def _ping(self) -> bytes:
        return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()

    # ---- no token configured: behaviour unchanged ----

    def test_open_when_no_token_configured(self):
        status, body = self._post(self._ping())
        self.assertEqual(status, 200)
        self.assertIn('"result"', body)

    # ---- token enforcement ----

    def test_missing_token_is_401(self):
        os.environ["MEDIASCRIBE_MCP_TOKEN"] = "s3cret"
        status, _ = self._post(self._ping())
        self.assertEqual(status, 401)

    def test_wrong_token_is_401(self):
        os.environ["MEDIASCRIBE_MCP_TOKEN"] = "s3cret"
        status, _ = self._post(self._ping(), headers={"X-MCP-Token": "wrong"})
        self.assertEqual(status, 401)

    def test_token_via_x_mcp_token_header(self):
        os.environ["MEDIASCRIBE_MCP_TOKEN"] = "s3cret"
        status, body = self._post(self._ping(), headers={"X-MCP-Token": "s3cret"})
        self.assertEqual(status, 200)
        self.assertIn('"result"', body)

    def test_token_via_bearer_authorization(self):
        os.environ["MEDIASCRIBE_MCP_TOKEN"] = "s3cret"
        status, body = self._post(self._ping(), headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(status, 200)
        self.assertIn('"result"', body)

    # ---- Host allowlist (DNS rebinding) ----

    def test_foreign_host_is_403(self):
        status, _ = self._post(self._ping(), host_header="evil.example.com")
        self.assertEqual(status, 403)

    def test_localhost_host_allowed(self):
        status, _ = self._post(self._ping(), host_header=f"localhost:{self.port}")
        self.assertEqual(status, 200)

    def test_extra_allowed_host_via_env(self):
        os.environ["MEDIASCRIBE_MCP_ALLOWED_HOSTS"] = "mcp.internal:443"
        status, _ = self._post(self._ping(), host_header="mcp.internal:443")
        self.assertEqual(status, 200)
        status2, _ = self._post(self._ping(), host_header="mcp.internal:9999")
        self.assertEqual(status2, 403)

    # ---- body size cap ----

    def test_oversized_body_is_413(self):
        big = b'{"pad": "' + b"a" * (1024 * 1024 + 16) + b'"}'
        self.assertGreater(len(big), 1024 * 1024)
        status, _ = self._post(big)
        self.assertEqual(status, 413)

    # ---- malformed JSON ----

    def test_malformed_json_returns_parse_error(self):
        status, body = self._post(b"this is not json")
        self.assertEqual(status, 200)
        self.assertIn("-32700", body)

    def test_malformed_body_does_not_kill_server(self):
        self._post(b"garbage-1")
        self._post(b"garbage-2")
        status, body = self._post(self._ping())
        self.assertEqual(status, 200)
        self.assertIn('"result"', body)


class TestMCPBatchMaxVideosClamp(unittest.TestCase):
    """P1-4⑥: batch 工具的 max_videos 在 handler 层 clamp 到 1-200。"""

    def test_clamp_in_command(self):
        import json as _json
        from unittest import mock

        from mediascribe import mcp_server

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            proc = mock.MagicMock()
            proc.stdout = _json.dumps({"ok": True})
            return proc

        with mock.patch("subprocess.run", side_effect=fake_run):
            r = self._request_tool(
                mcp_server, {"user_url": "https://v.douyin.com/x", "max_videos": 99999}
            )
        self.assertFalse(r["result"]["isError"])
        cmd = captured["cmd"]
        n_index = cmd.index("-n")
        self.assertEqual(cmd[n_index + 1], "200")

    @staticmethod
    def _request_tool(module, arguments):
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "batch_transcribe_creator", "arguments": arguments},
        }
        return module._handle_request(req)

    def test_clamp_lower_bound(self):
        import json as _json
        from unittest import mock

        from mediascribe import mcp_server

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            proc = mock.MagicMock()
            proc.stdout = _json.dumps({"ok": True})
            return proc

        with mock.patch("subprocess.run", side_effect=fake_run):
            r = self._request_tool(
                mcp_server, {"user_url": "https://v.douyin.com/x", "max_videos": -5}
            )
        self.assertFalse(r["result"]["isError"])
        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("-n") + 1], "1")

    def test_non_integer_falls_back_to_default(self):
        import json as _json
        from unittest import mock

        from mediascribe import mcp_server

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            proc = mock.MagicMock()
            proc.stdout = _json.dumps({"ok": True})
            return proc

        with mock.patch("subprocess.run", side_effect=fake_run):
            r = self._request_tool(mcp_server, {"user_url": "u", "max_videos": "abc"})
        self.assertFalse(r["result"]["isError"])
        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("-n") + 1], "10")


if __name__ == "__main__":
    unittest.main(verbosity=2)
