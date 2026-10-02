"""i18n integration checks for the batch CLI's message keys.

此前是模块级脚本(import 即执行 subprocess), pytest 收集阶段就会真跑
``python -m mediascribe --help``(2026-10-03 审查收敛为真测试函数)。
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from douyin_batch.i18n import Messages, set_language, t  # noqa: E402

# All the keys the batch CLI (douyin_batch_v3) uses
REQUIRED_KEYS = [
    "CLI_DESCRIPTION",
    "CLI_EPILOG",
    "CLI_HELP_FROM_VIDEO",
    "CLI_HELP_USER",
    "CLI_HELP_NUM",
    "CLI_HELP_WORKERS",
    "CLI_HELP_NO_HEADLESS",
    "CLI_HELP_RETRIES",
    "CLI_HELP_OUTPUT_DIR",
    "CLI_HELP_KEEP_AUDIO",
    "CLI_HELP_CONFIG",
    "CLI_HELP_LOG_LEVEL",
    "CLI_HELP_NO_CACHE",
    "CLI_HELP_CLEAR_CACHE",
    "CLI_HELP_SAVE_CONFIG",
    "CLI_HELP_LANG",
    "CLI_ERROR_NO_INPUT",
    "INFO_LOAD_CONFIG",
    "INFO_SAVE_CONFIG",
    "INFO_BANNER_TITLE",
    "INFO_BANNER_CONFIG",
    "INFO_BANNER_BROWSER_HEADLESS",
    "INFO_BANNER_BROWSER_VISIBLE",
    "INFO_BANNER_RETRIES",
    "INFO_BANNER_CACHE_ON",
    "INFO_BANNER_CACHE_OFF",
    "INFO_CACHE_CLEARED",
    "INFO_STEP1_FROM_VIDEO",
    "INFO_STEP1_USE_USER",
    "INFO_STEP1_LOAD_CONFIG",
    "INFO_STEP2",
    "INFO_STEP2_CACHED",
    "INFO_STEP3",
    "INFO_USER_URL_MISSING",
    "INFO_CLEANUP",
    "INFO_REPORT_GENERATING",
    "INFO_DONE",
    "INFO_DONE_TOTAL",
    "INFO_DONE_SUCCESS",
    "INFO_DONE_FAILED",
    "INFO_DONE_ELAPSED",
    "INFO_DONE_REPORT",
    "INFO_RUNNING_TOTAL",
    "INFO_ALL_PROCESSED",
    "SUCCESS_VIDEO_TRANSCRIBED",
    "ERROR_VIDEO_MEDIA",
    "ERROR_VIDEO_DOWNLOAD",
    "ERROR_VIDEO_TRANSCRIBE",
    "ERROR_VIDEO_EXCEPTION",
    "WARN_INTERRUPTED",
    "ERROR_UNHANDLED",
    "INFO_CACHE_STATS",
    "ERROR_NO_USER",
    "SUCCESS_USER_URL",
    "ERROR_NO_VIDEOS",
    "SUCCESS_VIDEOS_FOUND",
    "INFO_SKIP_PROCESSED",
]


def test_all_required_keys_present():
    missing = []
    for key in REQUIRED_KEYS:
        msg_dict = getattr(Messages, key, None)
        if msg_dict is None:
            missing.append(key)
            continue
        if "en" not in msg_dict or "zh" not in msg_dict:
            missing.append(f"{key} (missing en/zh)")
    assert not missing, f"{len(missing)} missing keys: {missing}"


def test_translations_differ_between_languages():
    set_language("en")
    en_title = t("CLI_DESCRIPTION")
    en_help = t("CLI_HELP_FROM_VIDEO")
    set_language("zh")
    zh_title = t("CLI_DESCRIPTION")
    zh_help = t("CLI_HELP_FROM_VIDEO")
    assert en_title != zh_title, "EN/ZH titles should differ"
    assert en_help != zh_help, "EN/ZH help should differ"


def test_format_arguments_work():
    set_language("en")
    assert "01:30" in t("INFO_DONE_ELAPSED", time="01:30")


def test_cli_help_renders():
    result = subprocess.run(
        [sys.executable, "-m", "mediascribe", "--help"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert "transcribe" in result.stdout.lower() or "mediascribe" in result.stdout.lower(), (
        "Help output missing"
    )
