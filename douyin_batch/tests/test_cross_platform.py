"""Test cross-platform utilities"""

import sys
from pathlib import Path

# Add project root (two levels up: tests/ -> douyin_batch/ -> project_root)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

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

print("=== OS Detection ===")
print("OS:", get_os())
print("is_windows:", is_windows())
print("is_macos:", is_macos())
print("is_linux:", is_linux())
print("Python OK:", check_python_version((3, 8)))
print("Info:", get_python_info())

print("\n=== safe_filename ===")
test_cases = [
    'test<>:"|?*file.mp4',
    "CON",
    "a" * 300 + ".mp4",
    "中文测试.mp4",
    "../../etc/passwd",
    "normal_file.mp4",
]
for tc in test_cases:
    print(f"  {tc[:30]!r} -> {safe_filename(tc)!r}")

print("\n=== normalize_path ===")
test_paths = ["test.txt", "~/test.txt", "./relative/file.txt"]
for tp in test_paths:
    p = normalize_path(tp)
    print(f"  {tp!r} -> {p}")

print("\n=== Security utils ===")
from douyin_batch.security import (
    check_url_safety,
    is_safe_url,
    sanitize_filename,
    validate_video_id,
)

print("Safe URL https://www.bilibili.com:", is_safe_url("https://www.bilibili.com/video/BV1xxx"))
print("Safe URL javascript:alert:", is_safe_url("javascript:alert(1)"))
print("Safe URL empty:", is_safe_url(""))
print("Safe URL <script>:", is_safe_url("https://test.com/<script>"))

# BV ID validation
print("Valid BV1xxx:", validate_video_id("BV1Nd596vEyU"))
print("Valid 1234567890:", validate_video_id("1234567890"))
print("Invalid abc:", validate_video_id("abc"))

# sanitize_filename
print("Sanitize ../../../etc/passwd:", sanitize_filename("../../../etc/passwd"))
print("Sanitize empty:", sanitize_filename(""))

# URL safety check
ok, reason = check_url_safety("https://www.bilibili.com/video/BV1xxx")
print(f"Bilibili URL: ok={ok}, reason={reason}")
ok, reason = check_url_safety("https://malicious.com/test")
print(f"Malicious URL: ok={ok}, reason={reason}")

print("\nAll tests completed!")
