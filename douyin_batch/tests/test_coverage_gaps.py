"""
Coverage gap fill — exercise the under-tested public APIs of
several modules so the per-file coverage moves toward 90 %+.

Targets (pre-fill coverage shown in parens):

* mediascribe.url_utils (45 %)
* mediascribe.inputs (73 %)
* mediascribe.audio_utils (30 %)
* mediascribe.transcribers.chunked (73 %)
* mediascribe.performance (94 %)
* mediascribe.observability (77 %)
* mediascribe.web.app (84 %)
* douyin_batch.cache (84 %)
* douyin_batch.retry (51 %)
* douyin_batch.security (67 %)
* douyin_batch.platform_compat (64 %)

The tests stay pure (no network, no ffmpeg, no Playwright); if a
helper requires an external dep, we cover the happy path of the
function the test can drive and leave the platform-specific
branches to the existing e2e suite.
"""

from __future__ import annotations

import json
import time
import wave
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# 1. mediascribe.url_utils
# ---------------------------------------------------------------------------
class TestUrlUtils:
    """Pure-function URL helpers — no network involved."""

    def test_extract_bvid_from_full_url(self):
        from mediascribe.url_utils import extract_bvid

        assert extract_bvid("https://www.bilibili.com/video/BV1Nd596vEyU") == "BV1Nd596vEyU"
        assert (
            extract_bvid("https://www.bilibili.com/video/BV1Nd596vEyU?p=1&t=42") == "BV1Nd596vEyU"
        )
        assert extract_bvid("just text BV1ABCDEFGHI more text") == "BV1ABCDEFGHI"
        assert extract_bvid("no bvid here") is None
        assert extract_bvid("") is None

    def test_is_short_url_known_domains(self):
        from mediascribe.url_utils import is_short_url

        assert is_short_url("https://b23.tv/xxxxx") is True
        assert is_short_url("https://v.douyin.com/abc/") is True
        assert is_short_url("https://t.cn/R123") is True
        assert is_short_url("https://youtu.be/dQw4w9WgXcQ") is True
        # P2-8: 按主机名精确匹配——路径里出现 b23.tv 不再误判为短链
        assert is_short_url("https://www.bilibili.com/b23.tv") is False
        # P2-8: userinfo 绕过（实际主机是 127.0.0.1）不算短链
        assert is_short_url("http://b23.tv@127.0.0.1/") is False
        # Non-short
        assert is_short_url("https://www.bilibili.com/video/BV1xxx") is False
        assert is_short_url("https://example.com") is False

    def test_resolve_short_url_non_short_passthrough(self):
        from mediascribe.url_utils import resolve_short_url

        # Non-short URL should be returned as-is (with https:// prefix
        # prepended if needed).
        out = resolve_short_url("https://www.bilibili.com/video/BV1xx")
        assert out == "https://www.bilibili.com/video/BV1xx"
        # Plain domain gets the https scheme added.
        assert resolve_short_url("www.example.com/path") == "https://www.example.com/path"

    def test_resolve_short_url_failure_returns_none(self):
        from mediascribe import url_utils

        with mock.patch.object(url_utils.urllib.request, "urlopen", side_effect=Exception("boom")):
            assert url_utils.resolve_short_url("https://b23.tv/xxxxx") is None

    def test_resolve_short_url_http_error_403_retries_with_get(self):
        import urllib.error

        from mediascribe import url_utils

        call_count = {"n": 0}

        def fake_urlopen(req, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
            # Second call (GET fallback) succeeds
            return mock.MagicMock(
                url="https://www.bilibili.com/video/BV1xxx",
                __enter__=lambda s: s,
                __exit__=lambda s, *a: False,
            )

        with mock.patch.object(url_utils.urllib.request, "urlopen", side_effect=fake_urlopen):
            out = url_utils.resolve_short_url("https://b23.tv/xxxxx")
        assert out == "https://www.bilibili.com/video/BV1xxx"
        assert call_count["n"] == 2

    def test_normalize_bilibili_url_no_bvid(self):
        from mediascribe.url_utils import normalize_bilibili_url

        # When there's no BV id, the resolver returns the URL unchanged.
        url, bvid = normalize_bilibili_url("https://example.com/no-bv")
        assert bvid is None
        # ``resolve_short_url`` is mocked away to keep the test offline.
        assert url == "https://example.com/no-bv"

    def test_normalize_url_bilibili_path(self):
        from mediascribe import url_utils

        with mock.patch.object(url_utils, "resolve_short_url", side_effect=lambda u: u):
            assert (
                url_utils.normalize_url("https://www.bilibili.com/video/BV1ABCDEFGHI")
                == "https://www.bilibili.com/video/BV1ABCDEFGHI"
            )
            # Non-bilibili URLs are passed through.
            assert url_utils.normalize_url("https://example.com/foo") == "https://example.com/foo"


# ---------------------------------------------------------------------------
# 2. mediascribe.inputs
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 3. mediascribe.transcribers.chunked
# ---------------------------------------------------------------------------
class TestChunkedHelpers:
    """Cover the pure helpers (no ffmpeg required)."""

    def test_merge_texts_concatenates(self):
        from mediascribe.transcribers.chunked import Chunk, ChunkResult, merge_texts

        c0 = Chunk(index=0, start=0.0, end=10.0, path=Path("/tmp/c0.wav"))
        c1 = Chunk(index=1, start=10.0, end=20.0, path=Path("/tmp/c1.wav"))
        results = [
            ChunkResult(chunk=c0, text="hello world", segments=[]),
            ChunkResult(chunk=c1, text="  spaced  ", segments=[]),
            ChunkResult(chunk=c1, text="", segments=[]),  # empty skipped
            ChunkResult(chunk=c1, text="final", segments=[]),
        ]
        out = merge_texts(results)
        assert out == "hello world\n\nspaced\n\nfinal"

    def test_merge_segments_sorts_by_start(self):
        from mediascribe.transcribers.chunked import Chunk, ChunkResult, merge_segments

        c0 = Chunk(index=0, start=0.0, end=10.0, path=Path("/tmp/c0.wav"))
        results = [
            ChunkResult(
                chunk=c0,
                text="x",
                segments=[
                    {"start": 5.0, "end": 6.0, "text": "b"},
                    {"start": 1.0, "end": 2.0, "text": "a"},
                ],
            ),
            ChunkResult(
                chunk=c0,
                text="y",
                segments=[
                    {"start": 3.0, "end": 4.0, "text": "c"},
                ],
            ),
        ]
        merged = merge_segments(results)
        # Sorted by start; mutating a segment in the merged list must
        # not affect the original (clone semantics).
        assert [s["start"] for s in merged] == [1.0, 3.0, 5.0]
        merged[0]["text"] = "MUT"
        assert results[0].segments[1]["text"] == "a"

    def test_probe_duration_wav(self, tmp_path):
        from mediascribe.transcribers.chunked import probe_duration

        wav = tmp_path / "tone.wav"
        # 0.1 s of silence at 16 kHz, mono, 16-bit
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 1600)
        dur = probe_duration(wav)
        assert 0.05 < dur < 0.2

    def test_probe_duration_unsupported_format_raises(self, tmp_path):
        from mediascribe.transcribers.chunked import probe_duration

        fake = tmp_path / "x.mp3"
        fake.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00not-a-real-mp3")
        # No ffprobe available in the test env, file isn't a real WAV;
        # we expect a clean RuntimeError (no real ffmpeg path, not a
        # generic stack trace).
        with pytest.raises(RuntimeError):
            probe_duration(fake)


# ---------------------------------------------------------------------------
# 4. mediascribe.performance
# ---------------------------------------------------------------------------
class TestPerformanceExtras:
    """Hit to_markdown, write, parallel_map edge cases."""

    def test_to_markdown_empty(self):
        from mediascribe.performance import PerformanceReport

        rep = PerformanceReport()
        assert "_No steps recorded._" in rep.to_markdown()

    def test_to_markdown_with_steps(self):
        from mediascribe.performance import (
            STEP_TIMES,
            PerformanceReport,
            clear_step_times,
        )

        clear_step_times()
        STEP_TIMES["download"] = [0.1, 0.2, 0.05]
        STEP_TIMES["transcribe"] = [1.5]
        try:
            rep = PerformanceReport.from_registry()
            md = rep.to_markdown()
            assert "| Step | Count |" in md
            assert "download" in md
            assert "transcribe" in md
            assert "Total:" in md
            # Round-trip via dict
            d = rep.to_dict()
            assert d["steps"]["download"]["count"] == 3
        finally:
            clear_step_times()

    def test_performance_report_write(self, tmp_path):
        from mediascribe.performance import (
            STEP_TIMES,
            PerformanceReport,
            clear_step_times,
        )

        clear_step_times()
        STEP_TIMES["x"] = [0.01]
        try:
            rep = PerformanceReport.from_registry()
            out = tmp_path / "perf.json"
            rep.write(out)
            data = json.loads(out.read_text(encoding="utf-8"))
            assert data["steps"]["x"]["count"] == 1
        finally:
            clear_step_times()

    def test_parallel_map_empty(self):
        from mediascribe.performance import parallel_map

        assert parallel_map(lambda x: x * 2, []) == []

    def test_parallel_map_order_preserved(self):
        from mediascribe.performance import parallel_map

        out = parallel_map(lambda x: x * 2, [1, 2, 3, 4], max_workers=2)
        assert out == [2, 4, 6, 8]

    def test_parallel_map_exception_captured(self):
        from mediascribe.performance import parallel_map

        def boom(x):
            if x == 2:
                raise ValueError("nope")
            return x

        out = parallel_map(boom, [1, 2, 3], max_workers=2)
        assert out[0] == 1
        assert isinstance(out[1], ValueError)
        assert out[2] == 3

    def test_download_cache_round_trip(self, tmp_path):
        from mediascribe.performance import DownloadCache

        src = tmp_path / "src.txt"
        src.write_text("hi", encoding="utf-8")
        # Pre-create the cache dir so ``shutil.copy2`` has a destination.
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache = DownloadCache(base=cache_dir)
        assert cache.get("http://x/a") is None  # miss
        dst = cache.put("http://x/a", src)
        assert dst.exists()
        assert cache.get("http://x/a") == dst  # hit
        stats = cache.stats()
        assert stats["hits"] >= 1 and stats["misses"] >= 1
        cache.clear()


# ---------------------------------------------------------------------------
# 5. mediascribe.observability
# ---------------------------------------------------------------------------
class TestObservabilityExtras:
    """Span / Meter edge cases not yet covered."""

    def test_set_status_with_description(self):
        from mediascribe.observability import (
            OBSERVABILITY,
            clear_observability,
            get_tracer,
        )

        clear_observability()
        tracer = get_tracer()
        with tracer.start_as_current_span("s") as span:
            span.set_status("OK", "all good")
            span.add_event("checkpoint", {"step": 1})
        # The status event should be recorded in the span events list.
        recorded = OBSERVABILITY["spans"][0]
        evs = recorded["events"]
        assert any(e.get("status") == "OK" for e in evs)
        assert any(e.get("name") == "checkpoint" for e in evs)

    def test_record_exception_marks_status_error(self):
        from mediascribe.observability import (
            OBSERVABILITY,
            clear_observability,
            get_tracer,
        )

        clear_observability()
        with get_tracer().start_as_current_span("fail") as span:
            try:
                raise ValueError("boom")
            except ValueError as e:
                span.record_exception(e)
        assert OBSERVABILITY["spans"][0]["status"] == "ERROR"
        assert any(e.get("name") == "ValueError" for e in OBSERVABILITY["spans"][0]["events"])

    def test_nested_spans_share_trace_id(self):
        from mediascribe.observability import (
            OBSERVABILITY,
            clear_observability,
            get_tracer,
        )

        clear_observability()
        with get_tracer().start_as_current_span("parent") as p:
            with get_tracer().start_as_current_span("child") as c:
                pass
        assert p.trace_id == c.trace_id
        # Root span should be in ``traces`` exactly once.
        roots = [s for s in OBSERVABILITY["spans"] if s["parent_id"] is None]
        assert len(roots) == 1
        assert OBSERVABILITY["traces"][0]["name"] == "parent"

    def test_meter_counter_and_histogram(self):
        from mediascribe.observability import (
            OBSERVABILITY,
            clear_observability,
            get_meter,
        )

        clear_observability()
        m = get_meter()
        c = m.create_counter("hits")
        c.add(1, {"path": "/a"})
        c.add(2, {"path": "/b"})
        h = m.create_histogram("latency_ms")
        h.record(15.5)
        assert len(OBSERVABILITY["metrics"]["hits"]) == 2
        assert OBSERVABILITY["metrics"]["hits"][0]["attributes"] == {"path": "/a"}
        assert OBSERVABILITY["metrics"]["latency_ms"][0]["value"] == 15.5

    def test_install_opentelemetry_exporter_returns_false_when_missing(self):
        from mediascribe import observability

        with mock.patch.object(observability, "_REAL_OTEL_AVAILABLE", False):
            assert observability.install_opentelemetry_exporter() is False


# ---------------------------------------------------------------------------
# 6. mediascribe.web.app — extra install.md and extension page coverage
# ---------------------------------------------------------------------------
class TestWebAppInstallMd:
    """The install.md endpoint has two formats (html vs raw)."""

    def test_install_md_html_default(self):
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        c = TestClient(create_app())
        r = c.get("/api/extension/install.md")
        assert r.status_code == 200
        # HTML wrapper renders the markdown inside a <pre> block with a
        # back-link to the install page.
        assert "<pre>" in r.text
        assert "&larr; Back" in r.text or "Back" in r.text
        # And the markdown payload itself is included verbatim.
        assert "Load unpacked" in r.text

    def test_install_md_raw_markdown(self):
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        c = TestClient(create_app())
        r = c.get("/api/extension/install.md?raw=1")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        # Body is pure markdown — starts with a ``#`` and contains install steps.
        assert r.text.lstrip().startswith("#")
        assert "chrome://extensions" in r.text or "Edge" in r.text


# ---------------------------------------------------------------------------
# 7. douyin_batch.cache
# ---------------------------------------------------------------------------
class TestCacheExtras:
    """Cover the file-load error path and user-list helpers."""

    def test_load_returns_empty_on_corrupt_json(self, tmp_path):
        from douyin_batch.cache import ProcessCache

        (tmp_path / "processed_videos.json").write_text("{not json", encoding="utf-8")
        cache = ProcessCache(cache_dir=tmp_path)
        assert cache.is_processed("any") is False
        assert cache.get_stats() == {"total": 0, "success": 0, "failed": 0}

    def test_mark_processed_writes_to_disk(self, tmp_path):
        from douyin_batch.cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        cache.mark_processed("v1", "http://x/1", "t.md", "a.mp3", success=True)
        assert cache.is_processed("v1")
        rec = cache.get_processed("v1")
        assert rec["transcript"] == "t.md" and rec["success"] is True
        # Stats reflect it
        assert cache.get_stats() == {"total": 1, "success": 1, "failed": 0}

    def test_filter_unprocessed(self, tmp_path):
        from douyin_batch.cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        cache.mark_processed("a", "http://x/a", success=True)
        videos = [{"video_id": "a"}, {"video_id": "b"}, {"video_id": "c"}]
        out = cache.filter_unprocessed(videos)
        assert [v["video_id"] for v in out] == ["b", "c"]

    def test_user_videos_round_trip(self, tmp_path):
        from douyin_batch.cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        assert cache.get_user_videos("http://u/1") == []
        cache.save_user_videos("http://u/1", [{"id": 1}, {"id": 2}])
        videos = cache.get_user_videos("http://u/1")
        assert [v["id"] for v in videos] == [1, 2]
        # Hash is keyed on URL, not whitespace.
        assert cache.get_user_videos("http://u/1  ") == []

    def test_clear_resets_state(self, tmp_path):
        from douyin_batch.cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        cache.mark_processed("a", "http://x/a", success=True)
        cache.save_user_videos("http://u/1", [{"id": 1}])
        cache.clear()
        assert cache.is_processed("a") is False
        assert cache.get_user_videos("http://u/1") == []


# ---------------------------------------------------------------------------
# 8. douyin_batch.retry
# ---------------------------------------------------------------------------
class TestRetryExtras:
    """Cover backoff / on_retry / max_retries=0 paths."""

    def test_retry_max_retries_zero_raises_first_exception(self):
        from douyin_batch.retry import retry

        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise ValueError("first")

        with pytest.raises(ValueError):
            retry(boom, max_retries=0, delay=0)
        assert calls["n"] == 1

    def test_retry_backoff_increases_delay(self):
        from douyin_batch.retry import retry

        # Patch time.sleep to record the delays without actually sleeping.
        delays: list[float] = []
        with mock.patch("douyin_batch.retry.time.sleep", side_effect=lambda d: delays.append(d)):

            def boom():
                raise ValueError()

            with pytest.raises(ValueError):
                retry(boom, max_retries=3, delay=0.1, backoff=2.0)

        # 4 attempts fail (0..3); sleeps fire after attempts 0, 1, 2 with
        # growing delays (0.1 → 0.2 → 0.4); the final break skips the
        # sleep so we get 3 entries, not 4.
        assert delays == [0.1, 0.2, 0.4]

    def test_retry_on_retry_callback_receives_attempt_and_exc(self):
        from douyin_batch.retry import retry

        attempts: list[tuple[int, Exception]] = []
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise RuntimeError(calls["n"])

        def on_retry(attempt, exc):
            attempts.append((attempt, exc))

        with pytest.raises(RuntimeError) as excinfo:
            retry(boom, max_retries=2, delay=0, exceptions=(RuntimeError,), on_retry=on_retry)
        # on_retry is called *before* the sleep on attempts 0 and 1, so
        # we get two callbacks with attempt numbers 1 and 2 and exc args
        # 1 and 2.  The third attempt breaks out without calling on_retry
        # and re-raises RuntimeError(3).
        assert [a for a, _ in attempts] == [1, 2]
        assert attempts[-1][1].args == (2,)
        assert excinfo.value.args == (3,)

    def test_retry_does_not_retry_on_unrelated_exception(self):
        from douyin_batch.retry import retry

        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise KeyError("nope")

        with pytest.raises(KeyError):
            retry(boom, max_retries=3, delay=0, exceptions=(ValueError,))
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# 9. douyin_batch.security
# ---------------------------------------------------------------------------
class TestSecurityExtras:
    """Edge cases for safe_join_path, validate_video_id, check_url_safety."""

    def test_safe_join_path_blocks_traversal(self, tmp_path):
        from douyin_batch.security import safe_join_path

        # OK: inside base
        p = safe_join_path(tmp_path, "a", "b.txt")
        assert p is not None
        assert p == (tmp_path / "a" / "b.txt").resolve()
        # Escape attempt
        assert safe_join_path(tmp_path, "..", "outside.txt") is None
        assert safe_join_path(tmp_path, "..", "..", "etc", "passwd") is None

    def test_validate_video_id(self):
        from douyin_batch.security import validate_video_id

        assert validate_video_id("BV1Nd596vEyU") is True
        assert validate_video_id("BV1") is False  # too short
        assert validate_video_id("1234567890123") is True
        assert validate_video_id("123") is False  # too short
        assert validate_video_id("") is False
        assert validate_video_id(None) is False  # type: ignore[arg-type]
        # Bad char
        assert validate_video_id("BV1abcdefghi!") is False

    def test_check_url_safety_trusted_subdomain(self):
        from douyin_batch.security import check_url_safety

        ok, reason = check_url_safety("https://www.bilibili.com/video/BV1")
        assert ok is True and reason == "OK"
        ok, reason = check_url_safety("https://m.bilibili.com/video/BV1")
        assert ok is True and reason == "OK"
        # Untrusted
        ok, reason = check_url_safety("https://evil.com/x")
        assert ok is False and "Untrusted" in reason

    def test_check_url_safety_custom_allowed_domains(self):
        from douyin_batch.security import check_url_safety

        ok, _ = check_url_safety(
            "https://my-mirror.example.com/foo",
            allowed_domains={"my-mirror.example.com"},
        )
        assert ok is True

    def test_sanitize_filename_extreme_inputs(self):
        from douyin_batch.security import sanitize_filename

        # Empty / None
        assert sanitize_filename("") == "unnamed"
        assert sanitize_filename(None) == "unnamed"  # type: ignore[arg-type]
        # Whitespace-only
        assert sanitize_filename("   ") == "unnamed"
        # Path separators
        assert "/" not in sanitize_filename("a/b/c")
        assert "\\" not in sanitize_filename("a\\b\\c")
        # Parent ref
        assert ".." not in sanitize_filename("..")
        # Length cap with extension preserved
        long = "a" * 250 + ".mp4"
        out = sanitize_filename(long, max_length=50)
        assert len(out) <= 50
        assert out.endswith(".mp4")

    def test_limit_string_length_non_string_returns_empty(self):
        from douyin_batch.security import limit_string_length

        assert limit_string_length(None) == ""  # type: ignore[arg-type]
        assert limit_string_length(123) == ""  # type: ignore[arg-type]
        assert limit_string_length("abcdef", 3) == "abc"
        assert limit_string_length("abc", 10) == "abc"


# ---------------------------------------------------------------------------
# 10. douyin_batch.platform_compat — pure helpers
# ---------------------------------------------------------------------------
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
