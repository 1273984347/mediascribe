"""mediascribe.config 的测试 —— 迁入自 tests/test_coverage_gaps_2.py(按被测模块归位)。"""

from __future__ import annotations

import pytest

###########################################################################
# 迁入自 tests/test_coverage_gaps_2.py —— TestConfig(mediascribe.config)
###########################################################################


class TestConfig:
    """Hit the few remaining defaults that aren't covered."""

    def test_settings_ensure_directories(self, tmp_path):
        from mediascribe.config import Settings

        s = Settings(workspace_root=tmp_path / "ws")
        s.ensure_directories()
        # ``workspace_root`` + 4 sub-dirs should all be created.
        for d in (
            s.workspace_root,
            s.downloads_dir,
            s.audio_dir,
            s.transcripts_dir,
            s.metadata_dir,
        ):
            assert d.exists() and d.is_dir()

    def test_settings_load_cookie_json(self, tmp_path):
        from mediascribe.config import _parse_cookie_string

        d = _parse_cookie_string('{"sid": "abc", "token": "xyz"}')
        assert d["sid"] == "abc" and d["token"] == "xyz"

    def test_settings_load_cookie_netscape(self, tmp_path):
        from mediascribe.config import _parse_cookie_string

        # Netscape format: 7 space-separated fields, last two are
        # name and value.
        netscape = "# Netscape HTTP Cookie File\n\nexample.com\tTRUE\t/\tFALSE\t0\tsid\tabc\n"
        d = _parse_cookie_string(netscape)
        assert d.get("sid") == "abc"

    def test_settings_load_cookie_file(self, tmp_path):
        from mediascribe.config import _load_cookie_file

        f = tmp_path / "cookies.txt"
        f.write_text('{"k1": "v1", "k2": "v2"}', encoding="utf-8")
        d = _load_cookie_file(f)
        assert d["k1"] == "v1" and d["k2"] == "v2"
        # Missing file → empty dict
        assert _load_cookie_file(tmp_path / "missing.txt") == {}


class TestDefaultEngine:
    """P1-2(2026-11-20 架构评审): 默认引擎安装探测(faster-whisper 优先)。"""

    def test_settings_default_engine_follows_probe(self, tmp_path):
        from mediascribe.config import Settings, default_engine

        s = Settings(workspace_root=tmp_path)
        assert s.engine == default_engine()

    def test_settings_explicit_engine_wins(self, tmp_path):
        from mediascribe.config import Settings

        s = Settings(workspace_root=tmp_path, engine="whisper")
        assert s.engine == "whisper"

    def test_default_engine_matches_install_state(self):
        from importlib.util import find_spec

        from mediascribe.config import default_engine

        expected = "faster-whisper" if find_spec("faster_whisper") else "whisper"
        assert default_engine() == expected

    def test_default_engine_raises_when_no_asr_engine_is_installed(self, monkeypatch):
        from mediascribe.config import default_engine

        monkeypatch.setattr("mediascribe.config.find_spec", lambda _name: None)

        with pytest.raises(RuntimeError) as exc_info:
            default_engine()

        assert getattr(exc_info.value, "code", None) == "ENGINE_UNAVAILABLE"

    def test_settings_records_whisper_fallback_notice(self, tmp_path, monkeypatch):
        from mediascribe.config import Settings

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "whisper" else None,
        )

        settings = Settings(workspace_root=tmp_path)

        assert settings.engine == "whisper"
        assert "faster-whisper" in settings.engine_notice

    def test_explicit_engine_skips_auto_probe_and_notice(self, tmp_path, monkeypatch):
        from mediascribe.config import Settings

        monkeypatch.setattr("mediascribe.config.find_spec", lambda _name: None)

        settings = Settings(workspace_root=tmp_path, engine="whisperx")

        assert settings.engine == "whisperx"
        assert settings.engine_notice == ""

    def test_default_model_is_small_but_not_explicit(self, tmp_path):
        from mediascribe.config import Settings

        settings = Settings(workspace_root=tmp_path, engine="whisper", model=None)

        assert settings.model == "small"
        assert settings.requested_model == "auto"
        assert settings.model_explicit is False

    def test_explicit_selection_sources_are_recorded(self, tmp_path):
        from mediascribe.config import Settings

        settings = Settings(
            workspace_root=tmp_path,
            engine="whisper",
            model="large-v3",
            device="cuda",
        )

        assert settings.requested_engine == "whisper"
        assert settings.requested_model == "large-v3"
        assert settings.requested_device == "cuda"
        assert settings.engine_explicit is True
        assert settings.model_explicit is True
        assert settings.device_explicit is True

    def test_auto_model_is_not_explicit(self, tmp_path):
        from mediascribe.config import Settings

        settings = Settings(workspace_root=tmp_path, engine="whisper", model="auto")

        assert settings.model == "auto"
        assert settings.requested_model == "auto"
        assert settings.model_explicit is False

    def test_model_download_is_disabled_by_default(self, tmp_path):
        from mediascribe.config import Settings

        settings = Settings(workspace_root=tmp_path, engine="whisper")

        assert settings.allow_model_download is False

    def test_model_download_can_be_explicitly_authorized(self, tmp_path):
        from mediascribe.config import Settings

        settings = Settings(
            workspace_root=tmp_path,
            engine="whisper",
            allow_model_download=True,
        )

        assert settings.allow_model_download is True
