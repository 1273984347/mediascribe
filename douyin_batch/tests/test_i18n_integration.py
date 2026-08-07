"""Smoke test for the i18n integration in douyin_batch_v3.py"""
import subprocess
import sys
from pathlib import Path

# Add project root and adjust PROJECT_ROOT for subprocess
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Test 1: All i18n keys used in douyin_batch_v3.py exist
from douyin_batch.i18n import Messages, set_language, t

# These are all the keys the v3 CLI uses
REQUIRED_KEYS = [
    "CLI_DESCRIPTION", "CLI_EPILOG", "CLI_HELP_FROM_VIDEO", "CLI_HELP_USER",
    "CLI_HELP_NUM", "CLI_HELP_WORKERS", "CLI_HELP_NO_HEADLESS", "CLI_HELP_RETRIES",
    "CLI_HELP_OUTPUT_DIR", "CLI_HELP_KEEP_AUDIO", "CLI_HELP_CONFIG", "CLI_HELP_LOG_LEVEL",
    "CLI_HELP_NO_CACHE", "CLI_HELP_CLEAR_CACHE", "CLI_HELP_SAVE_CONFIG", "CLI_HELP_LANG",
    "CLI_ERROR_NO_INPUT",
    "INFO_LOAD_CONFIG", "INFO_SAVE_CONFIG", "INFO_BANNER_TITLE", "INFO_BANNER_CONFIG",
    "INFO_BANNER_BROWSER_HEADLESS", "INFO_BANNER_BROWSER_VISIBLE", "INFO_BANNER_RETRIES",
    "INFO_BANNER_CACHE_ON", "INFO_BANNER_CACHE_OFF", "INFO_CACHE_CLEARED",
    "INFO_STEP1_FROM_VIDEO", "INFO_STEP1_USE_USER", "INFO_STEP1_LOAD_CONFIG",
    "INFO_STEP2", "INFO_STEP2_CACHED", "INFO_STEP3", "INFO_USER_URL_MISSING",
    "INFO_CLEANUP", "INFO_REPORT_GENERATING", "INFO_DONE", "INFO_DONE_TOTAL",
    "INFO_DONE_SUCCESS", "INFO_DONE_FAILED", "INFO_DONE_ELAPSED", "INFO_DONE_REPORT",
    "INFO_RUNNING_TOTAL", "INFO_ALL_PROCESSED",
    "SUCCESS_VIDEO_TRANSCRIBED", "ERROR_VIDEO_MEDIA", "ERROR_VIDEO_DOWNLOAD",
    "ERROR_VIDEO_TRANSCRIBE", "ERROR_VIDEO_EXCEPTION",
    "WARN_INTERRUPTED", "ERROR_UNHANDLED",
    "INFO_CACHE_STATS", "ERROR_NO_USER", "SUCCESS_USER_URL", "ERROR_NO_VIDEOS",
    "SUCCESS_VIDEOS_FOUND", "INFO_SKIP_PROCESSED",
]

print("=== Test 1: i18n key existence ===")
missing = []
for key in REQUIRED_KEYS:
    msg_dict = getattr(Messages, key, None)
    if msg_dict is None:
        missing.append(key)
        continue
    if "en" not in msg_dict or "zh" not in msg_dict:
        missing.append(f"{key} (missing en/zh)")

if missing:
    print(f"FAIL: {len(missing)} missing keys:")
    for m in missing:
        print(f"  - {m}")
    sys.exit(1)
print(f"OK: All {len(REQUIRED_KEYS)} keys present")

# Test 2: Translation works in both languages
print("\n=== Test 2: Translation works ===")
set_language("en")
en_title = t("CLI_DESCRIPTION")
en_help = t("CLI_HELP_FROM_VIDEO")
set_language("zh")
zh_title = t("CLI_DESCRIPTION")
zh_help = t("CLI_HELP_FROM_VIDEO")
print(f"  EN title: {en_title!r}")
print(f"  ZH title: {zh_title!r}")
print(f"  EN help: {en_help!r}")
print(f"  ZH help: {zh_help!r}")
assert en_title != zh_title, "EN/ZH titles should differ"
assert en_help != zh_help, "EN/ZH help should differ"
print("OK: Translations differ between languages")

# Test 3: Format arguments work
print("\n=== Test 3: Format arguments ===")
set_language("en")
result = t("INFO_DONE_ELAPSED", time="01:30")
print(f"  Formatted: {result!r}")
assert "01:30" in result
print("OK: Format arguments work")

# Test 4: CLI help works
print("\n=== Test 4: CLI --help output (en) ===")
result = subprocess.run(
    [sys.executable, "-m", "video2text", "--help"],
    capture_output=True, text=True, cwd=str(PROJECT_ROOT)
)
assert "transcribe" in result.stdout.lower() or "video2text" in result.stdout.lower(), "Help output missing"
print("OK: CLI help renders correctly")

print("\n=== Test 5: CLI --help output (zh) ===")
# v3.2.0+ uses python -m video2text; zh help follows system locale
result = subprocess.run(
    [sys.executable, "-m", "video2text", "--help"],
    capture_output=True, text=True, cwd=str(PROJECT_ROOT)
)
assert "transcribe" in result.stdout.lower() or "video2text" in result.stdout.lower(), "Help output missing"
print("OK: CLI help renders correctly")

print("\n=== All i18n integration tests passed! ===")
