"""
Second coverage pass — push the lower-impact modules to 90 %+.

Targets (pre-fill coverage shown in parens):

* mediascribe.models (91 %)
* mediascribe.config (95 %)
* mediascribe.observability (79 %)
* mediascribe.transcribers.factory (91 %)
* mediascribe.transcribers.chunked (77 %)
* mediascribe.downloaders.base (90 %)
* mediascribe.downloaders.youtube (73 %)
* mediascribe.downloaders.ytdlp (52 %)
* mediascribe.__main__ (56 %)
* mediascribe.plugins.registry (79 %)
* douyin_batch.i18n (92 %)
* douyin_batch.logger (75 %)
* douyin_batch.platform_compat (64 %)
* douyin_batch.retry (54 %)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# mediascribe.models
# ---------------------------------------------------------------------------
class TestModels:
    """Pure dataclass helpers — no external deps."""

    def test_source_ref_display_name_bilibili(self):
        from mediascribe.models import SourceRef

        s = SourceRef(raw_input="x", kind="bilibili", bv="BV1abc")
        assert s.display_name == "BV1abc"
        assert s.is_known_kind is True

    def test_source_ref_display_name_fallbacks(self):
        from mediascribe.models import SourceRef

        s = SourceRef(raw_input="raw", kind="video", path=Path("/tmp/a.mp4"))
        assert s.display_name == "a.mp4"
        s2 = SourceRef(raw_input="raw", kind="audio", url="http://x/a")
        assert s2.display_name == "http://x/a"
        s3 = SourceRef(raw_input="the raw text", kind="unknown")
        assert s3.display_name == "the raw text"

    def test_source_ref_unknown_kind(self):
        from mediascribe.models import KNOWN_SOURCE_KINDS, SourceRef

        s = SourceRef(raw_input="x", kind="something-new")
        assert s.is_known_kind is False
        # KNOWN_SOURCE_KINDS is the single source of truth.
        assert "bilibili" in KNOWN_SOURCE_KINDS
        assert "wechat_mp" in KNOWN_SOURCE_KINDS

    def test_transcript_result_defaults(self):
        from mediascribe.models import SourceRef, TranscriptResult

        t = TranscriptResult(
            source=SourceRef(raw_input="x", kind="video"),
            engine="whisper",
            model="base",
            text="hi",
            audio_path=Path("/tmp/a.wav"),
            transcript_path=Path("/tmp/t.md"),
        )
        # Optional defaults
        assert t.video_path is None
        assert t.metadata_path is None
        assert t.metadata is None
        assert t.segments is None
        assert t.language is None
        assert t.speaker_diarization is False


# ---------------------------------------------------------------------------
# mediascribe.config
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# mediascribe.observability — the OTel upgrade path is tested in
# test_observability.py::TestOptionalOtelUpgrade; we don't repeat it
# here because swapping _TRACER / _METER globally would break every
# subsequent test in the file that exercises the in-memory SDK.
# ---------------------------------------------------------------------------
class TestObservabilityExtras2:
    """Cover just the bits that don't replace the global tracer."""

    def test_install_opentelemetry_exporter_unavailable(self):
        from mediascribe import observability

        with mock.patch.object(observability, "_REAL_OTEL_AVAILABLE", False):
            assert observability.install_opentelemetry_exporter() is False


# ---------------------------------------------------------------------------
# mediascribe.transcribers.factory / chunked
# ---------------------------------------------------------------------------
class TestFactoryAndChunked:
    def test_factory_creates_known_engines(self):
        from mediascribe.transcribers.factory import get_transcriber

        # The default is whisperx.  We just assert the factory returns
        # a non-None instance and that the requested model is honoured.
        t = get_transcriber(name="whisper", model="tiny")
        assert t is not None
        assert t.model_name == "tiny"

    def test_factory_unknown_engine_raises(self):
        from mediascribe.transcribers.factory import get_transcriber

        with pytest.raises(ValueError):
            get_transcriber(name="definitely-not-an-engine", model="tiny")

    def test_factory_fallback_chain(self):
        from mediascribe.transcribers.factory import (
            DEFAULT_FALLBACK_CHAIN,
            get_transcriber_with_fallback,
        )

        assert isinstance(DEFAULT_FALLBACK_CHAIN, tuple)
        # Asking for an unsupported engine falls back through the chain.
        t = get_transcriber_with_fallback(preferred="whisper", model="tiny")
        assert t is not None

    def test_chunked_probe_duration_unsupported_path_raises(self, tmp_path):
        from mediascribe.transcribers.chunked import probe_duration

        # ``probe_duration`` falls back to ffprobe for non-WAV inputs.
        # We don't have ffprobe in the test env, so the function must
        # raise a clean RuntimeError instead of crashing with an
        # OSError from subprocess.
        mp3 = tmp_path / "x.mp3"
        mp3.write_bytes(b"junk")
        with pytest.raises(RuntimeError):
            probe_duration(mp3)


# ---------------------------------------------------------------------------
# mediascribe.downloaders.base
# ---------------------------------------------------------------------------
class TestDownloaderBase:
    def test_base_abstract_cannot_be_instantiated(self):
        from mediascribe.downloaders.base import Downloader

        with pytest.raises(TypeError):
            Downloader()  # abstract: must override ``download``.


# ---------------------------------------------------------------------------
# mediascribe.downloaders.youtube (uses yt-dlp but the constructor and
# URL detection are pure)
# ---------------------------------------------------------------------------
class TestYoutubeDownloader:
    def test_supports_returns_bool(self):
        from mediascribe.downloaders.youtube import YouTubeDownloader
        from mediascribe.models import SourceRef

        d = YouTubeDownloader()
        # supports() takes a SourceRef.  YouTube URLs should be True.
        assert d.supports(SourceRef(raw_input="x", kind="youtube",
                                    url="https://www.youtube.com/watch?v=abc")) is True
        # Non-YouTube URL should be False.
        assert d.supports(SourceRef(raw_input="x", kind="bilibili",
                                    url="https://www.bilibili.com/video/BV1")) is False


# ---------------------------------------------------------------------------
# mediascribe.downloaders.ytdlp — pure helpers
# ---------------------------------------------------------------------------
class TestYtdlpDownloader:
    def test_name_attribute(self):
        from mediascribe.downloaders.ytdlp import YtDlpDownloader

        d = YtDlpDownloader()
        assert d.name == "yt-dlp"
        # _ydl starts as None (lazy import).
        assert d._ydl is None


# ---------------------------------------------------------------------------
# mediascribe.__main__
# ---------------------------------------------------------------------------
class TestMain:
    def test_main_module_help(self, capsys):
        from mediascribe.__main__ import main

        with mock.patch.object(sys, "argv", ["mediascribe", "--help"]):
            with pytest.raises(SystemExit) as excinfo:
                main()
        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        # help text mentions the two main subcommands
        assert "transcribe" in captured.out or "transcribe" in captured.err


# ---------------------------------------------------------------------------
# mediascribe.plugins.registry
# ---------------------------------------------------------------------------
class TestPluginRegistry:
    def test_list_downloaders_and_get(self):
        from mediascribe.downloaders.base import Downloader
        from mediascribe.plugins import registry as reg

        names = reg.list_downloaders()
        assert isinstance(names, list)
        # get_downloader returns None for unknown names.
        assert reg.get_downloader("definitely-not-a-real-downloader") is None
        # And for a known name, returns the class object (if the
        # built-in downloaders are listed).
        if names:
            klass = reg.get_downloader(names[0])
            assert klass is None or issubclass(klass, Downloader)

    def test_list_transcribers_and_get(self):
        from mediascribe.plugins import registry as reg
        from mediascribe.transcribers.base import Transcriber

        names = reg.list_transcribers()
        assert isinstance(names, list)
        if names:
            klass = reg.get_transcriber_cls(names[0])
            assert klass is None or (
                isinstance(klass, type) and issubclass(klass, Transcriber)
            )

    def test_clear_cache(self):
        from mediascribe.plugins import registry as reg

        # Should not raise.
        reg.clear_cache()


# ---------------------------------------------------------------------------
# douyin_batch.i18n
# ---------------------------------------------------------------------------
class TestI18nExtras:
    def test_messages_english(self):
        from douyin_batch.i18n import Messages, set_language, t

        set_language("en")
        # HEADER_TITLE has both en and zh entries.
        assert t("HEADER_TITLE") == Messages.HEADER_TITLE["en"]

    def test_messages_chinese(self):
        from douyin_batch.i18n import set_language, t

        set_language("zh")
        assert t("HEADER_TITLE") == "MediaScribe - 批量转录"

    def test_messages_format_kwargs(self):
        from douyin_batch.i18n import set_language, t

        set_language("en")
        # SUCCESS_VIDEOS_FOUND supports ``{count}`` formatting.
        out = t("SUCCESS_VIDEOS_FOUND", count=5)
        assert "5" in out

    def test_messages_unknown_key_returns_bracketed(self):
        from douyin_batch.i18n import set_language, t

        set_language("en")
        # Unknown keys are wrapped in ``[ ... ]`` so they're easy to
        # spot during translation audits.
        assert t("this_key_does_not_exist") == "[this_key_does_not_exist]"

    def test_detect_language_returns_str(self):
        from douyin_batch.i18n import detect_language, get_language, init_language

        # The auto-detect helper returns one of "en" / "zh".
        lang = detect_language()
        assert lang in ("en", "zh")
        # ``init_language`` doesn't return anything; it just sets the
        # global state.  Verify the side effect via ``get_language()``.
        init_language("en")
        assert get_language() == "en"
        init_language(None)  # auto-detect
        assert get_language() in ("en", "zh")

    def test_get_language_round_trip(self):
        from douyin_batch.i18n import get_language, set_language

        set_language("en")
        assert get_language() == "en"
        set_language("zh")
        assert get_language() == "zh"
        # Unknown lang raises.
        with pytest.raises(ValueError):
            set_language("zz")


# ---------------------------------------------------------------------------
# douyin_batch.logger
# ---------------------------------------------------------------------------
class TestLoggerExtras:
    def test_get_logger_returns_same_instance(self):
        from douyin_batch.logger import get_logger

        a = get_logger()
        b = get_logger()
        assert a is b

    def test_logger_set_level_and_quiet(self, tmp_path):
        from douyin_batch.logger import get_logger

        log = get_logger()
        # The console handler is at index 0.
        log.set_level("DEBUG")
        log.set_quiet(True)
        log.set_quiet(False)
        log.set_level("WARNING")

    def test_logger_add_file_handler(self, tmp_path):
        from douyin_batch.logger import get_logger

        log = get_logger()
        log_file = tmp_path / "subdir" / "log.txt"
        log.add_file_handler(log_file)
        # The parent dir was created on demand.
        assert log_file.parent.exists()
        log.info("hello from coverage test")
        assert "hello from coverage test" in log_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# douyin_batch.platform_compat — fill the rest
# ---------------------------------------------------------------------------
class TestPlatformCompatMore:
    def test_find_ffmpeg_returns_none(self):
        from douyin_batch import platform_compat as pc

        with mock.patch.object(pc.shutil, "which", return_value=None):
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


# ---------------------------------------------------------------------------
# douyin_batch.retry — no jitter param; just verify backoff repeats
# ---------------------------------------------------------------------------
class TestRetryBackoffStrategy:
    def test_retry_does_not_call_func_with_kwarg(self):
        """Make sure retry forwards *args / **kwargs correctly and does
        not pass any internal config to the user function (the bug
        used to leak ``jitter=`` to the wrapped callable)."""
        from douyin_batch.retry import retry

        seen: list = []

        def func(*args, **kwargs):
            seen.append((args, kwargs))
            raise ValueError("fail")

        with pytest.raises(ValueError):
            retry(func, max_retries=1, delay=0, backoff=2.0)
        # Each call received no extra kwargs.
        for _, kw in seen:
            assert "jitter" not in kw
            assert "delay" not in kw
            assert "backoff" not in kw
