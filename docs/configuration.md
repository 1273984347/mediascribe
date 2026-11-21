# Configuration (environment variables)

Every `MEDIASCRIBE_*` variable in one table — the single source of truth
is `mediascribe/env_registry.py`; `python -m mediascribe doctor` prints
the currently-set values from the same registry (secrets are masked).
A consistency test (`tests/test_env_registry.py`) fails if code, registry
and this page drift apart.

Batch variables migrated to the `MEDIASCRIBE_BATCH_*` prefix (2026-11-21).
The legacy `DOUYIN_BATCH_*` (old brand) and `DOYIN_BATCH_*` (typo) prefixes
still work but log a deprecation warning.

| Variable | Description (EN) | 说明 (zh) |
|---|---|---|
| `MEDIASCRIBE_ALLOWED_HOSTS` | Host allow-list for the Web API | Web API 主机白名单 |
| `MEDIASCRIBE_ALLOW_UNSAFE_BIND` | Allow non-loopback bind without token (self-risk) | 允许无 token 绑定非回环地址（自担风险） |
| `MEDIASCRIBE_API_TOKEN` 🔒 | Bearer token for every Web API request | Web API 全端点 Bearer 令牌 |
| `MEDIASCRIBE_AUDIO_ONLY` | Prefer audio-only stream for downloads (default on) | 下载优先纯音轨（默认开） |
| `MEDIASCRIBE_BATCH_CONCURRENCY` | Concurrency within one batch (legacy alias MEDIASCRIBE_MAX_WORKERS) | 单批并发上限（旧名 MEDIASCRIBE_MAX_WORKERS） |
| `MEDIASCRIBE_BATCH_ENGINE` | Batch: ASR engine (default = install probe) | 批处理：ASR 引擎（默认安装探测） |
| `MEDIASCRIBE_BATCH_HEADLESS` | Batch: run browser headless (0/1) | 批处理：无头浏览器（0/1） |
| `MEDIASCRIBE_BATCH_KEEP_AUDIO` | Batch: keep downloaded media (0/1) | 批处理：保留媒体文件（0/1） |
| `MEDIASCRIBE_BATCH_LANGUAGE` | Batch: transcription language | 批处理：转录语言 |
| `MEDIASCRIBE_BATCH_LIST_TTL_HOURS` | Batch: creator-list cache TTL hours (0 = legacy no-TTL) | 批处理：作者列表缓存 TTL（小时，0=旧行为） |
| `MEDIASCRIBE_BATCH_LOG_LEVEL` | Batch: logging level | 批处理：日志级别 |
| `MEDIASCRIBE_BATCH_MAX_RETRIES` | Batch: download retries | 批处理：下载重试次数 |
| `MEDIASCRIBE_BATCH_MAX_VIDEOS` | Batch: max videos per run | 批处理：单次最多视频数 |
| `MEDIASCRIBE_BATCH_MODEL` | Batch: whisper model size | 批处理：模型档位 |
| `MEDIASCRIBE_BATCH_OUTPUT_DIR` | Batch: output directory | 批处理：输出目录 |
| `MEDIASCRIBE_BATCH_TIMEOUT` | MCP batch subprocess timeout (seconds) | MCP batch 子进程超时（秒） |
| `MEDIASCRIBE_BATCH_WORKERS` | Batch: download/OCR workers | 批处理：下载/OCR 并发数 |
| `MEDIASCRIBE_CACHE_DIR` | Download/model cache root | 下载/模型缓存根目录 |
| `MEDIASCRIBE_CLEANUP_FORCE` | Bypass the reversible-cleanup gate | 绕过可逆清理门控 |
| `MEDIASCRIBE_CLEANUP_MEDIA` | Delete media after transcription | 转录后删除媒体文件 |
| `MEDIASCRIBE_CORS_ORIGINS` | Comma-separated CORS allow-list override | CORS 白名单（逗号分隔覆盖） |
| `MEDIASCRIBE_CUSTOM_TERMS` | Custom terms file for post-processing | 后处理自定义术语表 |
| `MEDIASCRIBE_DEVICE` | Force device: cuda / cpu / auto | 强制设备：cuda / cpu / auto |
| `MEDIASCRIBE_DOWNLOAD_CACHE` | Enable persistent download cache (0/1) | 启用持久下载缓存（0/1） |
| `MEDIASCRIBE_FFMPEG_TIMEOUT` | ffmpeg subprocess timeout (seconds) | ffmpeg 子进程超时（秒） |
| `MEDIASCRIBE_HOST_MIN_INTERVAL_S` | Per-host download throttle interval (s) | 同主机下载节流间隔（秒） |
| `MEDIASCRIBE_JOB_EXECUTORS` | Concurrent batch jobs in the Web executor | Web 并发批次数 |
| `MEDIASCRIBE_LANG` | UI language: en / zh | 界面语言：en / zh |
| `MEDIASCRIBE_LEARNED_TERMS` | Learned-terms library path (learn.py) | learn.py 学习术语库路径 |
| `MEDIASCRIBE_LLM_API_BASE` | OpenAI-compatible endpoint (required when enabled) | OpenAI 兼容端点（启用时必填） |
| `MEDIASCRIBE_LLM_API_KEY` 🔒 | LLM API key (required when enabled) | LLM API key（启用时必填） |
| `MEDIASCRIBE_LLM_ENABLED` | Enable LLM post-processing (off by default; sends text to your API) | 启用 LLM 后处理（默认关；文本发往你配置的 API） |
| `MEDIASCRIBE_LLM_MAX_CHARS` | Max chars sent to the LLM per request | 单次发往 LLM 的最大字符数 |
| `MEDIASCRIBE_LLM_MODEL` | LLM model name (default deepseek-chat) | LLM 模型名（默认 deepseek-chat） |
| `MEDIASCRIBE_LLM_TIMEOUT` | LLM request timeout (seconds) | LLM 请求超时（秒） |
| `MEDIASCRIBE_LOG_LEVEL` | Logging level (default INFO) | 日志级别（默认 INFO） |
| `MEDIASCRIBE_MAX_DOWNLOAD_MB` | Max single-file download size (MB) | 单文件下载上限（MB） |
| `MEDIASCRIBE_MAX_WORKERS` | DEPRECATED alias of MEDIASCRIBE_BATCH_CONCURRENCY | 已弃用——MEDIASCRIBE_BATCH_CONCURRENCY 的别名 |
| `MEDIASCRIBE_MCP_ALLOWED_HOSTS` | Host allow-list for the MCP server | MCP 主机白名单 |
| `MEDIASCRIBE_MCP_TOKEN` 🔒 | MCP server auth token | MCP 服务鉴权令牌 |
| `MEDIASCRIBE_MIN_FREE_MB` | Min free disk on workspace volume (507 guard) | 工作区磁盘最低剩余（MB，507 守卫） |
| `MEDIASCRIBE_PUBLIC_BASE_URL` | Public origin for INSTALL.md / extension | 对外公开 origin（INSTALL.md/扩展用） |
| `MEDIASCRIBE_RATE_LIMIT` | Web requests per rate-limit window | 限流窗口内请求数 |
| `MEDIASCRIBE_RATE_LIMIT_WINDOW` | Rate-limit window (seconds) | 限流窗口（秒） |
| `MEDIASCRIBE_TRANSCRIPT_ROOT` | Transcript output root override | 转录稿输出根目录覆盖 |
| `MEDIASCRIBE_VAULT_DIR` | Wiki vault directory override | wiki vault 目录覆盖 |
| `MEDIASCRIBE_VRAM_BUDGET_MB` | Override GPU VRAM budget (MB) | 覆盖显存预算（MB） |
| `MEDIASCRIBE_VRAM_PER_TASK_MB` | Estimated VRAM per task (MB) | 单任务预估显存（MB） |
| `MEDIASCRIBE_WECHAT_COOKIE` 🔒 | WeChat MP cookies (env form) | 微信公众号 cookies（env 形式） |
| `MEDIASCRIBE_WHISPER_CPP_BIN` | whisper.cpp binary path | whisper.cpp 可执行文件路径 |
| `MEDIASCRIBE_WHISPER_CPP_MODELS` | whisper.cpp models directory | whisper.cpp 模型目录 |
| `MEDIASCRIBE_WIKI` | Enable the wiki vault layer (0/1) | 启用 wiki vault 层（0/1） |
| `MEDIASCRIBE_WORKSPACE` | Workspace root dir (default ./output) | 工作区根目录（默认 ./output） |
| `MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS` | WebSocket idle timeout (s) | WebSocket 空闲超时（秒） |
| `MEDIASCRIBE_WS_MAX_SESSION_SECONDS` | WebSocket max session length (s) | WebSocket 会话上限（秒） |
| `MEDIASCRIBE_ZH_SIMPLIFY` | Normalize traditional → simplified Chinese | 繁体归一为简体 |

## Legacy aliases

Each batch suffix is also read under the deprecated prefixes (warns on use):

- `DOUYIN_BATCH_HEADLESS`
- `DOUYIN_BATCH_MAX_VIDEOS`
- `DOUYIN_BATCH_WORKERS`
- `DOUYIN_BATCH_LANGUAGE`
- `DOUYIN_BATCH_MODEL`
- `DOUYIN_BATCH_ENGINE`
- `DOUYIN_BATCH_OUTPUT_DIR`
- `DOUYIN_BATCH_KEEP_AUDIO`
- `DOUYIN_BATCH_LOG_LEVEL`
- `DOUYIN_BATCH_MAX_RETRIES`
- `DOUYIN_BATCH_LIST_TTL_HOURS`
- `DOYIN_BATCH_HEADLESS`
- `DOYIN_BATCH_MAX_VIDEOS`
- `DOYIN_BATCH_WORKERS`
- `DOYIN_BATCH_LANGUAGE`
- `DOYIN_BATCH_MODEL`
- `DOYIN_BATCH_ENGINE`
- `DOYIN_BATCH_OUTPUT_DIR`
- `DOYIN_BATCH_KEEP_AUDIO`
- `DOYIN_BATCH_LOG_LEVEL`
- `DOYIN_BATCH_MAX_RETRIES`
- `DOYIN_BATCH_LIST_TTL_HOURS`
