"""douyin_batch.platform_compat 的测试 —— 迁入自 tests/test_coverage_gaps.py 与 tests/test_coverage_gaps_2.py(按被测模块归位)。"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

###########################################################################
# 迁入自 tests/test_coverage_gaps.py —— TestPlatformCompatExtras(douyin_batch.platform_compat)
###########################################################################


class TestPlatformCompatExtras:
    """Cover the pure helpers (path / filename).  ffmpeg detection is
    exercised in test_cross_platform; here we just check the small
    pure functions."""

    def test_safe_filename_basic(self):
        from douyin_batch.platform_compat import safe_filename

        # Strips OS-invalid chars
        assert "/" not in safe_filename("a/b")
        assert "\\" not in safe_filename("a\\b")
        assert ":" not in safe_filename("a:b")
        assert "?" not in safe_filename("a?b")
        assert "*" not in safe_filename("a*b")
        # Windows reserved names are renamed with a leading underscore
        out = safe_filename("CON")
        assert out.startswith("_") and out.upper().endswith("CON")
        # Control chars gone
        assert "\x00" not in safe_filename("a\x00b")
        # Length cap
        long = "a" * 500
        assert len(safe_filename(long)) <= 200

    def test_normalise_path_separators(self):
        from douyin_batch.platform_compat import normalize_path

        # Returns an absolute ``pathlib.Path`` (resolves ``~`` and
        # relative segments).  On Windows the native separator stays
        # backslash; on POSIX it stays slash — we don't force a
        # cross-platform rewrite.  The contract is "round-trips and is
        # absolute".
        out = normalize_path("a/b/c")
        assert isinstance(out, Path)
        assert out.is_absolute()
        # ``expanduser`` resolves the ``~`` if a HOME is set; just check
        # the function does not raise and returns a Path.
        out2 = normalize_path("~/some_dir")
        assert isinstance(out2, Path)

    def test_check_ffmpeg_returns_bool(self):
        from douyin_batch.platform_compat import check_ffmpeg

        result = check_ffmpeg()
        assert isinstance(result, bool)

    def test_get_os_and_helpers(self):
        from douyin_batch import platform_compat as pc

        os = pc.get_os()
        assert os in {"windows", "macos", "linux", "unknown"}
        # The ``is_*`` helpers mirror get_os().
        assert pc.is_windows() is (os == "windows")
        assert pc.is_macos() is (os == "macos")
        assert pc.is_linux() is (os == "linux")

    def test_check_python_version(self):
        from douyin_batch.platform_compat import check_python_version

        # Always satisfied on the test host (Python 3.12).
        assert check_python_version((3, 0)) is True
        # Not satisfied for a future version.
        assert check_python_version((99, 0)) is False

    def test_safe_filename_strips_control(self):
        from douyin_batch.platform_compat import safe_filename

        # All control chars (< 0x20) removed
        result = safe_filename("a\x01b\x1fc")
        assert "".join(ch for ch in result if ord(ch) < 32) == ""


###########################################################################
# 迁入自 tests/test_coverage_gaps_2.py —— TestPlatformCompatMore(douyin_batch.platform_compat)
###########################################################################


class TestPlatformCompatMore:
    def test_find_ffmpeg_returns_none(self):
        from douyin_batch import platform_compat as pc

        # PATH 和常见安装路径都没有时才返回 None — CI 上 /usr/bin/ffmpeg
        # 真实存在，必须把 Path.exists 一并 mock 掉。
        with (
            mock.patch.object(pc.shutil, "which", return_value=None),
            mock.patch.object(pc.Path, "exists", return_value=False),
        ):
            assert pc.find_ffmpeg() is None

    def test_find_ffmpeg_returns_path(self):
        from douyin_batch import platform_compat as pc

        with mock.patch.object(pc.shutil, "which", return_value="C:/ffmpeg.exe"):
            p = pc.find_ffmpeg()
        assert p is not None and p.name == "ffmpeg.exe"

    def test_find_ffmpeg_returns_none_when_absent(self):
        from douyin_batch import platform_compat as pc

        # No PATH, no common paths in a clean test env → None.
        with mock.patch.object(pc.shutil, "which", return_value=None):
            p = pc.find_ffmpeg()
        # We don't care if it actually returns None (CI may have ffmpeg
        # at a common path) — we just want to make sure the function
        # either returns a Path or None, never crashes.
        assert p is None or isinstance(p, Path)
