"""Verify all main modules import cleanly without errors."""

import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

modules_to_test = [
    "mediascribe",
    "mediascribe.config",
    "mediascribe.inputs",
    "mediascribe.models",
    "mediascribe.pipeline",
    "mediascribe.url_utils",
    "mediascribe.audio_utils",
    "mediascribe.downloaders",
    "mediascribe.downloaders.base",
    "mediascribe.downloaders.ytdlp",
    "mediascribe.downloaders.douyin",
    "mediascribe.downloaders.youtube",
    "mediascribe.downloaders.xiaohongshu",
    "mediascribe.downloaders.wechat_mp",
    "mediascribe.transcribers",
    "mediascribe.transcribers.base",
    "mediascribe.transcribers.whisper",
    "mediascribe.transcribers.faster_whisper",
    "mediascribe.transcribers.whisperx",
    "mediascribe.mcp_server",
    "douyin_batch",
    "douyin_batch.config",
    "douyin_batch.logger",
    "douyin_batch.cache",
    "douyin_batch.retry",
    "douyin_batch.progress",
    "douyin_batch.report",
    "douyin_batch.i18n",
    "douyin_batch.platform_compat",
    "douyin_batch.security",
    "douyin_batch.browser",
    "douyin_batch.transcribe",
]

# 可选 extra 的模块：未安装对应 extra 时跳过（不计入失败），
# 避免 CI 矩阵中未装 whisperx/faster-whisper/mcp 的 job 误红。
OPTIONAL_MODULES = {
    "mediascribe.transcribers.faster_whisper",
    "mediascribe.transcribers.whisperx",
    "mediascribe.mcp_server",
}


def test_import_all_modules():
    """所有主模块必须能干净 import(可选 extra 未装时跳过)。

    此前是模块级循环(import 即执行), 见 2026-10-03 审查收敛。
    """
    failed = []
    for mod_name in modules_to_test:
        try:
            importlib.import_module(mod_name)
        except Exception as e:  # noqa: BLE001 — import 失败形态多样, 统一收集
            if mod_name in OPTIONAL_MODULES:
                continue
            failed.append((mod_name, f"{type(e).__name__}: {e}"))

    assert not failed, f"{len(failed)} modules failed to import: {failed}"
    assert len(modules_to_test) > 20, "module list looks truncated"
