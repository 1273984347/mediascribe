"""LATEST.txt 指针写入规则测试（自定义 --output 不覆盖指针）"""

import tempfile
import unittest
from pathlib import Path

from mediascribe.__main__ import _write_latest_pointer


class _Settings:
    def __init__(self, workspace_root):
        self.workspace_root = workspace_root


class _Result:
    def __init__(self, transcript_path):
        self.transcript_path = transcript_path


class TestWriteLatestPointer(unittest.TestCase):
    def test_default_output_writes_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _Result(Path(tmp) / "out" / "transcript.md")
            _write_latest_pointer(_Settings(tmp), result)
            pointer = Path(tmp) / "LATEST.txt"
            self.assertTrue(pointer.exists())
            self.assertEqual(pointer.read_text(encoding="utf-8"), str(result.transcript_path))

    def test_custom_output_does_not_touch_pointer(self):
        # 回归(2026-10-01): --output 的辅助跑(双模型对照稿)劫持了指针
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "LATEST.txt"
            existing.write_text("keep-me", encoding="utf-8")
            _write_latest_pointer(
                _Settings(tmp), _Result(Path(tmp) / "other.md"), output=Path("custom.md")
            )
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep-me")

    def test_mock_without_attributes_is_silent(self):
        _write_latest_pointer(None, None)  # 不应抛异常

    def test_custom_output_with_broken_settings_is_silent(self):
        _write_latest_pointer(None, None, output=Path("x.md"))  # 不应抛异常


if __name__ == "__main__":
    unittest.main()
