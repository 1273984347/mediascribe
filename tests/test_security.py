"""douyin_batch.security 的测试 —— 迁入自 tests/test_coverage_gaps.py(按被测模块归位)。"""

from __future__ import annotations

###########################################################################
# 迁入自 tests/test_coverage_gaps.py —— TestSecurityExtras(douyin_batch.security)
###########################################################################


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
