"""Stable, bilingual recovery contracts for first-run failures."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from mediascribe.downloaders.download_errors import (
    CATEGORY_BOT_WALL,
    CATEGORY_FATAL,
    CATEGORY_GONE,
    CATEGORY_TRANSIENT,
    DownloadError,
)
from mediascribe.i18n import get_language, set_language
from mediascribe.model_weights import ModelDownloadError


class CodedError(RuntimeError):
    def __init__(self, code: str, message: str = "private diagnostic /secret/path"):
        super().__init__(message)
        self.code = code


def recovery(exc):
    from mediascribe.recovery import recovery_for_exception

    return recovery_for_exception(exc)


class TestRecoveryCategories(unittest.TestCase):
    def test_first_run_error_matrix_has_safe_actions(self):
        cases = [
            ("INPUT_INVALID", "input", False, "edit_input"),
            ("FFMPEG_MISSING", "environment", False, "install_ffmpeg"),
            ("ENGINE_UNAVAILABLE", "environment", False, "install_engine"),
            ("MODEL_NOT_READY", "model", False, "prepare_model"),
            ("MODEL_DOWNLOAD_FAILED", "model", True, "retry"),
            ("DEVICE_UNAVAILABLE", "device", False, "choose_device"),
            ("DOWNLOAD_FAILED", "download", True, "retry"),
            ("TRANSCRIPTION_EMPTY", "transcription", True, "retry"),
            ("OUTPUT_WRITE_FAILED", "output", False, "change_output"),
            ("CANCELLED", "task", False, "resubmit"),
            ("UNKNOWN", "unknown", False, "support"),
        ]
        for code, scope, retryable, action in cases:
            with self.subTest(code=code):
                info = recovery(CodedError(code))
                self.assertEqual(info["code"], code)
                self.assertEqual(info["category"], code)
                self.assertEqual(info["scope"], scope)
                self.assertEqual(info["retryable"], retryable)
                self.assertIn(action, [item["id"] for item in info["actions"]])
                self.assertTrue(info["reason"].strip())
                serialized = json.dumps(info, ensure_ascii=False)
                self.assertNotIn("/secret/path", serialized)
                self.assertNotIn("private diagnostic", serialized)

    def test_download_subcategories_only_allow_transient_retry(self):
        cases = [
            (CATEGORY_TRANSIENT, True, "retry"),
            (CATEGORY_BOT_WALL, False, "slow_down"),
            (CATEGORY_GONE, False, "change_input"),
            (CATEGORY_FATAL, False, "change_input"),
        ]
        for category, retryable, action in cases:
            with self.subTest(category=category):
                exc = DownloadError(category, "raw download detail")
                self.assertEqual(exc.code, "DOWNLOAD_FAILED")
                info = recovery(exc)
                self.assertEqual(info["category"], "DOWNLOAD_FAILED")
                self.assertEqual(info["retryable"], retryable)
                self.assertIn(action, [item["id"] for item in info["actions"]])

    def test_existing_result_errors_map_to_stable_categories(self):
        cases = [
            ("NO_SPEECH", "NO_SPEECH", False, "change_input"),
            ("MODEL_LOAD_FAILED", "MODEL_LOAD_FAILED", False, "prepare_model"),
            ("RESULT_WRITE_FAILED", "OUTPUT_WRITE_FAILED", False, "change_output"),
            ("RESULT_INVALID", "RESULT_INVALID", False, "support"),
        ]
        for code, category, retryable, action in cases:
            with self.subTest(code=code):
                info = recovery(CodedError(code))
                self.assertEqual(info["code"], code)
                self.assertEqual(info["category"], category)
                self.assertEqual(info["retryable"], retryable)
                self.assertIn(action, [item["id"] for item in info["actions"]])

    def test_unknown_exception_has_no_retry_and_support_action(self):
        info = recovery(RuntimeError("secret implementation detail"))
        self.assertEqual(info["code"], "UNKNOWN")
        self.assertEqual(info["category"], "UNKNOWN")
        self.assertFalse(info["retryable"])
        self.assertIn("support", [item["id"] for item in info["actions"]])
        self.assertNotIn("secret implementation detail", json.dumps(info))

    def test_reason_and_actions_follow_language_without_changing_codes(self):
        previous = get_language()
        try:
            set_language("en")
            english = recovery(ModelDownloadError("network reset"))
            set_language("zh")
            chinese = recovery(ModelDownloadError("network reset"))
        finally:
            set_language(previous)
        self.assertEqual(english["lang"], "en")
        self.assertEqual(chinese["lang"], "zh")
        self.assertEqual(english["code"], chinese["code"])
        self.assertEqual(english["category"], chinese["category"])
        self.assertEqual(english["retryable"], chinese["retryable"])
        self.assertEqual(
            [item["id"] for item in english["actions"]],
            [item["id"] for item in chinese["actions"]],
        )
        self.assertNotEqual(english["reason"], chinese["reason"])
        self.assertRegex(chinese["reason"], r"[\u4e00-\u9fff]")

    def test_format_recovery_includes_code_reason_scope_and_actions(self):
        from mediascribe.recovery import format_recovery

        previous = get_language()
        try:
            set_language("en")
            text = format_recovery(ModelDownloadError("raw secret"))
        finally:
            set_language(previous)
        self.assertIn("MODEL_DOWNLOAD_FAILED", text)
        self.assertIn("model", text)
        self.assertIn("Retry", text)
        self.assertNotIn("raw secret", text)


class TestMachineReadableRecovery(unittest.TestCase):
    def test_mcp_tool_error_exposes_structured_recovery(self):
        from mediascribe.mcp_server.protocol import _handle_request
        from mediascribe.mcp_server.tools import TOOL_HANDLERS

        def fail(_args):
            raise ModelDownloadError("private token /secret/path")

        with mock.patch.dict(TOOL_HANDLERS, {"transcribe_video": fail}):
            response = _handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": "transcribe_video", "arguments": {"source": "clip.wav"}},
                }
            )
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error_code"], "MODEL_DOWNLOAD_FAILED")
        self.assertTrue(result["structuredContent"]["recovery"]["retryable"])
        self.assertNotIn("/secret/path", json.dumps(result))

    def test_batch_startup_error_json_contains_recovery(self):
        from mediascribe import batch_cli
        from mediascribe.model_weights import WeightStatus

        missing = WeightStatus(
            engine="whisper",
            model="small",
            ok=False,
            location="fake-cache",
            hint="provision model",
        )
        with (
            mock.patch("mediascribe.config.find_spec", return_value=None),
            mock.patch("mediascribe.model_weights.check_weights", return_value=missing),
            mock.patch("mediascribe.batch_cli._resolve_user_url") as resolve,
            mock.patch("sys.stdout") as stdout,
        ):
            resolve.return_value = None
            self.assertEqual(
                batch_cli.main(["--user", "u", "--engine", "whisper", "--json"]),
                1,
            )
        resolve.assert_not_called()
        payload = json.loads("".join(call.args[0] for call in stdout.write.call_args_list))
        self.assertEqual(payload["errors"][0]["kind"], "MODEL_NOT_READY")
        self.assertEqual(payload["errors"][0]["recovery"]["category"], "MODEL_NOT_READY")

    def test_batch_item_error_carries_download_recovery(self):
        from types import SimpleNamespace

        from mediascribe.batch_cli import process_single_video_safe

        def fail_download(*_args, **_kwargs):
            raise DownloadError(CATEGORY_TRANSIENT, "raw network detail")

        result = process_single_video_safe(
            video={"video_id": "123", "url": "https://www.douyin.com/video/123"},
            index=1,
            total=1,
            download_dir=Path("output"),
            config=SimpleNamespace(max_retries=1, headless=True, max_wait_for_media=1),
            get_media_url_fn=lambda *a, **k: "https://v26.douyinvod.com/media-audio/x.mp4",
            download_media_fn=fail_download,
            transcribe_fn=lambda _path: None,
            log=mock.Mock(),
        )
        self.assertEqual(result["error_code"], "DOWNLOAD_FAILED")
        self.assertTrue(result["recovery"]["retryable"])
        self.assertIn("retry", [a["id"] for a in result["recovery"]["actions"]])

    def test_agent_output_can_carry_additive_recovery(self):
        from mediascribe.agent_output import AgentOutput

        info = recovery(ModelDownloadError("network reset"))
        output = AgentOutput(command="archive")
        output.add_error("model", "download failed", recovery=info)
        error = output.to_dict()["errors"][0]
        self.assertEqual(error["kind"], "model")
        self.assertEqual(error["recovery"]["category"], "MODEL_DOWNLOAD_FAILED")
        self.assertEqual(output.to_dict()["schema"], "mediascribe.agent-output/v1")


if __name__ == "__main__":
    unittest.main()
