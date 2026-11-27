"""环境变量单一真源清单（R-4，2026-11-21 架构评审）。

``MEDIASCRIBE_*`` 面随修复批次 organic 增长到 ~50 个,散在 config / web /
batch 三处且无一处汇总 —— 本模块把**名字、双语说明、是否敏感**收敛为
单一注册表,三个消费方:

* ``doctor`` 的环境变量汇总段（值对敏感变量打码）;
* ``docs/configuration.md``（与注册表的一致性由 ``tests/test_env_registry.py``
  钉死: 源码 grep 到的每个变量名必须在表内,表内每个正名必须在文档里）;
* 前缀收敛: 批处理线主前缀为 ``MEDIASCRIBE_BATCH_*``,历史品牌前缀
  ``DOUYIN_BATCH_*`` 与更早的拼写错误 ``DOYIN_BATCH_*`` 作为弃用别名
  （见 :data:`LEGACY_PREFIXES`，读取方 ``batch_config.from_env``）。

说明文案双语存放于本表（与 ``i18n.Messages`` 同模式），走 :func:`describe`
按当前语言取值 —— AST i18n 门禁只拦 print/raise 中的 CJK 字面量,
表内数据字典不在其列。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterator, Optional

__all__ = [
    "ENV_VARS",
    "LEGACY_PREFIXES",
    "BATCH_SUFFIXES",
    "iter_all_names",
    "describe",
    "current_value",
    "canonical_name",
]

Lang = Dict[str, str]
Entry = Dict[str, Any]


def _e(en: str, zh: str, *, secret: bool = False) -> Entry:
    return {"desc": {"en": en, "zh": zh}, "secret": secret}


ENV_VARS: Dict[str, Entry] = {
    # ---- 核心转录 ----
    "MEDIASCRIBE_WORKSPACE": _e(
        "Workspace root dir (default ./output)", "工作区根目录（默认 ./output）"
    ),
    "MEDIASCRIBE_TRANSCRIPT_ROOT": _e("Transcript output root override", "转录稿输出根目录覆盖"),
    "MEDIASCRIBE_DEVICE": _e("Force device: cuda / cpu / auto", "强制设备：cuda / cpu / auto"),
    "MEDIASCRIBE_LANG": _e("UI language: en / zh", "界面语言：en / zh"),
    "MEDIASCRIBE_LOG_LEVEL": _e("Logging level (default INFO)", "日志级别（默认 INFO）"),
    "MEDIASCRIBE_AUDIO_ONLY": _e(
        "Prefer audio-only stream for downloads (default on)", "下载优先纯音轨（默认开）"
    ),
    "MEDIASCRIBE_MAX_DOWNLOAD_MB": _e("Max single-file download size (MB)", "单文件下载上限（MB）"),
    "MEDIASCRIBE_FFMPEG_TIMEOUT": _e(
        "ffmpeg subprocess timeout (seconds)", "ffmpeg 子进程超时（秒）"
    ),
    "MEDIASCRIBE_ZH_SIMPLIFY": _e("Normalize traditional → simplified Chinese", "繁体归一为简体"),
    "MEDIASCRIBE_CLEANUP_MEDIA": _e("Delete media after transcription", "转录后删除媒体文件"),
    "MEDIASCRIBE_CLEANUP_FORCE": _e("Bypass the reversible-cleanup gate", "绕过可逆清理门控"),
    "MEDIASCRIBE_CACHE_DIR": _e(
        "Persistent download/chunk cache root (not ASR weights)",
        "持久下载/分块缓存根目录（不含 ASR 模型权重）",
    ),
    "MEDIASCRIBE_DOWNLOAD_CACHE": _e(
        "Enable persistent download cache (0/1)", "启用持久下载缓存（0/1）"
    ),
    "MEDIASCRIBE_CUSTOM_TERMS": _e("Custom terms file for post-processing", "后处理自定义术语表"),
    "MEDIASCRIBE_LEARNED_TERMS": _e(
        "Learned-terms library path (learn.py)", "learn.py 学习术语库路径"
    ),
    "MEDIASCRIBE_APP_DATA": _e(
        "Override user-level application data directory", "覆盖用户级应用数据目录"
    ),
    "MEDIASCRIBE_EVENT_DB": _e("Override local-only event database path", "覆盖本地事件数据库路径"),
    "MEDIASCRIBE_EVENT_RETENTION_DAYS": _e(
        "Local event retention days (default 90; 0 disables and clears)",
        "本地事件保留天数（默认 90；0 表示停止保留并清空）",
    ),
    "MEDIASCRIBE_INTERNAL_TEST": _e(
        "Mark local recordings as internal tests and skip automatic first-use events",
        "将本地记录标记为内部测试并跳过自动首用事件",
    ),
    # ---- GPU / 资源 ----
    "MEDIASCRIBE_VRAM_BUDGET_MB": _e("Override GPU VRAM budget (MB)", "覆盖显存预算（MB）"),
    "MEDIASCRIBE_VRAM_PER_TASK_MB": _e("Estimated VRAM per task (MB)", "单任务预估显存（MB）"),
    "MEDIASCRIBE_BATCH_CONCURRENCY": _e(
        "Concurrency within one batch (legacy alias MEDIASCRIBE_MAX_WORKERS)",
        "单批并发上限（旧名 MEDIASCRIBE_MAX_WORKERS）",
    ),
    "MEDIASCRIBE_MAX_WORKERS": _e(
        "DEPRECATED alias of MEDIASCRIBE_BATCH_CONCURRENCY",
        "已弃用——MEDIASCRIBE_BATCH_CONCURRENCY 的别名",
    ),
    "MEDIASCRIBE_JOB_EXECUTORS": _e("Concurrent batch jobs in the Web executor", "Web 并发批次数"),
    "MEDIASCRIBE_MIN_FREE_MB": _e(
        "Min free disk on workspace volume (507 guard)", "工作区磁盘最低剩余（MB，507 守卫）"
    ),
    "MEDIASCRIBE_HOST_MIN_INTERVAL_S": _e(
        "Per-host download throttle interval (s)", "同主机下载节流间隔（秒）"
    ),
    # ---- 批处理（MEDIASCRIBE_BATCH_*，legacy 见 LEGACY_PREFIXES）----
    "MEDIASCRIBE_BATCH_MAX_VIDEOS": _e("Batch: max videos per run", "批处理：单次最多视频数"),
    "MEDIASCRIBE_BATCH_WORKERS": _e("Batch: download/OCR workers", "批处理：下载/OCR 并发数"),
    "MEDIASCRIBE_BATCH_LANGUAGE": _e("Batch: transcription language", "批处理：转录语言"),
    "MEDIASCRIBE_BATCH_MODEL": _e("Batch: whisper model size", "批处理：模型档位"),
    "MEDIASCRIBE_BATCH_ENGINE": _e(
        "Batch: ASR engine (default = install probe)", "批处理：ASR 引擎（默认安装探测）"
    ),
    "MEDIASCRIBE_BATCH_ALLOW_MODEL_DOWNLOAD": _e(
        "Batch: allow downloading missing model weights (0/1)",
        "批处理：允许下载缺失模型权重（0/1）",
    ),
    "MEDIASCRIBE_BATCH_OUTPUT_DIR": _e("Batch: output directory", "批处理：输出目录"),
    "MEDIASCRIBE_BATCH_KEEP_AUDIO": _e(
        "Batch: keep downloaded media (0/1)", "批处理：保留媒体文件（0/1）"
    ),
    "MEDIASCRIBE_BATCH_LOG_LEVEL": _e("Batch: logging level", "批处理：日志级别"),
    "MEDIASCRIBE_BATCH_MAX_RETRIES": _e("Batch: download retries", "批处理：下载重试次数"),
    "MEDIASCRIBE_BATCH_LIST_TTL_HOURS": _e(
        "Batch: creator-list cache TTL hours (0 = legacy no-TTL)",
        "批处理：作者列表缓存 TTL（小时，0=旧行为）",
    ),
    "MEDIASCRIBE_BATCH_HEADLESS": _e(
        "Batch: run browser headless (0/1)", "批处理：无头浏览器（0/1）"
    ),
    "MEDIASCRIBE_BATCH_TIMEOUT": _e(
        "MCP batch subprocess timeout (seconds)", "MCP batch 子进程超时（秒）"
    ),
    # ---- Web ----
    "MEDIASCRIBE_API_TOKEN": _e(
        "Bearer token for every Web API request", "Web API 全端点 Bearer 令牌", secret=True
    ),
    "MEDIASCRIBE_ALLOW_UNSAFE_BIND": _e(
        "Allow non-loopback bind without token (self-risk)",
        "允许无 token 绑定非回环地址（自担风险）",
    ),
    "MEDIASCRIBE_CORS_ORIGINS": _e(
        "Comma-separated CORS allow-list override", "CORS 白名单（逗号分隔覆盖）"
    ),
    "MEDIASCRIBE_RATE_LIMIT": _e("Web requests per rate-limit window", "限流窗口内请求数"),
    "MEDIASCRIBE_RATE_LIMIT_WINDOW": _e("Rate-limit window (seconds)", "限流窗口（秒）"),
    "MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS": _e(
        "WebSocket idle timeout (s)", "WebSocket 空闲超时（秒）"
    ),
    "MEDIASCRIBE_WS_MAX_SESSION_SECONDS": _e(
        "WebSocket max session length (s)", "WebSocket 会话上限（秒）"
    ),
    "MEDIASCRIBE_PUBLIC_BASE_URL": _e(
        "Public origin for INSTALL.md / extension", "对外公开 origin（INSTALL.md/扩展用）"
    ),
    "MEDIASCRIBE_ALLOWED_HOSTS": _e("Host allow-list for the Web API", "Web API 主机白名单"),
    "MEDIASCRIBE_VAULT_DIR": _e("Wiki vault directory override", "wiki vault 目录覆盖"),
    "MEDIASCRIBE_WIKI": _e("Enable the wiki vault layer (0/1)", "启用 wiki vault 层（0/1）"),
    # ---- MCP ----
    "MEDIASCRIBE_MCP_TOKEN": _e("MCP server auth token", "MCP 服务鉴权令牌", secret=True),
    "MEDIASCRIBE_MCP_ALLOWED_HOSTS": _e("Host allow-list for the MCP server", "MCP 主机白名单"),
    # ---- LLM（opt-in，外呼）----
    "MEDIASCRIBE_LLM_ENABLED": _e(
        "Enable LLM post-processing (off by default; sends text to your API)",
        "启用 LLM 后处理（默认关；文本发往你配置的 API）",
    ),
    "MEDIASCRIBE_LLM_API_KEY": _e(
        "LLM API key (required when enabled)", "LLM API key（启用时必填）", secret=True
    ),
    "MEDIASCRIBE_LLM_API_BASE": _e(
        "OpenAI-compatible endpoint (required when enabled)", "OpenAI 兼容端点（启用时必填）"
    ),
    "MEDIASCRIBE_LLM_MODEL": _e(
        "LLM model name (default deepseek-chat)", "LLM 模型名（默认 deepseek-chat）"
    ),
    "MEDIASCRIBE_LLM_TIMEOUT": _e("LLM request timeout (seconds)", "LLM 请求超时（秒）"),
    "MEDIASCRIBE_LLM_MAX_CHARS": _e(
        "Max chars sent to the LLM per request", "单次发往 LLM 的最大字符数"
    ),
    # ---- 平台凭据 ----
    "MEDIASCRIBE_WECHAT_COOKIE": _e(
        "WeChat MP cookies (env form)", "微信公众号 cookies（env 形式）", secret=True
    ),
    "MEDIASCRIBE_WHISPER_CPP_BIN": _e("whisper.cpp binary path", "whisper.cpp 可执行文件路径"),
    "MEDIASCRIBE_WHISPER_CPP_MODELS": _e("whisper.cpp models directory", "whisper.cpp 模型目录"),
}

#: 批处理后缀表（与 batch_config._ENV_FIELDS 一致；一致性由测试钉住）。
BATCH_SUFFIXES = (
    "HEADLESS",
    "MAX_VIDEOS",
    "WORKERS",
    "LANGUAGE",
    "MODEL",
    "ENGINE",
    "OUTPUT_DIR",
    "KEEP_AUDIO",
    "LOG_LEVEL",
    "MAX_RETRIES",
    "LIST_TTL_HOURS",
)

#: 历史前缀（按弃用顺序）：旧品牌 DOUYIN_BATCH_* 与更早的拼写错误 DOYIN_BATCH_*。
LEGACY_PREFIXES = ("DOUYIN_BATCH_", "DOYIN_BATCH_")


def canonical_name(name: str) -> str:
    """Legacy 名 → 正名（``DOUYIN_BATCH_<suffix>`` → ``MEDIASCRIBE_BATCH_<suffix>``）；其余原样。"""
    for prefix in LEGACY_PREFIXES:
        if name.startswith(prefix):
            return "MEDIASCRIBE_BATCH_" + name[len(prefix) :]
    return name


def iter_all_names() -> Iterator[str]:
    """正名 + legacy 展开名的全量迭代（doctor 汇总 / 一致性测试用）。"""
    yield from ENV_VARS
    for prefix in LEGACY_PREFIXES:
        for suffix in BATCH_SUFFIXES:
            yield prefix + suffix


def describe(name: str, lang: Optional[str] = None) -> str:
    """取变量说明（双语；legacy 名回落到对应正名的说明）。"""
    from .i18n import _current_lang, _lang_override  # noqa: PLC0415  # 避免顶层环

    if lang is None:
        lang = _lang_override.get() or _current_lang
    entry = ENV_VARS.get(canonical_name(name))
    if entry is None:
        return ""
    desc: Dict[str, str] = entry["desc"]
    return str(desc.get(lang) or desc["en"])


def current_value(name: str) -> str:
    """当前生效值；敏感变量只报 set/unset，不回显内容。"""
    raw = os.environ.get(name, "")
    if not raw.strip():
        return "(unset)"
    if ENV_VARS.get(canonical_name(name), {}).get("secret"):
        return "(set, masked)"
    return raw
