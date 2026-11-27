<div align="center">
  <img src="docs/assets/banner-c1-video-to-text.jpg" alt="MediaScribe — local-ASR video transcription / 本地 ASR 视频转文字" width="100%" />
</div>

# MediaScribe

> **Local-ASR video transcription tool** - Convert videos to text using local AI models
> **本地 ASR 视频转文字工具** - 使用本地 AI 模型将视频转换为文字

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://github.com/1273984347/mediascribe/actions/workflows/test.yml/badge.svg)](https://github.com/1273984347/mediascribe/actions/workflows/test.yml)
[![Coverage](https://img.shields.io/badge/Coverage-75%25-green.svg)](#testing)
[![Platforms](https://img.shields.io/badge/Platforms-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#-cross-platform)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![i18n: EN / 中文](https://img.shields.io/badge/i18n-EN%20%2F%20中文-blue.svg)](#i18n)

[English](#english) | [中文](#中文)

> 🤖 **Built for AI coding agents** — MCP server, stable JSON output mode
> (schema `mediascribe.agent-output/v1`), and a Copilot instructions file.
> See [🤖 AI Agent Compatibility](#-ai-agent-compatibility).

---

## English

**MediaScribe** is a powerful local-ASR video transcription tool that downloads videos from multiple platforms (Bilibili, Douyin, YouTube, Xiaohongshu, WeChat MP) and transcribes them using local AI models (Whisper, faster-whisper, WhisperX, whisper.cpp). ASR inference runs on your machine by default. URL downloads and model-weight acquisition require network access; local files with pre-provisioned weights can run fully offline. Optional LLM post-processing is an explicit opt-in, off by default, and sends truncated transcript text plus the title and source URL to the OpenAI-compatible API you configure.

### ✨ Key Features

- 🎬 **Multi-Platform Support** - Bilibili, Douyin, YouTube, Xiaohongshu, WeChat MP, local files
- 🌍 **Local ASR by Default** - Inference stays on your machine; URL downloads and model-weight acquisition need network access (LLM post-processing is an explicit opt-in, off by default)
- 🚀 **No Cookies Required** - Smart URL extraction bypasses authentication
- 🤖 **Multiple AI Engines** - faster-whisper + small by default; whisper compatibility fallback with a notice; WhisperX is opt-in
- 📝 **Markdown Output** - Structured transcripts with metadata
- 🔄 **Batch Processing** - Download entire creator's content
- 🛡️ **Production Ready** - Logging, error handling, resume support
- 🎙️ **Optional VAD chunking** (Python API) — `[vad]` enables `webrtcvad` boundaries only when a caller requests them; normal `transcribe` is unchanged
- 💾 **Opt-in download cache** — persistent cross-run download caching is off by default; `MEDIASCRIBE_CACHE_DIR` sets the download/chunk cache root
- 📊 **`profile` CLI** (v3.2.0a) — `python -m mediascribe profile <run.jsonl>` to Markdown / JSON
- 🧠 **ASR 自动学习** (v3.2.0b) — `python -m mediascribe learn` 从用户校对累积术语库,越用越准
- 📡 **Six-stage WebSocket progress** (v3.4.0) — preflight / model prepare / download / extract audio / transcribe / assemble; stage-level, not measured intra-stage percentages

### 🚀 Quick Start

#### Installation

```bash
# PyPI minimal install (default ASR engine)
pip install "mediascribe[faster-whisper]"

# Web UI / MCP minimal installs also include faster-whisper
pip install "mediascribe[web,faster-whisper]"
pip install "mediascribe[mcp,faster-whisper]"

# Source install
git clone https://github.com/1273984347/mediascribe.git
cd mediascribe
pip install -e ".[faster-whisper]"

# Full source/development shim (not a pure base-dependency list):
# expands to -e .[web,faster-whisper,whisper,mcp]
pip install -r requirements.txt

# Install Playwright browser (for Douyin/Bilibili)
playwright install chromium

# Optional extras:
pip install "mediascribe[all]"    # ASR engines + OCR + MCP + Web + LLM; excludes [vad]
pip install "mediascribe[vad]"    # Python API VAD chunking only; normal transcribe is unchanged
pip install "mediascribe[ocr]"    # WeChat MP image-article OCR (+~150 packages)
pip install "mediascribe[llm]"    # LLM post-processing (OpenAI-compatible API)

# NVIDIA GPU for the optional OpenAI Whisper torch stack:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
# then run with: python -m mediascribe transcribe <URL> --device cuda
```

Package installation does not download ASR model weights. Normal `transcribe`
and `verify` also leave downloads off unless `--allow-model-download` is passed.

#### Single Video Transcription

```bash
# Online first use: explicitly authorize missing model weights
python -m mediascribe transcribe "https://www.bilibili.com/video/BV1Nd596vEyU" \
  --allow-model-download

# From local file (weights must already be provisioned unless download is allowed)
python -m mediascribe transcribe "video.mp4" --language zh

# Offline path: pre-provision weights, verify readiness, then transcribe locally
python -m mediascribe doctor --offline
python -m mediascribe transcribe "video.mp4" --language zh

# 指定模型 / 设备（默认 faster-whisper + small；large-v3 是模型，不是引擎）
python -m mediascribe transcribe "video.mp4" --model large-v3 --device cuda

# 模型按音频时长自动推荐（粤语 -l yue 恒选 large-v3）
python -m mediascribe transcribe "video.mp4" --model auto

# 默认按 ASR 语音边界分段；--timestamps 额外给每段加 [mm:ss]
python -m mediascribe transcribe "video.mp4" --timestamps

# 双模型交叉校对(生成 <转录稿>.crosscheck.md 复核清单)+ 成功后自动清理中间媒体
# 存疑复核(审校场景): 第三采样重转同一条音频, 对 crosscheck 分歧逐条给出第三方证据
python -m mediascribe verify output/transcripts/<转录稿>.md
# 在线且缺少复核权重时显式授权下载：
python -m mediascribe verify output/transcripts/<转录稿>.md --allow-model-download
python -m mediascribe transcribe "https://..." --cross-check small --cleanup-media

# 常用参数持久化到配置文件（CLI 显式参数优先）
python -m mediascribe --config mediascribe.json transcribe "video.mp4"

# 环境自检（ffmpeg / GPU / 引擎 / 模型缓存 / cookies）
python -m mediascribe doctor
```

> **Output quality (v3.4.0).** Transcripts now use the platform's real
> title as H1, aggregate author/duration/source-URL into a header block,
> and split the body into paragraphs along ASR speech boundaries. Timestamp
> prefixes are off by default; add `--timestamps` for per-paragraph `[mm:ss]`
> markers. Optional LLM punctuation/paragraph polishing is an explicit opt-in:
> it sends truncated transcript text plus the title and source URL to your
> configured API. The newest default artifact path is written to
> `output/LATEST.txt`; depending on the latest run, that may be a single
> transcript or an archive summary report.

#### Batch Transcription (Creator's Archive)

```bash
# Unified entry (v3.4.0)
python -m mediascribe archive --user "https://www.douyin.com/user/xxx" -n 20

# From a single video (auto-finds creator)
python -m mediascribe archive --from-video "https://v.douyin.com/xxxxx/" -n 10

# Legacy script entry still works
mediascribe-batch --user "https://www.douyin.com/user/xxx" -n 20
```

#### ASR Auto-Learning (`learn`)

Accumulate a term-correction glossary from your edits so transcripts get more accurate over time (v3.2.0b).

```bash
# Recommended: diff edited text vs original ASR to extract phonetic-error mappings
python -m mediascribe learn edit --original "ASR原始文本" --corrected "校对后文本"

# Compare a reference transcript against the ASR output
python -m mediascribe learn compare --reference "正确文本" --transcript "ASR文本"

# Manual term management
python -m mediascribe learn save    --wrong "错误词" --right "正确词"
python -m mediascribe learn list
python -m mediascribe learn confirm --wrong "错误词" --right "正确词"
python -m mediascribe learn remove  --wrong "错误词" --right "正确词"
python -m mediascribe learn export  --output terms.json
python -m mediascribe learn import  --input terms.json
python -m mediascribe learn clear
```

### 🏗️ Architecture

```
mediascribe/
├── mediascribe/          # Single product package: pipeline, downloaders, transcribers,
│                         #   config, url_utils, batch_*, i18n, security, web/
├── tests/                # pytest suite
├── docs/                 # Documentation site source (MkDocs)
│   └── assets/           # README banner images (style C, 21:9)
├── extension/            # Browser extension assets (used by mediascribe/web/app.py)
├── examples/plugins/     # Third-party plugin samples
├── scripts/              # Dev scripts by role: bench/ e2e/ pipeline/ tools/ launch/ gates/
└── output/               # Transcription working dir (git-ignored)
    ├── downloads/       # source media, reused across runs (dedup by cache)
    ├── audio/           # extracted audio
    ├── transcripts/     # <base>-<timestamp>.md transcripts
    ├── metadata/        # run metadata (json, one per transcript)
    ├── LATEST.txt       # pointer to newest transcript or archive summary
    └── wiki/            # human-reviewed deliverables (审校稿)
```

> **Transcription output.** Local runs write flat, per-type artifacts under
> `output/`: source media in `downloads/`, extracted audio in `audio/`, the
> transcript in `transcripts/` — named after the source with a timestamp
> (e.g. `douyin_<video_id>-<timestamp>.md`) — and run metadata in `metadata/`.
> `LATEST.txt` points at the newest default artifact, which may be a single
> transcript or an archive summary. Persistent download caching is opt-in and
> off by default. Human-reviewed deliverables (审校稿) live in `output/wiki/`.

### 🧪 Testing

```bash
# Run all unit tests
python scripts/run_tests.py

# Demo all v3 features
python scripts/demo_v3.py
```

### 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details.

### 📄 License

MIT License - see [LICENSE](LICENSE) for details.

---

## 🤖 AI Agent Compatibility

MediaScribe is **built first for AI coding agents**. Every file an agent might
need, every flag an agent might call, and every command an agent might run is
designed to be machine-readable and deterministic.

### Agent entry points

| Agent | File |
|---|---|
| **Any agent** | [`AGENTS.md`](AGENTS.md) — the authoritative repo guide (map, commands, conventions, don'ts) |
| **GitHub Copilot** | [`.github/copilot-instructions.md`](.github/copilot-instructions.md) — a pointer to `AGENTS.md` |

> 2026-10-03 审查: 此前这里列出的 `AGENTS.md` / `CLAUDE.md` /
> `.cursorrules` 等十个文件在仓库中并不存在, 已清除死链。

### MCP (Model Context Protocol) server

Expose MediaScribe to any MCP-aware agent as native tools:

```bash
# Minimal MCP install includes the default ASR engine
pip install "mediascribe[mcp,faster-whisper]"

# Register in your agent's MCP config
# Claude Code / Cursor / Cline:
{
  "mcpServers": {
    "mediascribe": {
      "command": "python",
      "args": ["-m", "mediascribe.mcp_server"]
    }
  }
}
```

Tools exposed: `transcribe_video`, `batch_transcribe_creator`,
`get_cache_stats`, `validate_url`, `sanitize_filename`, `detect_platform`,
`get_transcript`, `wiki_list`, `wiki_read_note`, `transcribe_wechat_mp`. The
server works over **stdio** (default) or **HTTP** (`--transport http --port 8765`)
and uses the official MCP spec (`protocolVersion: 2024-11-05`). The minimal
stdio transport can run without the official SDK; `[mcp]` installs it.

#### MCP tools reference

| Tool | Purpose | Required params | Optional params |
|------|---------|-----------------|-----------------|
| `transcribe_video` | Transcribe a single video URL or local file | `source` | `engine`, `language`, `whisper_model`, `allow_model_download`, `output_dir` |
| `batch_transcribe_creator` | Batch-process a creator's videos | `user_url` or `from_video` | `max_videos`, `language`, `engine`, `allow_model_download`, `output_dir` |
| `transcribe_wechat_mp` | Extract text / OCR / video from a WeChat MP article | `url` | `cookies`, `cookies_file`, `whisper_model`, `engine`, `allow_model_download`, `output_dir`, `ocr_engine`, `ocr_lang`, `save_images` |
| `detect_platform` | Identify platform from URL | `url` | – |
| `validate_url` | Validate a URL is supported | `url` | – |
| `get_transcript` | Read a previously written `.md` transcript | `path` | – |
| `get_cache_stats` | Inspect batch cache counts (total / success / failed / skipped) | – | – |
| `sanitize_filename` | Convert arbitrary string to safe filename | `name` | – |
| `wiki_list` | List local wiki-vault notes and index | – | – |
| `wiki_read_note` | Read one sandboxed wiki note | `name` | – |

### Supported platforms

| Platform | Video | Article / Text | Image / OCR | Cookies | Notes |
|----------|:-----:|:--------------:|:-----------:|:-------:|-------|
| **Bilibili** | ✅ | – | – | optional | BV / av / b23.tv short links |
| **Douyin** | ✅ | – | – | required for some | Real CDN URLs, no cookies by default |
| **YouTube** | ✅ | – | – | – | 4 `player_client` rotations to dodge 403 |
| **Xiaohongshu** | ✅ | – | ✅ image notes | – | Playwright extraction, 4-level fallback |
| **WeChat MP** | ✅ | ✅ `#js_content` | ✅ OCR chain | optional | 3 modes: text / video / image+OCR |
| **TikTok** | ✅ | – | – | – | Routed via `yt-dlp` |

OCR engine fallback chain for WeChat MP image articles:

```
auto  →  paddleocr  →  pytesseract  →  easyocr
                ↓ on ImportError   ↓ on ImportError   ↓ on ImportError
            return None         return None         return None
```

The `transcribe_wechat_mp` MCP tool accepts `ocr_engine` (default `auto`),
`ocr_lang` (default `chi_sim+eng`), and `save_images` (default `false`).
If every engine is missing on the host, the article is still saved with
`wechat_mp_status: partial` and the missing-image slots are marked.

### Bilingual JSON output (`--bilingual-json`)

This flag is supported by `archive` / `mediascribe-batch` only. In addition to
the stable English constants emitted by `--json`, it attaches localised human
labels alongside each constant. The locale is recorded per-field so downstream
agents can detect language without parsing the label value:

```json
{
  "schema": "mediascribe.agent-output/v1",
  "i18n_lang": "zh",
  "videos": [
    {
      "video_id": "v1",
      "platform": "wechat_mp",
      "platform_label": "微信公众号",
      "platform_label_i18n_lang": "zh",
      "status": "success",
      "status_label": "成功",
      "status_label_i18n_lang": "zh",
      "stage": "transcribe",
      "stage_label": "正在转录",
      "stage_label_i18n_lang": "zh"
    }
  ]
}
```

You can also filter the batch to a whitelist of platforms:

```bash
mediascribe-batch --user <URL> --platform youtube --platform wechat_mp --json
# Non-matching videos are returned with status="skipped", stage="platform_filter"
```

### Structured JSON output (`--json`)

`--json` is supported by **`archive` and `mediascribe-batch` only**; it is not a
universal flag for every CLI command. In those two entry points, human-readable
output is silenced and a single `mediascribe.agent-output/v1` document is
emitted to stdout:

```bash
python -m mediascribe archive --user <URL> --json
mediascribe-batch --user <URL> --json
# {
#   "schema": "mediascribe.agent-output/v1",
#   "ok": true,
#   "command": "douyin_batch_v3",
#   "version": "3.3.0",
#   "started_at": "2026-06-04T00:00:00Z",
#   "elapsed_seconds": 42.3,
#   "config": { ... },
#   "user_url": "https://...",
#   "videos": [
#     { "video_id": "v1", "status": "success", "transcript": "..." },
#     ...
#   ],
#   "stats": { "total": 10, "success": 8, "failed": 2 },
#   "errors": []
# }
```

The schema is **stable v1** and documented in
[`mediascribe/agent_output.py`](mediascribe/agent_output.py).

### Local events and metrics

Privacy-whitelisted lifecycle events stay in a local SQLite database and are
retained for **90 days** by default:

```bash
python -m mediascribe events list
python -m mediascribe events export --output output/events.json
python -m mediascribe events metrics
python -m mediascribe events clear
```

`clear` removes history but preserves the install identity and first-run marker.
Metrics count distinct installs: submission/success rates divide by new
installations, open rate by successful installs, and recovery success rate by
installs that selected a recovery action. Internal-test events are excluded;
zero denominators produce `null`.

### Agent-friendly task runner

```bash
make help        # list all targets
make install     # source/dev shim: pip install -r requirements.txt
make test        # python scripts/run_tests.py
make lint        # ruff check + ruff format --check
make format      # ruff check --fix + ruff format
make verify      # syntax + imports + tests
```

On Windows without `make`, the same commands work as plain
`python -m pip install -r requirements.txt` etc.; that requirements file is the
source/development shim, not a pure base install (see the
[`Makefile`](Makefile) header comments).

### Full test surface, running in ~2 min (see the live CI badge above for the current count):

```bash
python scripts/run_tests.py
# config / cache / retry / progress / URL parsing / pipeline stages / engines /
# agent-output JSON schema / MCP server / Web UI / security / cross-platform …
```

---

## 🌐 i18n / 国际化

The CLI and runtime messages are bilingual. Use `--lang en` or `--lang zh`, or let the tool auto-detect from your locale:

```bash
# Auto-detect (default)
mediascribe-batch --user "..."

# Force English
mediascribe-batch --lang en --user "..."

# Force Chinese
mediascribe-batch --lang zh --user "..."
```

You can also set the environment variable `MEDIASCRIBE_LANG=en` (or `zh`).

To add a new language, edit [`mediascribe/i18n.py`](mediascribe/i18n.py) — every message is a `{ "en": ..., "zh": ... }` dictionary. Adding `"ja": "..."` and exposing a `"ja"` choice in [`mediascribe/batch_cli.py`](mediascribe/batch_cli.py) is all that's needed.

---

## 🖥️ Cross-Platform / 跨平台

Tested on **Windows 10/11**, **macOS 12+**, and **Ubuntu 20.04+**:

| OS | Status | Notes |
|---|---|---|
| Windows | ✅ | Requires FFmpeg in PATH or `C:\ffmpeg\bin` |
| macOS (Intel / Apple Silicon) | ✅ | `brew install ffmpeg` |
| Linux (Ubuntu / Debian / Fedora / Arch) | ✅ | `apt install ffmpeg` / `dnf install ffmpeg` / `pacman -S ffmpeg` |

The library uses `pathlib` everywhere, normalises `~` and env-vars in user paths, and replaces characters that are invalid on Windows (`<>:"/\\|?*`) and reserved names (`CON`, `PRN`, ...).

---

## 中文

**MediaScribe** 是一个强大的本地 ASR 视频转文字工具，可以从多个平台（B站、抖音、YouTube、小红书、微信公众号）下载视频，并使用本地 AI 模型（Whisper、faster-whisper、WhisperX、whisper.cpp）进行转录。**ASR 推理默认在本机执行**；URL 下载和模型权重获取需要网络，本地文件配合预置权重可完全离线运行。可选 LLM 后处理为显式 opt-in、默认关闭，启用后会把截断的转录文本及标题、来源 URL 发往您配置的 OpenAI 兼容 API。

### ✨ 核心特性

- 🎬 **多平台支持** - B站、抖音、YouTube、小红书、微信公众号、本地文件
- 🌍 **ASR 默认本机** - 推理不上传；URL 下载和模型权重获取需要网络（LLM 后处理为显式 opt-in，默认关闭）
- 🚀 **无需 Cookies** - 智能 URL 提取绕过认证
- 🤖 **多种 AI 引擎** - 默认 faster-whisper + small；未安装时回退 whisper 并提示；WhisperX 需显式选择
- 📝 **Markdown 输出** - 结构化转录文档
- 🔄 **批量处理** - 批量下载作者全部往期内容
- 🛡️ **生产就绪** - 日志、错误处理、断点续传
- 🎙️ **可选 VAD 分块** - 仅 Python API；安装 `[vad]` 不会改变普通 `transcribe`
- 💾 **下载缓存默认关闭** - 显式开启后跨 run 复用；`MEDIASCRIBE_CACHE_DIR` 仅是下载/分块缓存根
- 📡 **六阶段 WebSocket 进度** - preflight / model prepare / download / extract audio / transcribe / assemble；阶段级进度，不是阶段内实测百分比
- 🧠 **ASR 自动学习** - `python -m mediascribe learn` 从用户校对累积术语库,越用越准

### 🚀 快速开始

#### 安装

```bash
# PyPI 最小安装（含默认 ASR 引擎）
pip install "mediascribe[faster-whisper]"

# 源码安装
git clone https://github.com/1273984347/mediascribe.git
cd mediascribe
pip install -e ".[faster-whisper]"

# 完整源码/开发安装 shim（不是纯基础依赖）：
# 展开为 -e .[web,faster-whisper,whisper,mcp]
pip install -r requirements.txt

# Web / MCP 最小安装同样包含 faster-whisper
pip install "mediascribe[web,faster-whisper]"
pip install "mediascribe[mcp,faster-whisper]"

# 安装 Playwright 浏览器（用于抖音/B站）
playwright install chromium
```

安装包不会自动下载 ASR 模型权重；普通 `transcribe` / `verify` 也只会在显式传入
`--allow-model-download` 时下载缺失权重。`[all]` 包含 ASR、OCR、MCP、Web、LLM，
但不包含 `[vad]`。

#### 单个视频转录

```bash
# 在线首次使用：显式授权下载缺失模型权重
python -m mediascribe transcribe "https://www.bilibili.com/video/BV1Nd596vEyU" \
  --allow-model-download

# 从本地文件（需已预置权重，或显式允许下载）
python -m mediascribe transcribe "video.mp4" --language zh

# 离线路径：先预置权重，再校验并就绪后转录
python -m mediascribe doctor --offline
python -m mediascribe transcribe "video.mp4" --language zh

# 指定模型 / 设备（默认 faster-whisper + small；large-v3 是模型，不是引擎）
python -m mediascribe transcribe "video.mp4" --model large-v3 --device cuda

# 模型按音频时长自动推荐（粤语 -l yue 恒选 large-v3）
python -m mediascribe transcribe "video.mp4" --model auto

# 默认按 ASR 语音边界分段；--timestamps 额外给每段加 [mm:ss]
python -m mediascribe transcribe "video.mp4" --timestamps

# 双模型交叉校对(生成 .crosscheck.md 复核清单)+ 成功后自动清理中间媒体
# 存疑复核(审校场景): 第三采样重转同一条音频, 对 crosscheck 分歧逐条给出第三方证据
python -m mediascribe verify output/transcripts/<转录稿>.md
# 在线且缺少复核权重时显式授权下载：
python -m mediascribe verify output/transcripts/<转录稿>.md --allow-model-download
python -m mediascribe transcribe "https://..." --cross-check small --cleanup-media

# 常用参数持久化到配置文件（CLI 显式参数优先）
python -m mediascribe --config mediascribe.json transcribe "video.mp4"

# 环境自检（ffmpeg / GPU / 引擎 / 模型缓存 / cookies）
python -m mediascribe doctor
```

> **输出质量（v3.4.0）。** 转录稿 H1 使用平台真实标题，「基本信息」
> 聚合作者/时长/来源链接，正文沿 ASR 语音停顿自动分段。默认不带时间戳；
> `--timestamps` 才给每段加 `[mm:ss]` 标记。LLM 标点/分段润色为显式
> opt-in，启用后会把截断转录文本及标题、来源 URL 发往配置的 API。
> 最新默认产物路径写入 `output/LATEST.txt`，可能是单份转录稿，也可能是
> archive 汇总报告。

#### 批量转录（作者往期内容）

```bash
# 统一入口（v3.4.0）
python -m mediascribe archive --user "https://www.douyin.com/user/xxx" -n 20

# 从单个视频（自动找到作者）
python -m mediascribe archive --from-video "https://v.douyin.com/xxxxx/" -n 10

# 旧脚本入口仍然可用
mediascribe-batch --user "https://www.douyin.com/user/xxx" -n 20
```

#### 本地事件与指标

事件默认仅写入本地 SQLite，并保留 **90 天**：

```bash
python -m mediascribe events list
python -m mediascribe events export --output output/events.json
python -m mediascribe events metrics
python -m mediascribe events clear
```

`clear` 清空历史但保留安装身份与首次运行标记。指标按安装 ID 去重：提交率/
成功率的分母是新安装数，打开率的分母是成功安装数，恢复成功率的分母是选择过
恢复动作的安装数；内部测试事件不计入，分母为零时返回 `null`。

#### ASR 自动学习（learn）

根据用户校对累积术语库，越用越准（v3.2.0b）。

```bash
# 推荐：对比编辑前后文本，自动提取语音相似的错误映射
python -m mediascribe learn edit --original "ASR原始文本" --corrected "校对后文本"

# 对比参考文本与转录
python -m mediascribe learn compare --reference "正确文本" --transcript "ASR文本"

# 手动管理术语
python -m mediascribe learn save    --wrong "错误词" --right "正确词"
python -m mediascribe learn list
python -m mediascribe learn confirm --wrong "错误词" --right "正确词"
python -m mediascribe learn remove  --wrong "错误词" --right "正确词"
python -m mediascribe learn export  --output terms.json
python -m mediascribe learn import  --input terms.json
python -m mediascribe learn clear
```

### 🧪 测试

```bash
# 运行所有单元测试
python scripts/run_tests.py

# 演示 v3 所有功能
python scripts/demo_v3.py
```

### 🤝 贡献代码

欢迎贡献！详情请见 [CONTRIBUTING.md](CONTRIBUTING.md)。

### 📄 许可证

MIT 许可证 - 详情见 [LICENSE](LICENSE)。

---

## 🌐 国际化（i18n）

命令行与运行时消息均为中英双语。可通过 `--lang en` 或 `--lang zh` 显式指定，也支持根据系统语言自动识别：

```bash
# 自动检测（默认）
mediascribe-batch --user "..."

# 强制英文
mediascribe-batch --lang en --user "..."

# 强制中文
mediascribe-batch --lang zh --user "..."
```

也可通过环境变量 `MEDIASCRIBE_LANG=en`（或 `zh`）配置。

如需新增语言，只需编辑 [`mediascribe/i18n.py`](mediascribe/i18n.py) —— 每条消息均为 `{ "en": ..., "zh": ... }` 字典，再在 [`mediascribe/batch_cli.py`](mediascribe/batch_cli.py) 中扩展 `--lang` 的可选值即可。

---

## 🖥️ 跨平台

已在 **Windows 10/11**、**macOS 12+**、**Ubuntu 20.04+** 上测试通过：

| 操作系统 | 状态 | 备注 |
|---|---|---|
| Windows | ✅ | 需将 FFmpeg 加入 PATH 或安装至 `C:\ffmpeg\bin` |
| macOS（Intel / Apple Silicon） | ✅ | `brew install ffmpeg` |
| Linux（Ubuntu / Debian / Fedora / Arch） | ✅ | `apt / dnf / pacman` 安装 FFmpeg |

库内统一使用 `pathlib`，自动展开 `~` 和环境变量；并对 Windows 非法字符（`<>:"/\\|?*`）与保留名（`CON`、`PRN` 等）做替换处理。

---

## 🙏 Acknowledgments / 致谢

This project integrates and is inspired by:
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - Video downloader
- [bili2text](https://github.com/lanbinleo/bili2text) - Bilibili transcription
- [WhisperX](https://github.com/m-bain/whisperX) - Fast Whisper with alignment
- [whisper.cpp](https://github.com/ggerganov/whisper.cpp) - C++ Whisper implementation
- [OpenAI Whisper](https://github.com/openai/whisper) - Original Whisper
