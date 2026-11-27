"""mediascribe.inputs 的测试 —— 迁入自 tests/test_coverage_gaps.py(按被测模块归位)。"""

from __future__ import annotations

from pathlib import Path

import pytest

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


class TestPreflightSource:
    def _settings(self, tmp_path):
        from mediascribe.config import Settings

        return Settings(workspace_root=tmp_path / "workspace", engine="whisper")

    def test_missing_local_path_is_rejected(self, tmp_path):
        from mediascribe.inputs import PreflightError, preflight_source

        with pytest.raises(PreflightError) as exc_info:
            preflight_source(str(tmp_path / "missing.wav"), self._settings(tmp_path))

        assert exc_info.value.code == "INPUT_INVALID"

    def test_unsupported_local_format_is_rejected(self, tmp_path):
        from mediascribe.inputs import PreflightError, preflight_source

        source = tmp_path / "notes.txt"
        source.write_text("not media", encoding="utf-8")

        with pytest.raises(PreflightError) as exc_info:
            preflight_source(str(source), self._settings(tmp_path))

        assert exc_info.value.code == "INPUT_INVALID"

    def test_local_video_requires_ffmpeg(self, tmp_path, monkeypatch):
        from mediascribe.inputs import PreflightError, preflight_source

        source = tmp_path / "video.mp4"
        source.write_bytes(b"fake")
        monkeypatch.setattr("shutil.which", lambda _name: None)

        with pytest.raises(PreflightError) as exc_info:
            preflight_source(str(source), self._settings(tmp_path))

        assert exc_info.value.code == "FFMPEG_MISSING"

    def test_local_audio_does_not_require_ffmpeg(self, tmp_path, monkeypatch):
        from mediascribe.inputs import preflight_source

        source = tmp_path / "audio.wav"
        source.write_bytes(b"fake")
        monkeypatch.setattr("shutil.which", lambda _name: None)

        parsed = preflight_source(str(source), self._settings(tmp_path))

        assert parsed.kind == "audio"
        assert parsed.path == source

    def test_unwritable_output_is_rejected(self, tmp_path):
        from mediascribe.inputs import PreflightError, preflight_source

        source = tmp_path / "audio.wav"
        source.write_bytes(b"fake")
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("block", encoding="utf-8")

        with pytest.raises(PreflightError) as exc_info:
            preflight_source(str(source), self._settings(tmp_path), output=blocker / "out.md")

        assert exc_info.value.code == "OUTPUT_WRITE_FAILED"
