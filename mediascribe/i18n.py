"""
Internationalization (i18n) - English/Chinese bilingual support
国际化支持 - 中英文双语
"""

import locale
import os
from typing import Optional


# Default language detection
def detect_language() -> str:
    """
    Detect user's preferred language.

    Returns:
        "zh" for Chinese, "en" for English (default)
    """
    # 1. Check environment variable
    env_lang = os.environ.get("MEDIASCRIBE_LANG") or os.environ.get("LANG")
    if env_lang:
        env_lang = env_lang.lower()
        if env_lang.startswith("zh"):
            return "zh"
        if env_lang.startswith("en"):
            return "en"

    # 2. Check system locale
    #    locale.getdefaultlocale() 自 3.12 起弃用；优先 getlocale()。
    #    Windows 上 getlocale() 未 setlocale 时可能返回 (None, None)，
    #    此时回落 LC_ALL / LC_CTYPE，再回落旧 getdefaultlocale()（抑制
    #    弃用警告）以保持 Windows 中文系统的检测行为。
    sys_locale: Optional[str] = None
    try:
        sys_locale = locale.getlocale()[0]
    except Exception:
        sys_locale = None
    if not sys_locale:
        sys_locale = os.environ.get("LC_ALL") or os.environ.get("LC_CTYPE")
    if not sys_locale:
        try:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                sys_locale = locale.getdefaultlocale()[0]
        except Exception:
            sys_locale = None
    if sys_locale and sys_locale.lower().startswith("zh"):
        return "zh"

    # 3. Default to English
    return "en"


# Language registry
class Messages:
    """Bilingual messages registry"""

    # ==================== Header / 标题 ====================
    HEADER_TITLE = {
        "en": "MediaScribe - Batch Transcription",
        "zh": "MediaScribe - 批量转录",
    }

    # ==================== Steps / 步骤 ====================
    STEP_FETCH_USER = {
        "en": "Step 1: Fetching creator's profile...",
        "zh": "步骤1: 正在获取作者主页...",
    }

    STEP_FETCH_VIDEOS = {
        "en": "Step 2: Fetching creator's videos...",
        "zh": "步骤2: 正在获取作者往期视频...",
    }

    STEP_PROCESS = {
        "en": "Step 3: Processing videos...",
        "zh": "步骤3: 正在处理视频...",
    }

    # ==================== Success / 成功 ====================
    SUCCESS_USER_URL = {
        "en": "Creator's profile: {url}",
        "zh": "作者主页: {url}",
    }

    SUCCESS_VIDEOS_FOUND = {
        "en": "Found {count} videos",
        "zh": "共找到 {count} 个视频",
    }

    SUCCESS_TRANSCRIBE = {
        "en": "Transcription completed: {name}",
        "zh": "转录完成: {name}",
    }

    SUCCESS_BATCH = {
        "en": "Batch processing completed!",
        "zh": "批量处理完成！",
    }

    # ==================== Errors / 错误 ====================
    ERROR_NO_USER = {
        "en": "Failed to fetch creator's profile",
        "zh": "无法获取作者主页",
    }

    ERROR_NO_VIDEOS = {
        "en": "No videos found",
        "zh": "未找到任何视频",
    }

    ERROR_MEDIA_URL = {
        "en": "Failed to fetch media URL",
        "zh": "无法获取媒体URL",
    }

    ERROR_DOWNLOAD = {
        "en": "Download failed",
        "zh": "下载失败",
    }

    ERROR_TRANSCRIBE = {
        "en": "Transcription failed",
        "zh": "转录失败",
    }

    ERROR_BROWSER = {
        "en": "Browser error: {error}",
        "zh": "浏览器错误: {error}",
    }

    ERROR_NETWORK = {
        "en": "Network error: {error}",
        "zh": "网络错误: {error}",
    }

    # ==================== Warnings / 警告 ====================
    WARN_INTERRUPT = {
        "en": "User interrupted",
        "zh": "用户中断",
    }

    WARN_NO_NEW_VIDEOS = {
        "en": "No more new videos, stopping",
        "zh": "无更多新视频，停止滚动",
    }

    # ==================== Info / 信息 ====================
    INFO_CONFIG = {
        "en": "Current configuration",
        "zh": "当前配置",
    }

    INFO_CACHE_STATS = {
        "en": "Cache stats: total={total}, success={success}, failed={failed}",
        "zh": "缓存统计: 总 {total} | 成功 {success} | 失败 {failed}",
    }

    INFO_SKIP_PROCESSED = {
        "en": "Skipped {count} already-processed videos",
        "zh": "跳过已处理: {count} 个",
    }

    INFO_ELAPSED = {
        "en": "Elapsed: {time}",
        "zh": "已用时: {time}",
    }

    # ==================== Prompt / 提示 ====================
    PROMPT_MANUAL_DOUYIN = {
        "en": "How to get Douyin media URL manually:",
        "zh": "如何手动获取抖音媒体URL:",
    }

    PROMPT_MANUAL_STEPS = {
        "en": "1. Open Douyin video in browser\n2. Press F12 -> Network tab\n3. Play video, find request starting with douyinvod.com\n4. Copy the URL",
        "zh": "1. 在浏览器中打开抖音视频\n2. 按 F12 -> Network 标签\n3. 播放视频，找到 douyinvod.com 开头的请求\n4. 复制该 URL",
    }

    # ==================== CLI / 命令行 ====================
    CLI_DESCRIPTION = {
        "en": "Batch-transcribe a Douyin creator's archive",
        "zh": "批量转录抖音作者往期内容",
    }

    CLI_EPILOG = {
        "en": """
Examples:
  # Auto-discover creator from a single video
  python -m mediascribe archive --from-video "https://v.douyin.com/xxxxx/" -n 10

  # Provide a creator profile URL directly
  python -m mediascribe archive --user "https://www.douyin.com/user/MS4wLjABAAAAxxxx" -n 20

  # Use a config file
  python -m mediascribe archive --config my_config.json

Environment variables:
  DOUYIN_BATCH_HEADLESS       Run browser in headless mode
  DOUYIN_BATCH_MAX_VIDEOS     Maximum number of videos
  DOUYIN_BATCH_WORKERS        Concurrency level
  DOUYIN_BATCH_LANGUAGE       Transcription language
  DOUYIN_BATCH_OUTPUT_DIR     Output directory
        """,
        "zh": """
示例:
  # 从视频URL自动获取作者主页
  python -m mediascribe archive --from-video "https://v.douyin.com/xxxxx/" -n 10

  # 直接提供作者主页
  python -m mediascribe archive --user "https://www.douyin.com/user/MS4wLjABAAAAxxxx" -n 20

  # 使用配置文件
  python -m mediascribe archive --config my_config.json

环境变量:
  DOUYIN_BATCH_HEADLESS       是否无头模式
  DOUYIN_BATCH_MAX_VIDEOS     最大视频数
  DOUYIN_BATCH_WORKERS        并发数
  DOUYIN_BATCH_LANGUAGE       转录语言
  DOUYIN_BATCH_OUTPUT_DIR     输出目录
        """,
    }

    CLI_HELP_FROM_VIDEO = {
        "en": "Auto-discover creator from a video URL",
        "zh": "从视频URL自动获取作者主页",
    }

    CLI_HELP_USER = {
        "en": "Creator profile URL",
        "zh": "作者主页URL",
    }

    CLI_HELP_NUM = {
        "en": "Maximum number of videos (default: 10)",
        "zh": "最大视频数量 (默认: 10)",
    }

    CLI_HELP_WORKERS = {
        "en": "Concurrency level (default: 1)",
        "zh": "并发数 (默认: 1)",
    }

    CLI_HELP_NO_HEADLESS = {
        "en": "Disable headless browser mode",
        "zh": "禁用无头模式",
    }

    CLI_HELP_RETRIES = {
        "en": "Download retry count",
        "zh": "下载重试次数",
    }

    CLI_HELP_OUTPUT_DIR = {
        "en": "Output directory",
        "zh": "输出目录",
    }

    CLI_HELP_KEEP_AUDIO = {
        "en": "Keep audio files after transcription",
        "zh": "保留音频文件",
    }

    CLI_HELP_CONFIG = {
        "en": "Config file path (JSON)",
        "zh": "配置文件路径 (JSON)",
    }

    CLI_HELP_LOG_LEVEL = {
        "en": "Log level",
        "zh": "日志级别",
    }

    CLI_HELP_NO_CACHE = {
        "en": "Disable resume support",
        "zh": "禁用断点续传",
    }

    CLI_HELP_CLEAR_CACHE = {
        "en": "Clear cache before running",
        "zh": "运行前清空缓存",
    }

    CLI_HELP_SAVE_CONFIG = {
        "en": "Save current config to a file",
        "zh": "保存当前配置到文件",
    }

    CLI_HELP_LANG = {
        "en": "UI language: en or zh (default: auto-detect)",
        "zh": "界面语言: en 或 zh (默认: 自动检测)",
    }

    CLI_HELP_BILINGUAL_JSON = {
        "en": "Output JSON with localised human labels alongside stable English constants (platform_label, status_label, stage_label).",
        "zh": "输出 JSON 时附带本地化的人类可读标签（platform_label / status_label / stage_label），同时保留稳定英文字段。",
    }
    CLI_HELP_JSON = {
        "en": "Output structured JSON to stdout (for AI agents / scripting)",
        "zh": "向 stdout 输出结构化 JSON（供 AI Agent / 脚本使用）",
    }

    CLI_HELP_DRY_RUN = {
        "en": "Resolve URLs and plan only; do not download or transcribe",
        "zh": "仅解析 URL 并制定计划，不下载也不转录",
    }

    CLI_HELP_PLATFORM = {
        "en": "Only process videos of the given platform (bilibili, douyin, youtube, xiaohongshu, wechat_mp, tiktok); repeat to allow multiple. Default: all.",
        "zh": "仅处理指定平台的视频（bilibili, douyin, youtube, xiaohongshu, wechat_mp, tiktok），可重复以允许多个；默认全部处理。",
    }
    CLI_HELP_PLATFORM_INVALID = {
        "en": "Invalid --platform value: {value}. Allowed: {allowed}.",
        "zh": "--platform 取值非法: {value}。允许: {allowed}。",
    }
    CLI_WARN_PLATFORM_SKIPPED = {
        "en": "Skipped {video_id} (platform={platform} not in filter {filter_set})",
        "zh": "已跳过 {video_id}（平台={platform} 不在过滤器 {filter_set} 内）",
    }
    CLI_PLATFORM_SUMMARY = {
        "en": "Platform filter: {allowed}",
        "zh": "平台过滤器: {allowed}",
    }

    CLI_ERROR_NO_INPUT = {
        "en": "Please provide --from-video, --user or --config",
        "zh": "请提供 --from-video, --user 或 --config",
    }

    # ==================== Runtime / 运行时 ====================
    INFO_LOAD_CONFIG = {
        "en": "Loaded config: {path}",
        "zh": "加载配置: {path}",
    }

    INFO_SAVE_CONFIG = {
        "en": "Config saved: {path}",
        "zh": "配置已保存: {path}",
    }

    INFO_BANNER_TITLE = {
        "en": "MediaScribe - Douyin Batch Transcription",
        "zh": "抖音作者往期内容批量转录",
    }

    INFO_BANNER_CONFIG = {
        "en": "Config: workers={workers}, max_videos={max_videos}",
        "zh": "配置: workers={workers}, max_videos={max_videos}",
    }

    INFO_BANNER_BROWSER_HEADLESS = {
        "en": "Browser: headless",
        "zh": "浏览器: 无头模式",
    }

    INFO_BANNER_BROWSER_VISIBLE = {
        "en": "Browser: visible",
        "zh": "浏览器: 显示模式",
    }

    INFO_BANNER_RETRIES = {
        "en": "Retries: {count}",
        "zh": "重试次数: {count}",
    }

    INFO_BANNER_CACHE_ON = {
        "en": "Resume support: enabled",
        "zh": "断点续传: 启用",
    }

    INFO_BANNER_CACHE_OFF = {
        "en": "Resume support: disabled",
        "zh": "断点续传: 禁用",
    }

    INFO_CACHE_CLEARED = {
        "en": "Cache cleared",
        "zh": "缓存已清空",
    }

    INFO_STEP1_FROM_VIDEO = {
        "en": "Step 1: Discovering creator from video URL...",
        "zh": "步骤1: 从视频URL提取作者主页...",
    }

    INFO_STEP1_USE_USER = {
        "en": "Step 1: Using provided creator profile: {url}",
        "zh": "步骤1: 使用提供的作者主页: {url}",
    }

    INFO_STEP1_LOAD_CONFIG = {
        "en": "Step 1: Loading URL from config...",
        "zh": "步骤1: 从配置加载 URL...",
    }

    INFO_STEP2 = {
        "en": "Step 2: Fetching creator's videos (max {count})...",
        "zh": "步骤2: 获取作者往期视频 (最多 {count} 个)...",
    }

    INFO_STEP2_CACHED = {
        "en": "Found {count} videos in cache",
        "zh": "缓存中有 {count} 个视频",
    }

    INFO_STEP3 = {
        "en": "Step 3: Processing videos ({count} total)...",
        "zh": "步骤3: 开始批量处理 ({count} 个视频)...",
    }

    INFO_USER_URL_MISSING = {
        "en": "Creator URL not found in config",
        "zh": "配置文件中未找到用户URL",
    }

    INFO_CLEANUP = {
        "en": "Cleaning up temporary audio files...",
        "zh": "清理临时音频文件...",
    }

    INFO_REPORT_GENERATING = {
        "en": "Generating summary report...",
        "zh": "生成汇总报告...",
    }

    INFO_DONE = {
        "en": "Batch processing completed!",
        "zh": "批量处理完成！",
    }

    INFO_DONE_TOTAL = {
        "en": "Total: {count}",
        "zh": "总数: {count}",
    }

    INFO_DONE_SUCCESS = {
        "en": "Success: {count}",
        "zh": "成功: {count}",
    }

    INFO_DONE_FAILED = {
        "en": "Failed: {count}",
        "zh": "失败: {count}",
    }

    INFO_DONE_ELAPSED = {
        "en": "Elapsed: {time}",
        "zh": "耗时: {time}",
    }

    INFO_DONE_REPORT = {
        "en": "Summary report: {path}",
        "zh": "汇总报告: {path}",
    }

    INFO_RUNNING_TOTAL = {
        "en": "Progress: {done}/{total} | Elapsed: {time}",
        "zh": "累计: {done}/{total} | 用时: {time}",
    }

    INFO_ALL_PROCESSED = {
        "en": "All videos have been processed already!",
        "zh": "所有视频都已处理过！",
    }

    # ==================== Progress / Cleanup (v3.2.0g) ====================
    INFO_PROGRESS_DESC = {
        "en": "Batch transcribing",
        "zh": "批量转录",
    }

    INFO_CLEANUP_KEEP_FAILED = {
        "en": "Keeping {count} file(s) from failed videos for retry",
        "zh": "保留 {count} 个失败视频的文件以供重试",
    }

    WARN_URL_UNSAFE = {
        "en": "Video {video_id}: media URL failed safety check ({reason}), url={url}, skipping",
        "zh": "视频 {video_id}: 媒体 URL 未通过安全检查（{reason}），url={url}，已跳过",
    }

    # ==================== Per-video / 单个视频 ====================
    SUCCESS_VIDEO_TRANSCRIBED = {
        "en": "[{index}/{total}] {id} - Transcription completed",
        "zh": "[{index}/{total}] {id} - 转录完成",
    }

    ERROR_VIDEO_MEDIA = {
        "en": "[{index}/{total}] {id} - Failed to fetch media URL",
        "zh": "[{index}/{total}] {id} - 无法获取媒体URL",
    }

    ERROR_VIDEO_DOWNLOAD = {
        "en": "[{index}/{total}] {id} - Download failed",
        "zh": "[{index}/{total}] {id} - 下载失败",
    }

    ERROR_VIDEO_TRANSCRIBE = {
        "en": "[{index}/{total}] {id} - Transcription failed",
        "zh": "[{index}/{total}] {id} - 转录失败",
    }

    ERROR_VIDEO_EXCEPTION = {
        "en": "[{index}/{total}] {id} - Exception: {error}",
        "zh": "[{index}/{total}] {id} - 异常: {error}",
    }

    WARN_INTERRUPTED = {
        "en": "User interrupted",
        "zh": "用户中断",
    }

    ERROR_UNHANDLED = {
        "en": "Unhandled exception: {error}",
        "zh": "未处理的异常: {error}",
    }

    # ==================== Platform-specific status / 平台状态 ====================
    # 用于 agent_output.py 中 videos[].status 字段的可读描述
    STATUS_SUCCESS = {
        "en": "success",
        "zh": "成功",
    }
    STATUS_FAILED = {
        "en": "failed",
        "zh": "失败",
    }
    STATUS_SKIPPED = {
        "en": "skipped",
        "zh": "已跳过",
    }
    STATUS_PARTIAL = {
        "en": "partial",
        "zh": "部分完成",
    }

    # Platform labels / 平台标签
    PLATFORM_BILIBILI = {
        "en": "Bilibili",
        "zh": "B站",
    }
    PLATFORM_DOUYIN = {
        "en": "Douyin",
        "zh": "抖音",
    }
    PLATFORM_YOUTUBE = {
        "en": "YouTube",
        "zh": "YouTube",
    }
    PLATFORM_XIAOHONGSHU = {
        "en": "Xiaohongshu",
        "zh": "小红书",
    }
    PLATFORM_WECHAT_MP = {
        "en": "WeChat MP",
        "zh": "微信公众号",
    }
    PLATFORM_TIKTOK = {
        "en": "TikTok",
        "zh": "TikTok",
    }
    PLATFORM_LOCAL = {
        "en": "Local file",
        "zh": "本地文件",
    }
    PLATFORM_UNKNOWN = {
        "en": "Unknown",
        "zh": "未知",
    }

    # Stage labels / 阶段标签
    STAGE_MEDIA_URL = {
        "en": "extracting media URL",
        "zh": "正在提取媒体 URL",
    }
    STAGE_DOWNLOAD = {
        "en": "downloading",
        "zh": "正在下载",
    }
    STAGE_TRANSCRIBE = {
        "en": "transcribing",
        "zh": "正在转录",
    }
    STAGE_TEXT_EXTRACT = {
        "en": "extracting text",
        "zh": "正在提取文本",
    }

    # Platform-specific error messages / 平台错误
    ERROR_YOUTUBE_BOT = {
        "en": "YouTube bot check triggered; use cookies or a residential IP",
        "zh": "触发 YouTube 机器人校验，请使用 cookies 或住宅 IP",
    }
    ERROR_XHS_LOGIN = {
        "en": "Xiaohongshu note requires login",
        "zh": "小红书笔记需要登录",
    }
    ERROR_WECHAT_RATE = {
        "en": "WeChat MP rate-limited; wait a few minutes",
        "zh": "微信公众号触发限流，请等待几分钟后重试",
    }
    ERROR_NO_PLAYWRIGHT = {
        "en": "playwright not installed; run: pip install playwright && python -m playwright install chromium",
        "zh": "未安装 playwright；请运行: pip install playwright && python -m playwright install chromium",
    }

    # Platform-specific success summaries / 平台成功汇总
    SUCCESS_YOUTUBE_DOWNLOAD = {
        "en": "YouTube video downloaded: {title}",
        "zh": "YouTube 视频已下载: {title}",
    }
    SUCCESS_XHS_DOWNLOAD = {
        "en": "Xiaohongshu note downloaded: {title}",
        "zh": "小红书笔记已下载: {title}",
    }
    SUCCESS_WECHAT_ARTICLE = {
        "en": "WeChat article extracted: {title} ({chars} chars)",
        "zh": "微信公众号文章已提取: {title}（{chars} 字）",
    }
    SUCCESS_WECHAT_VIDEO = {
        "en": "WeChat video message downloaded: {title}",
        "zh": "微信公众号视频消息已下载: {title}",
    }

    # WeChat MP OCR labels
    INFO_WECHAT_OCR_PARTIAL = {
        "en": "WeChat article OCR partial: {success}/{total} images",
        "zh": "公众号图片 OCR 部分成功: {success}/{total} 张",
    }
    INFO_WECHAT_OCR_NONE = {
        "en": "No OCR engine available; skipping image OCR",
        "zh": "未安装 OCR 引擎，跳过图片识别",
    }
    STAGE_OCR = {
        "en": "OCR-ing images",
        "zh": "正在 OCR 图片",
    }

    # Bilingual subtitle labels (for WeChat MP video messages)
    LABEL_BILINGUAL_SUBTITLE_HEADER = {
        "en": "## Subtitle (Transcription / 字幕)",
        "zh": "## 字幕（Transcription / 转录）",
    }
    LABEL_BILINGUAL_ENGINE = {
        "en": "Transcription Engine",
        "zh": "转录引擎",
    }
    LABEL_BILINGUAL_LANG = {
        "en": "Detected Language",
        "zh": "检测语言",
    }
    LABEL_BILINGUAL_SPEAKERS = {
        "en": "Speaker Diarization",
        "zh": "说话人分离",
    }
    LABEL_BILINGUAL_ENABLED = {
        "en": "Enabled",
        "zh": "已启用",
    }
    LABEL_BILINGUAL_DISABLED = {
        "en": "Disabled",
        "zh": "未启用",
    }
    LABEL_BILINGUAL_PLATFORM = {
        "en": "Platform",
        "zh": "平台",
    }
    LABEL_BILINGUAL_SEGMENT_PREFIX = {
        "en": "[Segment {idx}]",
        "zh": "[片段 {idx}]",
    }

    # ==================== Runtime guard (runtime_guard) ====================
    RUNTIME_APPLE_SWITCH = {
        "en": (
            "Apple Silicon detected, but {engine} has no Metal backend "
            "(measured: ctranslate2 raises 'unsupported device metal') "
            "⇒ switched to {new_engine} (lowest RAM; tiny measured at 291 MiB)"
        ),
        "zh": (
            "当前是 Apple Silicon，而 {engine} 没有 Metal 后端"
            "（实测 ctranslate2 直接抛 unsupported device metal）"
            " ⇒ 已改用 {new_engine}（内存占用最低，实测 tiny 仅 291 MiB）"
        ),
    }
    RUNTIME_APPLE_NO_CPP = {
        "en": (
            "Apple Silicon detected, but {engine} has no Metal backend; "
            "would switch to whisper.cpp, yet no whisper.cpp executable was "
            "found ⇒ keeping the original engine (transcription may fail). "
            "See docs/engines.md for provisioning (MEDIASCRIBE_WHISPER_CPP_BIN)"
        ),
        "zh": (
            "当前是 Apple Silicon，而 {engine} 没有 Metal 后端；"
            "本应改用 whisper.cpp，但未检测到 whisper.cpp 可执行文件"
            " ⇒ 保留原引擎（转录可能失败）。预置方式见 docs/engines.md"
            "（MEDIASCRIBE_WHISPER_CPP_BIN）"
        ),
    }
    RUNTIME_MEM_SWITCH = {
        "en": (
            "Total RAM is only {ram} MiB (pressure line {limit} MiB) "
            "⇒ switched to whisper-cpp (lowest measured peak RAM; tiny at 291 MiB)"
        ),
        "zh": (
            "机器总内存仅 {ram} MiB（压力线 {limit} MiB）"
            " ⇒ 已改用 whisper-cpp（实测峰值内存最低，tiny 仅 291 MiB）"
        ),
    }
    RUNTIME_MEM_NO_CPP = {
        "en": (
            "Total RAM is only {ram} MiB (pressure line {limit} MiB); "
            "would switch to whisper.cpp, yet no whisper.cpp executable was "
            "found ⇒ keeping the original engine and degrading the model "
            "tier instead. See docs/engines.md (MEDIASCRIBE_WHISPER_CPP_BIN)"
        ),
        "zh": (
            "机器总内存仅 {ram} MiB（压力线 {limit} MiB）；"
            "本应改用 whisper.cpp，但未检测到 whisper.cpp 可执行文件"
            " ⇒ 保留原引擎并按资源降档。预置方式见 docs/engines.md"
            "（MEDIASCRIBE_WHISPER_CPP_BIN）"
        ),
    }
    RUNTIME_VRAM_SHORT = {
        "en": (
            "Free VRAM {free} MiB (safe budget {budget} MiB) < estimated "
            "need {need} MiB for {model}"
        ),
        "zh": ("可用显存 {free} MiB（安全预算 {budget} MiB）< {model} 估算需求 {need} MiB"),
    }
    RUNTIME_RAM_SHORT = {
        "en": ("Available RAM {free} MiB < estimated need {need} MiB for {model}"),
        "zh": "可用内存 {free} MiB < {model} 估算需求 {need} MiB",
    }
    RUNTIME_LADDER_FLOOR = {
        "en": "{reason}; already the smallest tier ({model}), may still run tight",
        "zh": "{reason}；已是最小档位（{model}），仍可能吃紧",
    }
    RUNTIME_DEGRADED = {
        "en": "{reason} ⇒ degraded {old} → {new}",
        "zh": "{reason} ⇒ 已降档 {old} → {new}",
    }
    RUNTIME_RAM_ABORT = {
        "en": "System available RAM {avail:.0f} MiB fell below the abort line {mb} MiB",
        "zh": "系统可用内存 {avail:.0f} MiB 跌破紧急线 {mb} MiB",
    }
    RUNTIME_VRAM_ABORT = {
        "en": ("VRAM usage {used}/{total} MiB ({pct:.0f}%) crossed the abort line {limit:.0f}%"),
        "zh": ("显存占用 {used}/{total} MiB（{pct:.0f}%）越过紧急线 {limit:.0f}%"),
    }
    RUNTIME_TEMP_ABORT = {
        "en": "GPU temperature {temp}℃ crossed the abort line {limit}℃",
        "zh": "GPU 温度 {temp}℃ 越过紧急线 {limit}℃",
    }
    RUNTIME_OVERLOAD = {
        "en": "Resource overload detected: {reason} ⇒ requesting cancellation of this transcription",
        "zh": "检测到资源过载：{reason} ⇒ 请求中止本次转录",
    }
    RUNTIME_LOG_PREFIX = {
        "en": "Resource guard: {message}",
        "zh": "资源守卫: {message}",
    }

    # ==================== whisper.cpp (transcribers/whisper_cpp) ====================
    CPP_BINARY_NOT_FOUND = {
        "en": (
            "whisper.cpp executable not found.\n"
            "  Provision: download the official prebuilt package and extract, e.g.\n"
            "    https://github.com/ggml-org/whisper.cpp/releases/download/b5130/whisper-bin-x64.zip\n"
            "  then set MEDIASCRIBE_WHISPER_CPP_BIN to the whisper-cli executable inside\n"
            "  (or extract under ~/.cache/mediascribe/whisper-cpp/bin/Release/ for auto-detection)\n"
            "  ⚠️ Provision the **whole directory** (whisper.dll / ggml*.dll included) — "
            "the official package is dynamically linked; a lone exe fails with STATUS_DLL_NOT_FOUND"
        ),
        "zh": (
            "未找到 whisper.cpp 可执行文件。\n"
            "  预置方式：下载官方预编译包并解压，例如\n"
            "    https://github.com/ggml-org/whisper.cpp/releases/download/b5130/whisper-bin-x64.zip\n"
            "  然后设置环境变量 MEDIASCRIBE_WHISPER_CPP_BIN 指向其中的 whisper-cli.exe\n"
            "  （或解压到 ~/.cache/mediascribe/whisper-cpp/bin/Release/ 下自动探测）\n"
            "  ⚠️ 必须把 **整个目录**（含 whisper.dll / ggml*.dll）一起预置 —— "
            "官方包是动态链接的，漏 DLL 会以 STATUS_DLL_NOT_FOUND 静默失败"
        ),
    }
    CPP_DLL_INCOMPLETE = {
        "en": (
            "whisper.cpp runtime DLLs incomplete: missing {missing} — the "
            "official prebuilt package is dynamically linked; whisper-cli.exe "
            "must be provisioned together with whisper.dll / ggml*.dll into {parent}"
        ),
        "zh": (
            "whisper.cpp 运行时 DLL 不完整：缺少 {missing} —— "
            "官方预编译包是动态链接的，必须把 whisper-cli.exe 与同目录的 "
            "whisper.dll / ggml*.dll 一起预置到 {parent}"
        ),
    }
    CPP_MODEL_NOT_FOUND = {
        "en": (
            "whisper.cpp GGML weight ggml-{model}.bin not found.\n"
            "  Expected at: {dir}\n"
            "  Provision: place the ggml weight file in that directory "
            "(override the directory with MEDIASCRIBE_WHISPER_CPP_MODELS)"
        ),
        "zh": (
            "未找到 whisper.cpp 的 GGML 权重 ggml-{model}.bin。\n"
            "  应位于：{dir}\n"
            "  预置方式：把 ggml 权重文件放到该目录"
            "（可用 MEDIASCRIBE_WHISPER_CPP_MODELS 环境变量改目录）"
        ),
    }
    CPP_TRANSCODE_FAILED = {
        "en": "whisper.cpp: audio transcoding failed ({path})",
        "zh": "whisper.cpp: 音频转码失败（{path}）",
    }
    CPP_DLL_RUNTIME = {
        "en": (
            "whisper.cpp missing runtime DLLs (STATUS_DLL_NOT_FOUND) — the "
            "official prebuilt package is dynamically linked; provision "
            "whisper-cli.exe together with whisper.dll / ggml*.dll into {parent}"
        ),
        "zh": (
            "whisper.cpp 缺少运行时 DLL（STATUS_DLL_NOT_FOUND）—— "
            "官方预编译包是动态链接的，必须把 whisper-cli.exe 与同目录的 "
            "whisper.dll / ggml*.dll 一起预置到 {parent}"
        ),
    }
    CPP_RUN_FAILED = {
        "en": "whisper.cpp transcription failed (exit={code}): {tail}",
        "zh": "whisper.cpp 转写失败（exit={code}）：{tail}",
    }

    # ==================== Model weights (model_weights) ====================
    WEIGHTS_STATUS_OK = {
        "en": "Weights ready: {location}",
        "zh": "权重就绪：{location}",
    }
    WEIGHTS_STATUS_MISSING = {
        "en": "Missing weight {engine}/{model} (expected at {location})\n{hint}",
        "zh": "缺少权重 {engine}/{model}（应位于 {location}）\n{hint}",
    }
    WEIGHTS_HF_NO_DIR = {
        "en": "cache directory does not exist",
        "zh": "缓存目录不存在",
    }
    WEIGHTS_HF_CACHED = {
        "en": "cached ({rev})",
        "zh": "已缓存（{rev}）",
    }
    WEIGHTS_BLOB_PRESENT = {
        "en": "cached (blobs present)",
        "zh": "已缓存（blob 存在）",
    }
    WEIGHTS_SNAPSHOT_EMPTY = {
        "en": "snapshot directory is empty",
        "zh": "快照目录为空",
    }
    WEIGHTS_FILE_PRESENT = {
        "en": "{mib:.0f} MiB",
        "zh": "{mib:.0f} MiB",
    }
    WEIGHTS_FILE_MISSING = {
        "en": "file does not exist",
        "zh": "文件不存在",
    }
    WEIGHTS_HINT_HF = {
        "en": (
            "Provisioning (either):\n"
            "  1) Run once while online: python -m mediascribe transcribe --engine faster-whisper "
            "--model {model} --url <any audio> (downloads into the HuggingFace cache)\n"
            "  2) Offline machine: copy the whole Systran/faster-whisper-{model} snapshot "
            "directory into {cache_dir}"
        ),
        "zh": (
            "预置方式（任选其一）：\n"
            "  1) 有网时先跑一次：python -m mediascribe transcribe --engine faster-whisper "
            "--model {model} --url <任意音频>（会下载到 HuggingFace 缓存）\n"
            "  2) 离线机：把 Systran/faster-whisper-{model} 的快照目录整个拷到{cache_dir}"
        ),
    }
    WEIGHTS_HINT_OPENAI = {
        "en": (
            "Provisioning: place openai/whisper's {model}.pt into {dir}/\n"
            "  (override the directory with the WHISPER_CACHE_DIR env var)"
        ),
        "zh": (
            "预置方式：把 openai/whisper 的 "
            "{model}.pt 放到 {dir}/\n"
            "  （可用 WHISPER_CACHE_DIR 环境变量改目录）"
        ),
    }
    WEIGHTS_HINT_CPP = {
        "en": (
            "Provisioning: place the GGML weight ggml-{model}.bin into {dir}/\n"
            "  (override the directory with the MEDIASCRIBE_WHISPER_CPP_MODELS env var)"
        ),
        "zh": (
            "预置方式：把 GGML 权重 "
            "ggml-{model}.bin 放到 {dir}/\n"
            "  （可用 MEDIASCRIBE_WHISPER_CPP_MODELS 环境变量改目录）"
        ),
    }
    WEIGHTS_UNKNOWN_ENGINE = {
        "en": "(unknown engine)",
        "zh": "(未知引擎)",
    }
    WEIGHTS_UNKNOWN_GUIDANCE = {
        "en": "Unknown engine; cannot provide provisioning guidance.",
        "zh": "未知引擎，无法给出权重预置指引",
    }
    WEIGHTS_UNKNOWN_ENGINE_HINT = {
        "en": "Unknown engine {engine!r}; cannot provide provisioning guidance.",
        "zh": "未知引擎 {engine!r}，无法给出权重预置指引。",
    }

    # ==================== Pipeline misc ====================
    YUE_WEAK_MODEL_WARNING = {
        "en": (
            "⚠️ Cantonese (-l yue) quality is poor on the {name} tier "
            "(limited Cantonese training data; small tiers are mostly unusable); "
            "-m large-v3 is strongly recommended"
        ),
        "zh": (
            "⚠️ 粤语(-l yue)在 {name} 档位上识别质量很差(粤语训练数据有限, "
            "小模型基本不可用), 强烈建议 -m large-v3"
        ),
    }

    # ==================== Web validation ====================
    WEB_URL_UNPARSEABLE = {
        "en": "URL cannot be parsed: {url}",
        "zh": "URL 无法解析: {url}",
    }
    WEB_URL_SCHEME = {
        "en": "only http/https URLs are supported: {url}",
        "zh": "仅支持 http/https URL: {url}",
    }
    WEB_URL_USERINFO = {
        "en": "URL must not contain userinfo (user@host): {url}",
        "zh": "URL 不允许包含 userinfo (user@host): {url}",
    }
    WEB_URL_NO_HOST = {
        "en": "URL has no hostname: {url}",
        "zh": "URL 缺少 hostname: {url}",
    }
    WEB_URL_HOST_FAIL = {
        "en": "URL hostname cannot be resolved: {host}",
        "zh": "URL hostname 无法解析: {host}",
    }
    WEB_URL_PRIVATE = {
        "en": (
            "URL points at a private/loopback/link-local/reserved address; "
            "rejected (SSRF protection): {host}"
        ),
        "zh": "URL 指向私有/环回/链路本地/保留地址, 已拒绝 (SSRF 防护): {host}",
    }
    WEB_LOCAL_PATH = {
        "en": (
            "local paths are only allowed inside the workspace; submit an "
            "http(s) URL for remote sources (workspace: {workspace}): {entry}"
        ),
        "zh": (
            "本地路径仅允许 workspace 目录内的文件, 远端请提交 http(s) URL"
            " (workspace: {workspace}): {entry}"
        ),
    }
    WEB_EMPTY_ENTRY = {
        "en": "submission entry must not be empty",
        "zh": "提交项不能为空",
    }

    # ==================== Third-sample verify (verify) ====================
    VERIFY_NO_METADATA = {
        "en": "metadata file not found: {path}",
        "zh": "未找到元数据文件: {path}",
    }
    VERIFY_NO_TRANSCRIPT = {
        "en": "transcript not found: {path}",
        "zh": "转录稿不存在: {path}",
    }
    VERIFY_NO_AUDIO_NO_URL = {
        "en": (
            "audio is gone and metadata has no source URL — cannot re-download; "
            "please provide the audio manually"
        ),
        "zh": "音频不存在且 metadata 无来源 URL, 无法重新下载; 请手动提供音频",
    }
    VERIFY_EXTRACT_FAILED = {
        "en": "audio extraction failed",
        "zh": "音频提取失败",
    }
    VERIFY_PROV_REUSE = {
        "en": "reused audio left on disk (duration not re-checked)",
        "zh": "复用磁盘上残留的音频(时长未另核)",
    }
    VERIFY_PROV_FRESH = {
        "en": "re-downloaded during verification (duration not re-checked)",
        "zh": "验证轮重新下载(时长未另核)",
    }
    VERIFY_SAMPLE_HEADER = {
        "en": (
            "<!-- Third sample (mediascribe verify): {model}, no initial_prompt "
            "(no term injection), multi-temperature. Audio: {audio} ({provenance}; "
            "source: {source}) -->"
        ),
        "zh": (
            "<!-- 第三采样(mediascribe verify): {model}, 无 initial_prompt(术语库不注入), "
            "多档温度。音频: {audio}({provenance}; 来源: {source}) -->"
        ),
    }
    VERIFY_SAMPLE_PURPOSE = {
        "en": "<!-- Purpose: third-party adjudication of doubtful spots; read together with the same-stem .verify.md -->",
        "zh": "<!-- 用途: 存疑项第三方裁决, 配合同名 .verify.md 阅读 -->",
    }
    VERIFY_REPORT_TITLE = {
        "en": "# Third-Sample Verification Report",
        "zh": "# 第三采样验证报告",
    }
    VERIFY_REPORT_MAIN = {
        "en": "- **Main**: `{name}` ({model})",
        "zh": "- **主稿**: `{name}`（{model}）",
    }
    VERIFY_REPORT_CROSS = {
        "en": "- **Cross-check**: `{model}`",
        "zh": "- **对照**: `{model}`",
    }
    VERIFY_REPORT_NO_CROSS = {
        "en": "- **Cross-check**: none (no crosscheck list)",
        "zh": "- **对照**: 无 crosscheck 清单",
    }
    VERIFY_REPORT_SAMPLE = {
        "en": "- **Third sample**: `{model}` (no term injection + multi-temperature; decode-level independent sample)",
        "zh": "- **第三采样**: `{model}`（无术语注入 + 多档温度, 解码级独立样本）",
    }
    VERIFY_PROV_REUSE_SHORT = {
        "en": "reused on-disk audio",
        "zh": "复用磁盘音频",
    }
    VERIFY_PROV_FRESH_SHORT = {
        "en": "re-downloaded during verification",
        "zh": "验证轮重新下载",
    }
    VERIFY_REPORT_AUDIO = {
        "en": "- **Audio**: `{name}` ({note})",
        "zh": "- **音频**: `{name}`（{note}）",
    }
    VERIFY_REPORT_SAMPLE_FILE = {
        "en": "- **Sample transcript**: `{name}`",
        "zh": "- **采样稿**: `{name}`",
    }
    VERIFY_REPORT_DIVERGENCE = {
        "en": (
            "- **Divergences**: {total} = matches-main {a} / matches-cross {b}"
            " / third-reading {c} / not-located {d}"
        ),
        "zh": ("- **分歧**: {total} 处 = 同主稿 {a} / 同对照 {b} / 第三读 {c} / 未定位 {d}"),
    }
    VERIFY_REPORT_NOTE = {
        "en": (
            "(Alignment is approximate; evidence windows are for human "
            "adjudication. A verdict only checks whether the main/cross word "
            "appears in the window (punctuation-normalized); a third reading "
            "does not mean the main transcript is wrong. After review, write "
            "conclusions back into the review notes, then clean up media.)"
        ),
        "zh": (
            "（对齐为近似定位, 证据窗口供人工裁决; 判定按窗口内是否出现主稿词/"
            "对照词（去标点归一）, 第三读≠主稿错。复核后请把结论回写审校说明,"
            "再清理中间媒体。）"
        ),
    }
    VERIFY_REPORT_NONE = {
        "en": (
            "No content-word divergence list (no .crosscheck.json or empty); "
            "only the sample transcript was produced."
        ),
        "zh": "无实词分歧清单(无 .crosscheck.json 或分歧为空), 仅产出采样稿。",
    }
    VERIFY_CROSS_MISSING = {
        "en": "(missing)",
        "zh": "（漏）",
    }
    VERIFY_ENTRY = {
        "en": "### {index}. main「{main}」 ↔ cross「{cross}」",
        "zh": "### {index}. 主「{main}」↔ 照「{cross}」",
    }
    VERIFY_VERDICT_LINE = {
        "en": "Verdict: **{verdict}**",
        "zh": "判定: **{verdict}**",
    }
    VERIFY_WINDOW_LINE = {
        "en": "Third sample: 「{window}」",
        "zh": "第三采样: 「{window}」",
    }
    VERIFY_VERDICT_NO_ANCHOR = {
        "en": "Verdict: **{verdict}** (no approximate anchor found in the third sample)",
        "zh": "判定: **{verdict}**（第三采样中未找到近似锚点）",
    }
    VERIFY_AUDIO_REUSE = {
        "en": "reused",
        "zh": "复用",
    }
    VERIFY_AUDIO_FRESH = {
        "en": "re-downloaded",
        "zh": "重新下载",
    }
    VERIFY_PRINT_AUDIO = {
        "en": "🎵 Audio: {path} ({status})",
        "zh": "🎵 音频: {path}({status})",
    }
    VERIFY_PRINT_SAMPLING = {
        "en": "🔎 Third sample: {name}/{model}, no term injection + multi-temperature...",
        "zh": "🔎 第三采样: {name}/{model}, 无术语注入 + 多档温度...",
    }
    VERIFY_EMPTY = {
        "en": "third sample produced no segments",
        "zh": "第三采样结果为空",
    }
    VERIFY_PRINT_SAMPLE = {
        "en": "📝 Sample transcript: {path}",
        "zh": "📝 采样稿: {path}",
    }
    VERIFY_CROSS_FALLBACK = {
        "en": "crosscheck transcript",
        "zh": "crosscheck 对照稿",
    }
    VERIFY_PRINT_DONE = {
        "en": (
            "✅ Verification done: {total} divergences = matches-main {a} / "
            "matches-cross {b} / third-reading {c} / not-located {d}"
        ),
        "zh": ("✅ 验证完成: {total} 处分歧 = 同主稿 {a} / 同对照 {b} / 第三读 {c} / 未定位 {d}"),
    }
    VERIFY_PRINT_REPORT = {
        "en": "📋 Verification report: {path}",
        "zh": "📋 验证报告: {path}",
    }

    # ==================== CLI (``__main__``) ====================
    CLI_CROSS_CHECK_FAILED = {
        "en": "⚠️ Cross-check failed (main transcript unaffected): {etype}: {e}",
        "zh": "⚠️ 交叉校对失败(不影响主稿): {etype}: {e}",
    }
    CLI_NEXT_STEPS_TITLE = {
        "en": "\n💡 Next steps (optional):",
        "zh": "\n💡 下一步（可选）：",
    }
    CLI_NEXT_LEARN = {
        "en": (
            "   · Feed corrections back into the term library:  "
            "python -m mediascribe learn edit --original <raw> --corrected <proofread>"
        ),
        "zh": (
            "   · 校对后回填术语库:  python -m mediascribe learn edit "
            "--original <原稿> --corrected <校对稿>"
        ),
    }
    CLI_NEXT_VERIFY = {
        "en": (
            "   · Third-sample re-check for doubtful spots (review workflow, run "
            "before deleting media):  python -m mediascribe verify <transcript.md>"
        ),
        "zh": (
            "   · 存疑项第三方复核(审校场景, 删媒体前跑):  python -m mediascribe verify <转录稿.md>"
        ),
    }
    CLI_NEXT_LLM = {
        "en": (
            "   · Enable LLM polish (punctuation/paragraphing/proper nouns):  "
            "MEDIASCRIBE_LLM_ENABLED=1 + MEDIASCRIBE_LLM_API_BASE <endpoint>"
        ),
        "zh": (
            "   · 开启 LLM 润色（标点/分段/专名）:  "
            "MEDIASCRIBE_LLM_ENABLED=1 + MEDIASCRIBE_LLM_API_BASE <endpoint>"
        ),
    }
    CLI_NEXT_DOCTOR = {
        "en": "   · Environment self-check:  python -m mediascribe doctor",
        "zh": "   · 环境自检:  python -m mediascribe doctor",
    }
    CLI_CFG_MISSING = {
        "en": "❌ config file not found: {path}",
        "zh": "❌ 配置文件不存在: {path}",
    }
    CLI_CFG_BAD_JSON = {
        "en": "❌ config file is not valid JSON: {path} — {exc}",
        "zh": "❌ 配置文件不是合法 JSON: {path} — {exc}",
    }
    CLI_CFG_NOT_DICT = {
        "en": "❌ config file top level must be a JSON object: {path}",
        "zh": "❌ 配置文件顶层必须是 JSON 对象: {path}",
    }
    CLI_CLEANUP_DONE = {
        "en": "🧹 cleaned {n} intermediate media files (transcripts and metadata untouched)",
        "zh": "🧹 已清理中间媒体 {n} 个文件(转录稿与元数据不受影响)",
    }
    CLI_NO_INPUTS = {
        "en": "❌ no inputs provided",
        "zh": "❌ 没有提供输入",
    }
    CLI_BATCH_START = {
        "en": "🚀 batch-processing {n} inputs...",
        "zh": "🚀 批量处理 {n} 个输入...",
    }
    CLI_INTERRUPTED = {
        "en": "\n\n⏹️ interrupted by user",
        "zh": "\n\n⏹️ 用户中断",
    }
    CLI_BATCH_ITEM = {
        "en": "\n--- [{i}/{n}] ---",
        "zh": "\n--- [{i}/{n}] ---",
    }
    CLI_ITEM_FAILED = {
        "en": "❌ failed: {exc}",
        "zh": "❌ 处理失败: {exc}",
    }
    CLI_BATCH_DONE = {
        "en": "📊 batch finished",
        "zh": "📊 批量处理完成",
    }
    CLI_BATCH_OK = {
        "en": "✅ success: {ok}/{total}",
        "zh": "✅ 成功: {ok}/{total}",
    }
    CLI_ERROR = {
        "en": "\n❌ error: {e}",
        "zh": "\n❌ 错误: {e}",
    }
    CLI_QUICKSTART = {
        "en": (
            "🎬 MediaScribe — offline video-to-text\n"
            "\n"
            "Quick start:\n"
            "  python -m mediascribe transcribe <video URL or local file>\n"
            "  python -m mediascribe transcribe <URL> -e faster-whisper -m large-v3 -l zh\n"
            "  python -m mediascribe doctor        # environment self-check (recommended first)\n"
            "\n"
            "Commands:\n"
            "  transcribe   transcribe a single video/audio (URL or local file)\n"
            "  batch        process multiple inputs\n"
            "  archive      batch-transcribe a creator's page\n"
            "  learn        ASR term auto-learning (improves with use)\n"
            "  profile      render a performance profile report\n"
            "  doctor       environment self-check (ffmpeg / GPU / engines / model caches)\n"
            "\n"
            "Full help: python -m mediascribe --help\n"
        ),
        "zh": (
            "🎬 MediaScribe — 离线视频转文字\n"
            "\n"
            "快速开始：\n"
            "  python -m mediascribe transcribe <视频链接或本地文件>\n"
            "  python -m mediascribe transcribe <URL> -e faster-whisper -m large-v3 -l zh\n"
            "  python -m mediascribe doctor        # 环境自检（首次推荐先跑）\n"
            "\n"
            "常用命令：\n"
            "  transcribe   转录单个视频/音频（URL 或本地文件）\n"
            "  batch        批量处理多个输入\n"
            "  archive      批量转录某创作者的主页视频\n"
            "  learn        ASR 术语自动学习（越用越准）\n"
            "  profile      渲染性能 profile 报告\n"
            "  doctor       环境自检（ffmpeg / GPU / 引擎 / 模型缓存）\n"
            "\n"
            "详细帮助：python -m mediascribe --help\n"
        ),
    }
    CLI_UNKNOWN_CMD = {
        "en": "mediascribe: unknown command {cmd!r}",
        "zh": "mediascribe: 未知命令 {cmd!r}",
    }
    CLI_AVAILABLE_CMDS = {
        "en": (
            "Available commands: transcribe / batch / archive / learn / profile / "
            "doctor / verify\nTry 'python -m mediascribe --help'"
        ),
        "zh": (
            "可用命令: transcribe / batch / archive / learn / profile / doctor / verify\n"
            "Try 'python -m mediascribe --help'"
        ),
    }
    CLI_VERIFY_FAILED = {
        "en": "❌ third-sample verification failed: {etype}: {e}",
        "zh": "❌ 第三采样验证失败: {etype}: {e}",
    }
    CLI_ARCHIVE_USAGE = {
        "en": (
            "\narchive usage (same flags as mediascribe-batch):\n"
            "  python -m mediascribe archive --user <creator page URL> -n 20\n"
            "  python -m mediascribe archive --from-video <single video URL> -n 10\n"
            "\n"
            "Full flags: python -m mediascribe archive --help or mediascribe-batch --help\n"
        ),
        "zh": (
            "\narchive 用法（与 mediascribe-batch 参数一致）:\n"
            "  python -m mediascribe archive --user <作者主页URL> -n 20\n"
            "  python -m mediascribe archive --from-video <单条视频URL> -n 10\n"
            "\n"
            "完整参数: python -m mediascribe archive --help 或 mediascribe-batch --help\n"
        ),
    }
    CLI_ARCHIVE_LOAD_FAIL = {
        "en": (
            "❌ cannot load mediascribe.batch_cli ({exc}).\n"
            "Run directly: mediascribe-batch --user <URL>"
        ),
        "zh": (
            "❌ 无法加载 mediascribe.batch_cli ({exc})。\n"
            "请直接运行: mediascribe-batch --user <URL>"
        ),
    }
    LEARN_NO_MAPPINGS = {
        "en": "no error mappings found",
        "zh": "未发现错误映射",
    }
    LEARN_NO_MAPPINGS_SIMILAR = {
        "en": "no phonetically-similar error mappings found",
        "zh": "未发现语音相似的错误映射",
    }
    LEARN_FOUND_MAPPINGS = {
        "en": "found {n} phonetically-similar error mappings:",
        "zh": "发现 {n} 条语音相似的错误映射:",
    }
    LEARN_MAPPING_LINE = {
        "en": '  "{wrong}" → "{right}"  (context: {ctx})',
        "zh": '  "{wrong}" → "{right}"  (上下文: {ctx})',
    }
    LEARN_SAVED_STATS = {
        "en": "\nSaved to the term library ({total} entries, {active} active)",
        "zh": "\n已保存到术语库 (共 {total} 条, {active} 条生效)",
    }
    LEARN_EDIT_RESULT = {
        "en": "learned {total} terms ({active} active, {pending} pending)",
        "zh": "已学习 {total} 条术语 ({active} 条生效, {pending} 条待确认)",
    }
    LEARN_SAVED_PAIR = {
        "en": 'saved: "{wrong}" → "{right}"',
        "zh": '已保存: "{wrong}" → "{right}"',
    }
    LEARN_STATS = {
        "en": "term library: {total} entries ({active} active)",
        "zh": "术语库共 {total} 条 ({active} 条生效)",
    }
    LEARN_EMPTY = {
        "en": "term library is empty",
        "zh": "术语库为空",
    }
    LEARN_LIST_HEADER = {
        "en": (
            "term library: {total} entries ({confirmed} confirmed, "
            "{pending} pending, {active} active)"
        ),
        "zh": ("术语库: {total} 条 ({confirmed} 已确认, {pending} 待确认, {active} 生效)"),
    }
    LEARN_TERM_LINE = {
        "en": '  {status} "{wrong}" → "{right}" (×{count}, {pct:.0%})',
        "zh": '  {status} "{wrong}" → "{right}" (×{count}, {pct:.0%})',
    }
    LEARN_TERM_CONTEXT = {
        "en": "     context: {ctx}",
        "zh": "     上下文: {ctx}",
    }
    LEARN_CONFIRMED = {
        "en": 'confirmed: "{wrong}" → "{right}"',
        "zh": '已确认: "{wrong}" → "{right}"',
    }
    LEARN_NOT_FOUND = {
        "en": 'not found: "{wrong}" → "{right}"',
        "zh": '未找到: "{wrong}" → "{right}"',
    }
    LEARN_REMOVED = {
        "en": 'removed: "{wrong}" → "{right}"',
        "zh": '已删除: "{wrong}" → "{right}"',
    }
    LEARN_EXPORTED = {
        "en": "exported to {path}",
        "zh": "已导出到 {path}",
    }
    LEARN_IMPORTED = {
        "en": "imported ({total} entries, {active} active)",
        "zh": "已导入 ({total} 条, {active} 条生效)",
    }
    LEARN_CLEARED = {
        "en": "learning records cleared",
        "zh": "已清空学习记录",
    }

    # ==================== WhisperX progress / Web models ====================
    WX_LOADING_MODEL = {
        "en": "Loading the WhisperX model (cache first)...",
        "zh": "加载 WhisperX 模型（优先取缓存）...",
    }
    WX_ALIGNING = {
        "en": "Running word-level alignment...",
        "zh": "执行 Word-level 对齐...",
    }
    WX_DIARIZING = {
        "en": "Running speaker diarization...",
        "zh": "执行说话人分离...",
    }
    WX_DIARIZE_FAILED = {
        "en": "speaker diarization failed: {e}",
        "zh": "说话人分离失败: {e}",
    }
    WEB_URL_SCHEME_BLOCKED = {
        "en": (
            "URL scheme not allowed: {url}... (only http/https, local file "
            "paths inside the workspace, or platform share text)"
        ),
        "zh": ("URL 协议不被允许: {url}... (仅支持 http/https, 本地文件路径, 或平台短链接文本)"),
    }

    # ==================== Downloaders (wechat_mp / xiaohongshu) ====================
    DL_SIZE_LIMIT_EXCEEDED = {
        "en": (
            "download exceeded the {limit_mb} MiB limit and was aborted "
            "(tune with MEDIASCRIBE_MAX_DOWNLOAD_MB): {url}"
        ),
        "zh": (
            "下载超过 {limit_mb} MiB 上限，已中止（可用 MEDIASCRIBE_MAX_DOWNLOAD_MB 调整）: {url}"
        ),
    }
    WP_NO_URL = {
        "en": "a WeChat MP article URL is required",
        "zh": "需要提供微信公众号文章 URL",
    }
    WP_FOREIGN_HOST = {
        "en": (
            "URL host is not a WeChat MP domain ({host}); refused to send "
            "WeChat cookies to this address"
        ),
        "zh": "URL 主机不是微信公众号域名（{host}），已拒绝携带微信 cookies 请求该地址",
    }
    WP_FETCH_FAILED = {
        "en": "failed to fetch the WeChat MP article HTML",
        "zh": "无法拉取微信公众号文章 HTML",
    }
    WP_NO_CONTENT = {
        "en": (
            "could not extract body text or video from the article; it may "
            "be deleted, blocked, or behind a login wall"
        ),
        "zh": "无法从文章中提取正文或视频。可能文章已删除、被封禁或需登录。",
    }
    WP_DOWNLOAD_MISSING = {
        "en": "download failed: file not found {path}",
        "zh": "下载失败: 找不到文件 {path}",
    }
    XHS_NO_URL = {
        "en": "a Xiaohongshu video URL is required",
        "zh": "需要提供小红书视频 URL",
    }
    XHS_FOREIGN_HOST = {
        "en": "URL host is not a Xiaohongshu domain ({hosts}); refused to process",
        "zh": "URL 主机不是小红书系域名（{hosts}），已拒绝处理",
    }
    XHS_NO_MEDIA = {
        "en": (
            "could not obtain the real media URL of the note. Likely causes: "
            "login required, region-restricted, or an image-only note"
        ),
        "zh": "无法获取小红书视频的真实媒体 URL。可能原因：需要登录、地区受限、或笔记是纯图文。",
    }
    XHS_IMAGE_NOTE = {
        "en": (
            "image-only Xiaohongshu notes are not supported for audio "
            "transcription: the note has no <video> element; submit a video "
            "note instead"
        ),
        "zh": (
            "小红书图文笔记暂不支持音频转写：该笔记不含视频元素，"
            "请改用视频笔记链接（页面内含 <video> 播放器）。"
        ),
    }
    XHS_DOWNLOAD_MISSING = {
        "en": "download failed: file not found {path}",
        "zh": "下载失败: 找不到文件 {path}",
    }

    # ==================== Pipeline progress ====================
    PIPE_DONE = {
        "en": "\n✅ Done!",
        "zh": "\n✅ 完成！",
    }
    PIPE_TRANSCRIPT = {
        "en": "📝 Transcript: {path}",
        "zh": "📝 转录文件: {path}",
    }
    PIPE_METADATA = {
        "en": "📋 Metadata: {path}",
        "zh": "📋 元数据: {path}",
    }
    PIPE_PROCESSING = {
        "en": "\n🎬 Processing: {name}",
        "zh": "\n🎬 处理: {name}",
    }
    PIPE_KIND = {
        "en": "📋 Kind: {kind}",
        "zh": "📋 类型: {kind}",
    }
    PIPE_WECHAT_DOWNLOADING = {
        "en": "📥 Downloading WeChat MP content...",
        "zh": "📥 下载公众号内容...",
    }
    PIPE_WECHAT_EMPTY_BODY = {
        "en": "WeChat MP article body is empty",
        "zh": "公众号文章正文为空",
    }
    PIPE_WECHAT_TEXT_SKIP = {
        "en": "📝 Text-type WeChat MP article: skipping ASR, generating markdown directly",
        "zh": "📝 公众号文本型文章：跳过 ASR，直接生成 markdown",
    }
    PIPE_EXTRACTING_AUDIO = {
        "en": "🔊 Extracting audio...",
        "zh": "🔊 提取音频...",
    }
    PIPE_EXTRACT_FAILED = {
        "en": "audio extraction failed",
        "zh": "音频提取失败",
    }
    PIPE_TRANSCRIBING = {
        "en": "🎤 Starting transcription...",
        "zh": "🎤 开始转录...",
    }
    PIPE_TRANSCRIBE_EMPTY = {
        "en": "transcription result is empty",
        "zh": "转录结果为空",
    }
    WX_NOT_INSTALLED = {
        "en": "WhisperX is not installed; run: pip install whisperx",
        "zh": "WhisperX 未安装，请运行: pip install whisperx",
    }


# Global i18n state
_current_lang: str = "en"


def set_language(lang: str):
    """Set the current language"""
    global _current_lang
    if lang not in ("en", "zh"):
        raise ValueError(f"Unsupported language: {lang}")
    _current_lang = lang


def get_language() -> str:
    """Get the current language"""
    return _current_lang


def init_language(lang: Optional[str] = None):
    """Initialize language"""
    global _current_lang
    if lang:
        set_language(lang)
    else:
        _current_lang = detect_language()


def t(key: str, **kwargs) -> str:
    """
    Translate a message key.

    Args:
        key: Message key in Messages class
        **kwargs: Format arguments

    Returns:
        Translated string
    """
    msg_dict = getattr(Messages, key, None)
    if msg_dict is None:
        # Fallback: return the key
        return f"[{key}]"

    msg: str = msg_dict.get(_current_lang, msg_dict.get("en", f"[{key}]"))
    if kwargs:
        try:
            return msg.format(**kwargs)
        except KeyError:
            return msg
    return msg
