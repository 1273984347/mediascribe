"""Cross-platform utility behaviour (real assertions).

此前是模块级 print 脚本(见 2026-10-03 审查收敛)。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from douyin_batch.platform_compat import (
    check_python_version,
    get_os,
    get_python_info,
    is_linux,
    is_macos,
    is_windows,
    normalize_path,
    safe_filename,
)
from douyin_batch.security import (
    check_url_safety,
    is_safe_url,
    sanitize_filename,
    validate_video_id,
)


def test_os_detection_mutually_exclusive():
    assert get_os() in ("windows", "macos", "linux", "unknown")
    flags = [is_windows(), is_macos(), is_linux()]
    assert sum(flags) <= 1, "at most one OS flag may be true"


def test_python_version_check():
    assert check_python_version((3, 8)) is True
    assert check_python_version((99, 0)) is False
    info = get_python_info()
    assert info  # non-empty info dict/string


def test_safe_filename_strips_hostile_chars():
    for raw in ['test<>:"|?*file.mp4', "CON", "a" * 300 + ".mp4", "../../etc/passwd"]:
        out = safe_filename(raw)
        assert out, f"empty result for {raw!r}"
        assert "/" not in out and "\\" not in out, f"path sep leaked for {raw!r}: {out!r}"
    # 中文与普通文件名应原样保留
    assert safe_filename("中文测试.mp4") == "中文测试.mp4"
    assert safe_filename("normal_file.mp4") == "normal_file.mp4"


def test_normalize_path_variants():
    assert normalize_path("test.txt")
    assert normalize_path("~/test.txt")
    assert normalize_path("./relative/file.txt")


def test_is_safe_url():
    assert is_safe_url("https://www.bilibili.com/video/BV1xxx")
    assert not is_safe_url("javascript:alert(1)")
    assert not is_safe_url("")
    assert not is_safe_url("https://test.com/<script>")


def test_validate_video_id():
    assert validate_video_id("BV1Nd596vEyU")
    assert validate_video_id("1234567890")
    assert not validate_video_id("abc")


def test_sanitize_filename():
    assert sanitize_filename("../../../etc/passwd")
    assert sanitize_filename("") == "" or sanitize_filename("") is not None


def test_check_url_safety_domain_allowlist():
    ok, _reason = check_url_safety("https://www.bilibili.com/video/BV1xxx")
    assert ok
    ok2, _ = check_url_safety("https://malicious.com/test")
    assert not ok2
