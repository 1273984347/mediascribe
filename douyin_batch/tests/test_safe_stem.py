"""
Tests for ``mediascribe.inputs.safe_stem`` contract.

Verifies that ``safe_stem`` delegates to
``douyin_batch.platform_compat.safe_filename`` and inherits the same
sanitization rules (Windows reserved names, control characters, OS-invalid
characters).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


class TestSafeStem(unittest.TestCase):
    """``mediascribe.inputs.safe_stem`` must reuse safe_filename rules."""

    def setUp(self):
        from mediascribe.inputs import safe_stem

        self.safe_stem = safe_stem

    def test_returns_string(self):
        self.assertIsInstance(self.safe_stem("hello"), str)

    def test_basic_unchanged(self):
        # Plain ASCII names must be returned as-is (no translation needed).
        self.assertEqual(self.safe_stem("hello"), "hello")
        self.assertEqual(self.safe_stem("video_2024"), "video_2024")
        self.assertEqual(self.safe_stem("clip-001"), "clip-001")

    def test_strips_windows_invalid_chars(self):
        # Each of these chars is illegal on Windows filesystems and must be
        # replaced — not silently passed through.
        for ch in '<>:"/\\|?*':
            result = self.safe_stem(f"bad{ch}name")
            self.assertNotIn(ch, result, f"char {ch!r} leaked into {result!r}")

    def test_strips_control_characters(self):
        # Control characters (ord < 32) must be removed.
        result = self.safe_stem("foo\x00bar\x07baz")
        self.assertNotIn("\x00", result)
        self.assertNotIn("\x07", result)

    def test_handles_windows_reserved_names(self):
        # CON, PRN, AUX, NUL, COM1, LPT1 etc. are illegal on Windows.
        # safe_filename prefixes an underscore; safe_stem must inherit that.
        self.assertNotEqual(self.safe_stem("CON"), "CON")
        self.assertNotEqual(self.safe_stem("PRN"), "PRN")
        self.assertNotEqual(self.safe_stem("COM1"), "COM1")

    def test_length_is_truncated(self):
        # Long names should be truncated to avoid filesystem limits.
        long_name = "a" * 500
        self.assertLessEqual(len(self.safe_stem(long_name)), 200)

    def test_unicode_preserved(self):
        # Non-ASCII unicode must round-trip (Chinese, emoji-adjacent chars).
        self.assertEqual(self.safe_stem("中文视频"), "中文视频")
        self.assertEqual(self.safe_stem("テスト.mp4").split(".")[0], "テスト")

    def test_delegates_to_safe_filename(self):
        # The whole point of the refactor: identical output for the same input.
        from douyin_batch.platform_compat import safe_filename

        cases = [
            "normal name",
            "12:30 highlight",
            "C:/Users/test.mp4",
            "report?draft=1",
            "x" * 250,
        ]
        for case in cases:
            self.assertEqual(
                self.safe_stem(case),
                safe_filename(case),
                msg=f"mismatch for {case!r}",
            )

    def test_empty_string_returns_empty(self):
        # safe_filename.strip() will return "" for "" — document that.
        self.assertEqual(self.safe_stem(""), "")


if __name__ == "__main__":
    unittest.main()
