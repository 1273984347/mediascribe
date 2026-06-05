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
    env_lang = os.environ.get("VIDEO2TEXT_LANG") or os.environ.get("LANG")
    if env_lang:
        env_lang = env_lang.lower()
        if env_lang.startswith("zh"):
            return "zh"
        if env_lang.startswith("en"):
            return "en"

    # 2. Check system locale
    try:
        sys_locale = locale.getdefaultlocale()[0]
        if sys_locale and sys_locale.lower().startswith("zh"):
            return "zh"
    except Exception:
        pass

    # 3. Default to English
    return "en"


# Language registry
class Messages:
    """Bilingual messages registry"""

    # ==================== Header / 标题 ====================
    HEADER_TITLE = {
        "en": "Video2Text - Batch Transcription",
        "zh": "Video2Text - 批量转录",
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
  python douyin_batch_v3.py --from-video "https://v.douyin.com/xxxxx/" -n 10

  # Provide a creator profile URL directly
  python douyin_batch_v3.py --user "https://www.douyin.com/user/MS4wLjABAAAAxxxx" -n 20

  # Use a config file
  python douyin_batch_v3.py --config my_config.json

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
  python douyin_batch_v3.py --from-video "https://v.douyin.com/xxxxx/" -n 10

  # 直接提供作者主页
  python douyin_batch_v3.py --user "https://www.douyin.com/user/MS4wLjABAAAAxxxx" -n 20

  # 使用配置文件
  python douyin_batch_v3.py --config my_config.json

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
        "en": "Video2Text - Douyin Batch Transcription",
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

    msg = msg_dict.get(_current_lang, msg_dict.get("en", f"[{key}]"))
    if kwargs:
        try:
            return msg.format(**kwargs)
        except KeyError:
            return msg
    return msg
