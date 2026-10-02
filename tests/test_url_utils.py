"""mediascribe.url_utils 的测试 —— 迁入自 tests/test_coverage_gaps.py(按被测模块归位)。"""

from __future__ import annotations

from unittest import mock

###########################################################################
# 迁入自 tests/test_coverage_gaps.py —— TestUrlUtils(mediascribe.url_utils)
###########################################################################


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
