"""Verify all main modules import cleanly without errors."""
import importlib
import sys
from pathlib import Path

# tests/ -> douyin_batch/ -> project_root (3 levels up)
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

modules_to_test = [
    "video2text",
    "video2text.config",
    "video2text.inputs",
    "video2text.models",
    "video2text.pipeline",
    "video2text.url_utils",
    "video2text.audio_utils",
    "video2text.downloaders",
    "video2text.downloaders.base",
    "video2text.downloaders.ytdlp",
    "video2text.downloaders.douyin",
    "video2text.downloaders.youtube",
    "video2text.downloaders.xiaohongshu",
    "video2text.downloaders.wechat_mp",
    "video2text.transcribers",
    "video2text.transcribers.base",
    "video2text.transcribers.whisper",
    "video2text.transcribers.faster_whisper",
    "video2text.transcribers.whisperx",
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

failed = []
for mod_name in modules_to_test:
    try:
        importlib.import_module(mod_name)
        print(f"  OK   {mod_name}")
    except Exception as e:
        print(f"  FAIL {mod_name}: {type(e).__name__}: {e}")
        failed.append((mod_name, e))

print(f"\n{'='*60}")
if failed:
    print(f"FAILED: {len(failed)} modules failed to import")
    for name, err in failed:
        print(f"  - {name}: {err}")
    sys.exit(1)
else:
    print(f"SUCCESS: All {len(modules_to_test)} modules imported cleanly")
