# Video2Text

> **Offline video transcription tool** - Convert videos to text using local AI models
> **离线视频转文字工具** - 使用本地 AI 模型将视频转换为文字

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-612%20passed-brightgreen.svg)](#testing)
[![Coverage](https://img.shields.io/badge/Coverage-75%25-green.svg)](#testing)
[![Platforms](https://img.shields.io/badge/Platforms-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#-cross-platform)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![i18n: EN / 中文](https://img.shields.io/badge/i18n-EN%20%2F%20中文-blue.svg)](#i18n)

[English](#english) | [中文](#中文) | [Español](README_ES.md) | [日本語](README_JA.md)

> 🤖 **Built for AI coding agents** — ships with `AGENTS.md`, MCP server, JSON
> output mode, and platform-specific config files for Claude Code, Cursor,
> Cline, Windsurf, GitHub Copilot, Cody, Continue, Aider, Trae and more. See
> [🤖 AI Agent Compatibility](#-ai-agent-compatibility).

---

## English

**Video2Text** is a powerful offline video transcription tool that downloads videos from multiple platforms (Bilibili, Douyin, YouTube, Xiaohongshu, WeChat MP) and transcribes them using local AI models (Whisper, WhisperX, whisper.cpp). No cloud services, no API keys, no data leaves your machine.

### ✨ Key Features

- 🎬 **Multi-Platform Support** - Bilibili, Douyin, YouTube, Xiaohongshu, WeChat MP, local files
- 🌍 **100% Offline** - Local AI models, no data uploaded
- 🚀 **No Cookies Required** - Smart URL extraction bypasses authentication
- 🤖 **Multiple AI Engines** - Whisper, WhisperX, faster-whisper
- 📝 **Markdown Output** - Structured transcripts with metadata
- 🔄 **Batch Processing** - Download entire creator's content
- 🛡️ **Production Ready** - Logging, error handling, resume support
- 🎙️ **VAD chunking** (v3.2.0a) — `webrtcvad`-driven boundaries for long videos with 5 s overlap
- 💾 **Cross-run cache** (v3.2.0a) — XDG-spec disk cache with LRU + TTL, `VIDEO2TEXT_CACHE_DIR` override
- 📊 **`profile` CLI** (v3.2.0a) — `python -m video2text profile <run.jsonl>` to Markdown / JSON / CSV
- 🧠 **ASR 自动学习** (v3.2.0b) — `python -m video2text learn` 从用户校对累积术语库,越用越准
- 📡 **WebSocket progress** (v3.2.0a) — `/ws/progress/{job_id}` streams 3-bar download / transcribe / assemble updates

### 🚀 Quick Start

#### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/video2text.git
cd video2text

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser (for Douyin/Bilibili)
playwright install chromium
```

#### Single Video Transcription

```bash
# From URL
python -m video2text transcribe "https://www.bilibili.com/video/BV1Nd596vEyU"

# From local file
python -m video2text transcribe "video.mp4" --language zh

# 指定模型 / 设备（默认模型 small；中文推荐 large-v3）
python -m video2text transcribe "video.mp4" --model large-v3 --device cuda

# 全局选项也可前置（与上一行等价）
python -m video2text --model large-v3 transcribe "video.mp4"
```

#### Batch Transcription (Creator's Archive)

```bash
# From creator's profile URL
python douyin_batch_v3.py --user "https://www.douyin.com/user/xxx" -n 20

# From a single video (auto-finds creator)
python douyin_batch_v3.py --from-video "https://v.douyin.com/xxxxx/" -n 10
```

#### ASR Auto-Learning (`learn`)

Accumulate a term-correction glossary from your edits so transcripts get more accurate over time (v3.2.0b).

```bash
# Recommended: diff edited text vs original ASR to extract phonetic-error mappings
python -m video2text learn edit --original "ASR原始文本" --corrected "校对后文本"

# Compare a reference transcript against the ASR output
python -m video2text learn compare --reference "正确文本" --transcript "ASR文本"

# Manual term management
python -m video2text learn save    --wrong "错误词" --right "正确词"
python -m video2text learn list
python -m video2text learn confirm --wrong "错误词" --right "正确词"
python -m video2text learn remove  --wrong "错误词" --right "正确词"
python -m video2text learn export  --output terms.json
python -m video2text learn import  --input terms.json
python -m video2text learn clear
```

### 🏗️ Architecture

```
video2text/
├── video2text/          # Core library: pipeline, downloaders, transcribers, config, url_utils
├── douyin_batch/        # Batch module: browser, logger, config, cache, progress, report, retry, tests/
├── docs/                # Documentation sources
├── docs_site/           # Documentation website (MkDocs)
├── extension/           # Browser extension assets (used by video2text/web/app.py)
├── examples/            # Usage examples
├── scripts/             # Helper scripts
└── output/              # Transcription working dir (git-ignored)
    └── video_transcripts/
        └── douyin_<video_id>/
            ├── downloads/        # source media (mp4)
            ├── audio/            # extracted wav
            ├── metadata/         # platform metadata (json)
            ├── <id>.md           # raw transcript
            └── <id>.reviewed.md  # human-reviewed transcript
```

> **Transcription output.** Local runs write per-video artifacts under
> `output/video_transcripts/douyin_<video_id>/`: the source media in
> `downloads/`, extracted audio in `audio/`, platform metadata in `metadata/`,
> the raw transcript `<id>.md`, and the human-reviewed transcript
> `<id>.reviewed.md`. Each video is fully isolated in its own folder.

### 🧪 Testing

```bash
# Run all unit tests
python run_tests.py

# Demo all v3 features
python demo_v3.py
```

### 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details.

### 📄 License

MIT License - see [LICENSE](LICENSE) for details.

---

## 🤖 AI Agent Compatibility

Video2Text is **built first for AI coding agents**. Every file an agent might
need, every flag an agent might call, and every command an agent might run is
designed to be machine-readable and deterministic.

### Universal guide: `AGENTS.md`

Every agent should read [`AGENTS.md`](AGENTS.md) first. It contains the
project layout, conventions, build/test commands, hard rules, common-task
recipes, and an explicit list of "what NOT to do".

### Per-agent config files

| Agent | File | Status |
|---|---|---|
| **Claude Code** | [`CLAUDE.md`](CLAUDE.md) | ✅ |
| **Cursor** | [`.cursorrules`](.cursorrules) | ✅ |
| **Cline / Roo Code** | [`.clinerules`](.clinerules) | ✅ |
| **Windsurf** | [`.windsurfrules`](.windsurfrules) | ✅ |
| **GitHub Copilot** | [`.github/copilot-instructions.md`](.github/copilot-instructions.md) | ✅ |
| **Cody (Sourcegraph)** | [`.cody.yml`](.cody.yml) | ✅ |
| **Continue** | [`.continue/config.json`](.continue/config.json) | ✅ |
| **Aider** | [`.aider.conf.yml`](.aider.conf.yml) | ✅ |
| **Trae** | [`.traerules`](.traerules) | ✅ |
| **Zed AI / Tabnine / Codeium** | reads `AGENTS.md` automatically | ✅ |

### MCP (Model Context Protocol) server

Expose Video2Text to any MCP-aware agent as native tools:

```bash
# Install MCP SDK (optional, the stdio server is dependency-free)
pip install mcp

# Register in your agent's MCP config
# Claude Code / Cursor / Cline:
{
  "mcpServers": {
    "video2text": {
      "command": "python",
      "args": ["-m", "video2text.mcp_server"]
    }
  }
}
```

Tools exposed: `transcribe_video`, `batch_transcribe_creator`,
`get_cache_stats`, `validate_url`, `sanitize_filename`, `detect_platform`,
`get_transcript`, `transcribe_wechat_mp`. The server works over **stdio**
(default) or **HTTP** (`--transport http --port 8765`) and uses the official
MCP spec (`protocolVersion: 2024-11-05`).

#### MCP tools reference

| Tool | Purpose | Required params | Optional params |
|------|---------|-----------------|-----------------|
| `transcribe_video` | Transcribe a single video URL or local file | `url_or_path` | `engine`, `language`, `output` |
| `batch_transcribe_creator` | Batch-process a creator's videos | `creator_url` | `max_videos`, `engine` |
| `transcribe_wechat_mp` | Extract text / OCR / video from a WeChat MP article | `url` | `ocr_engine`, `ocr_lang`, `save_images` |
| `detect_platform` | Identify platform from URL | `url` | – |
| `validate_url` | Validate a URL is supported | `url` | – |
| `get_transcript` | Read a previously written `.md` transcript | `path` | – |
| `get_cache_stats` | Inspect local cache (size, hit rate) | – | – |
| `sanitize_filename` | Convert arbitrary string to safe filename | `name` | – |

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

In addition to the stable English constants emitted by `--json`, the
`--bilingual-json` flag attaches localised human labels alongside each
constant. The locale is recorded per-field so downstream agents can
detect language without parsing the label value:

```json
{
  "schema": "video2text.agent-output/v1",
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
python douyin_batch_v3.py --user <URL> --platform youtube --platform wechat_mp --json
# Non-matching videos are returned with status="skipped", stage="platform_filter"
```

### Structured JSON output (`--json`)

Every CLI invocation supports `--json` for agents that prefer to invoke the
binary directly. Human-readable output is silenced and a single
`video2text.agent-output/v1` document is emitted to stdout:

```bash
python douyin_batch_v3.py --user <URL> --json
# {
#   "schema": "video2text.agent-output/v1",
#   "ok": true,
#   "command": "douyin_batch_v3",
#   "version": "2.1.0",
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
[`douyin_batch/agent_output.py`](douyin_batch/agent_output.py).

### Agent-friendly task runner

```bash
make help        # list all targets
make install     # pip install -r requirements.txt
make test        # python run_tests.py (170 tests, all pass)
make lint        # ruff check + ruff format --check
make format      # ruff check --fix + ruff format
make verify      # syntax + imports + tests
```

On Windows without `make`, the same commands work as plain
`python -m pip install -r requirements.txt` etc. (see the
[`Makefile`](Makefile) header comments).

### 170 tests across 6 categories run in < 15 s:

```bash
python run_tests.py
# 38 basic unit tests   (config, cache, retry, progress, URL parsing, …)
# 14 agent-output tests (JSON schema, Unicode, save/load, …)
# 15 MCP server tests   (JSON-RPC, stdio, tools, errors)
```

---

## 🌐 i18n / 国际化

The CLI and runtime messages are bilingual. Use `--lang en` or `--lang zh`, or let the tool auto-detect from your locale:

```bash
# Auto-detect (default)
python douyin_batch_v3.py --user "..."

# Force English
python douyin_batch_v3.py --lang en --user "..."

# Force Chinese
python douyin_batch_v3.py --lang zh --user "..."
```

You can also set the environment variable `VIDEO2TEXT_LANG=en` (or `zh`).

To add a new language, edit [`douyin_batch/i18n.py`](douyin_batch/i18n.py) — every message is a `{ "en": ..., "zh": ... }` dictionary. Adding `"ja": "..."` and exposing a `"ja"` choice in `douyin_batch_v3.py` is all that's needed.

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

**Video2Text** 是一个强大的离线视频转文字工具，可以从多个平台（B站、抖音）下载视频，并使用本地 AI 模型（Whisper、WhisperX、whisper.cpp）进行转录。无需云服务、无需 API 密钥、数据不离开您的设备。

### ✨ 核心特性

- 🎬 **多平台支持** - B站、抖音、YouTube、小红书、微信公众号、本地文件
- 🌍 **100% 离线** - 本地 AI 模型，不上传数据
- 🚀 **无需 Cookies** - 智能 URL 提取绕过认证
- 🤖 **多种 AI 引擎** - Whisper、WhisperX、faster-whisper
- 📝 **Markdown 输出** - 结构化转录文档
- 🔄 **批量处理** - 批量下载作者全部往期内容
- 🛡️ **生产就绪** - 日志、错误处理、断点续传
- 🧠 **ASR 自动学习** - `python -m video2text learn` 从用户校对累积术语库,越用越准

### 🚀 快速开始

#### 安装

```bash
# 克隆仓库
git clone https://github.com/yourusername/video2text.git
cd video2text

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器（用于抖音/B站）
playwright install chromium
```

#### 单个视频转录

```bash
# 从 URL
python -m video2text transcribe "https://www.bilibili.com/video/BV1Nd596vEyU"

# 从本地文件
python -m video2text transcribe "video.mp4" --language zh

# 指定模型 / 设备（默认 small；中文推荐 large-v3）
python -m video2text transcribe "video.mp4" --model large-v3 --device cuda

# 全局选项也可前置（等价）
python -m video2text --model large-v3 transcribe "video.mp4"
```

#### 批量转录（作者往期内容）

```bash
# 从作者主页 URL
python douyin_batch_v3.py --user "https://www.douyin.com/user/xxx" -n 20

# 从单个视频（自动找到作者）
python douyin_batch_v3.py --from-video "https://v.douyin.com/xxxxx/" -n 10
```

#### ASR 自动学习（learn）

根据用户校对累积术语库，越用越准（v3.2.0b）。

```bash
# 推荐：对比编辑前后文本，自动提取语音相似的错误映射
python -m video2text learn edit --original "ASR原始文本" --corrected "校对后文本"

# 对比参考文本与转录
python -m video2text learn compare --reference "正确文本" --transcript "ASR文本"

# 手动管理术语
python -m video2text learn save    --wrong "错误词" --right "正确词"
python -m video2text learn list
python -m video2text learn confirm --wrong "错误词" --right "正确词"
python -m video2text learn remove  --wrong "错误词" --right "正确词"
python -m video2text learn export  --output terms.json
python -m video2text learn import  --input terms.json
python -m video2text learn clear
```

### 🧪 测试

```bash
# 运行所有单元测试
python run_tests.py

# 演示 v3 所有功能
python demo_v3.py
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
python douyin_batch_v3.py --user "..."

# 强制英文
python douyin_batch_v3.py --lang en --user "..."

# 强制中文
python douyin_batch_v3.py --lang zh --user "..."
```

也可通过环境变量 `VIDEO2TEXT_LANG=en`（或 `zh`）配置。

如需新增语言，只需编辑 [`douyin_batch/i18n.py`](douyin_batch/i18n.py) —— 每条消息均为 `{ "en": ..., "zh": ... }` 字典，再在 `douyin_batch_v3.py` 中扩展 `--lang` 的可选值即可。

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
