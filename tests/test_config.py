"""mediascribe.config 的测试 —— 迁入自 tests/test_coverage_gaps_2.py(按被测模块归位)。"""

from __future__ import annotations

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
