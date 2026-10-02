"""mediascribe.inputs 的测试 —— 迁入自 tests/test_coverage_gaps.py(按被测模块归位)。"""

from __future__ import annotations

from pathlib import Path

###########################################################################
# 迁入自 tests/test_coverage_gaps.py —— TestInputs(mediascribe.inputs)
###########################################################################


class TestInputs:
    """Test the small file-extension and stem sanitisation helpers."""

    def test_is_audio_file_recognised_extensions(self):
        from mediascribe.inputs import is_audio_file

        for ext in (".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"):
            assert is_audio_file(Path(f"x{ext}")) is True
        # Case-insensitive
        assert is_audio_file(Path("X.MP3")) is True
        # Unknown
        assert is_audio_file(Path("foo.txt")) is False
        assert is_audio_file(Path("noext")) is False

    def test_is_video_file_recognised_extensions(self):
        from mediascribe.inputs import is_video_file

        for ext in (".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm", ".m4v"):
            assert is_video_file(Path(f"x{ext}")) is True
        assert is_video_file(Path("X.MP4")) is True
        assert is_video_file(Path("foo.txt")) is False
        assert is_video_file(Path("noext")) is False

    def test_safe_stem_windows_reserved(self):
        from mediascribe.inputs import safe_stem

        # The shared platform_compat helper turns Windows reserved names
        # into ``_NAME`` so they can be created cross-platform.
        assert "_CON" in safe_stem("CON").upper() or safe_stem("CON").upper() == "CON_"
        # Control chars stripped
        cleaned = safe_stem("bad\x00name\x1f")
        assert "\x00" not in cleaned and "\x1f" not in cleaned
