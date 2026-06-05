# Video2Text v3.1.0

> **Offline video transcription tool** — download videos from multiple
> platforms and transcribe them with local AI models. No cloud, no API
> keys, no data leaves your machine.

[![Tests](https://img.shields.io/badge/Tests-547%20passed-brightgreen.svg)](#)
[![Coverage](https://img.shields.io/badge/Coverage-97%25-brightgreen.svg)](#)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](#)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platforms](https://img.shields.io/badge/Platforms-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)

---

## Highlights

### 🔌 Plugin system via `entry_points`
Third-party `Downloader` / `Transcriber` / `URLTransformer` registration
through standard Python entry_points — zero changes to `video2text` itself.
See `examples/plugins/` (TCN transformer, Vimeo downloader) and
[docs_site/plugins.md](docs_site/plugins.md).

### 🎙️ Long-video chunking
`ChunkedTranscriber` splits long audio into overlapping windows
(default 600 s + 5 s overlap) and merges per-chunk segments with global
timestamps. See [docs_site/chunking.md](docs_site/chunking.md).

### ⚡ Performance utilities
- `@profile_step` decorator for step-level timing
- `PerformanceReport` with auto-generated Markdown summary
- `parallel_map` with ordered output and error tolerance
- `DownloadCache` for content-addressed media caching

### 🔭 OpenTelemetry-compatible mini-SDK
Zero-dependency tracer + meter that drops in `OBSERVABILITY` globals.
Upgrade to real `opentelemetry-sdk` with one call:
`install_opentelemetry_exporter()`. See
[docs_site/observability.md](docs_site/observability.md).

### 🛡️ Production hardening
- **Rate limiting** — thread-safe sliding window (default 10 req/60 s per
  IP), RFC 6585 headers (`429` / `Retry-After` / `X-RateLimit-*`),
  configurable via `VIDEO2TEXT_RATE_LIMIT` (set to `0` to disable)
- **Security** — path-traversal protection, CORS, basic auth
- **CORS** — explicit allow-list with credentials toggle
- **Cookies** — JSON / Netscape formats, env-var injection

### 🧩 Browser extension (Chrome 114+ Side Panel)
- Toolbar popup for quick transcribe
- **Side Panel** (Chrome 114+) for persistent workspace
- One-click ZIP download from the Web UI (`/extension` page)
- Open-source under the same MIT license

### 🤖 AI agent native
- `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.clinerules`, `.cody.yml`,
  `.windsurfrules`, `.aider.conf.yml`, `.traerules`, GitHub Copilot
- **MCP server** (`video2text.mcp_server`) for Claude Code / Cursor / Cline
- **JSON output mode** for agent-friendly consumption
- **Skills**: `video2text`, `video2text-benchmark`, `video2text-wechat`

### 🚀 One-click start
Five paths, all runnable out of the box:
- `python scripts/one_click_up.py` (cross-platform)
- `bash scripts/one_click_up.sh` (Linux / macOS)
- `powershell scripts/one_click_up.ps1` (Windows)
- `make up` / `just up` (build tools)
- `docker compose up` (containers)

---

## What's New in 3.1.0

### Added
- **Browser Side Panel support** (`extension/manifest.json`,
  `extension/background.js`, `extension/popup.{html,js,css}`)
- **Plugin system** (`video2text/plugins/registry.py`)
- **Long-video chunking** (`video2text/transcribers/chunked.py`,
  `douyin_batch/`)
- **Performance utilities** (`video2text/performance.py`)
- **OpenTelemetry-compatible mini-SDK** (`video2text/observability.py`,
  `douyin_batch/observability.py`)
- **Rate limiting** with `VIDEO2TEXT_RATE_LIMIT` env var
- **Security**: path-traversal guards, CORS, basic auth
- **MCP server** with `transcribe_video` and `transcribe_batch` tools
- **Skill files** for `video2text`, `video2text-benchmark`,
  `video2text-wechat`
- **Web UI extension builder** (one-click ZIP download at `/extension`)
- **CI matrix**: Python 3.8-3.12 × Windows/macOS/Linux
- **Bandit security scanning** in CI
- **Dependabot** for grouped dependency updates
- **WebSocket / WebDAV / async transcriber base classes**

### Changed
- `douyin_batch_v3.py` is now fully i18n-aware (CLI help, banner, status)
- README is bilingual; English-first with the Chinese section preserved
- `pyproject.toml` updated with optional `whisperx` / `faster-whisper` extras
- `Logger.set_quiet()` for clean `--json` mode output
- `AgentOutput._apply_labels` accepts a `lang` parameter
- `Pipeline.transcribe` accepts a new `bilingual` keyword for WeChat MP
  video messages
- `process_single_video_safe` accepts a `platform_filter` list
- Lint tooling unified to **ruff** (was `flake8` + `black` + `isort`)

### Fixed
- Hard-coded Chinese strings in `douyin_batch_v3.py` replaced with `t()`
- `safe_filename()` strips control chars and Windows reserved names
- MCP server's stdio parser now handles UTF-8 BOM
- `Pipeline._get_downloader` fallback chain ensures every platform gets
  a working downloader
- `Settings.wechat_cookies_file` is now properly stored as a `Path`
- `report.generate_summary_report` no longer crashes when
  `transcript=None` or the field is missing
- Thread-safe sliding window rate limiter
- Path traversal attacks blocked in MCP file endpoints
- Bandit false-positives excluded from `tests/`

---

## Quality

- **547 tests passing**, 9 skipped in ≈ 175 s
- **97% coverage** project-wide
- **8 modules at 100%**: `__init__`, `agent_output`, `cache`, `config`,
  `platform_compat`, `report`, `retry`, `transcribe`
- **0 ruff errors** across the entire codebase
- **CI** runs `ruff` + `pytest` + `bandit` on every push / PR

---

## Installation

### From source (recommended for v3.1.0)

```bash
git clone https://github.com/<your-org>/video2text.git
cd video2text
pip install -r requirements.txt
# optional: pip install -r requirements-dev.txt  (for testing)
```

### Optional extras

```bash
# WhisperX (faster, word-level timestamps)
pip install video2text[whisperx]
# or faster-whisper (CTranslate2 backend)
pip install video2text[faster-whisper]
```

### Docker

```bash
docker compose up
# Web UI → http://localhost:8000
```

### One-click (no Python knowledge required)

```bash
python scripts/one_click_up.py    # macOS / Linux / Windows
```

---

## Quick start

### CLI

```bash
# Single video
python -m video2text "https://www.bilibili.com/video/BVxxxxxx"

# Batch from a user profile
python -m video2text "https://www.douyin.com/user/MS4wLjABAAAA..."

# JSON output for agents
python -m video2text --json "URL"
```

### Python API

```python
from video2text import Pipeline, Settings

settings = Settings(workspace_root="./workspace")
pipeline = Pipeline(settings)
result = pipeline.transcribe("https://www.bilibili.com/video/BVxxxxxx")
print(result.transcript_path.read_text(encoding="utf-8"))
```

### MCP server (for AI agents)

```bash
python -m video2text.mcp_server
# configure Claude Code / Cursor to connect via stdio
```

### Web UI

```bash
python -m video2text.web
# or: python main.py --mode web
# then open http://localhost:8000
```

### Browser extension

1. Run the Web UI: `python -m video2text.web`
2. Visit `http://localhost:8000/extension`
3. Click **Download ZIP**
4. Chrome / Edge → `chrome://extensions` → enable Developer mode →
   "Load unpacked" → select the extracted folder

Chrome 114+ unlocks the **Side Panel** view (persistent, dockable).

---

## Supported platforms

| Platform  | Downloader           | Notes                  |
|-----------|----------------------|------------------------|
| Bilibili  | `YtDlpDownloader`    | No cookies required    |
| Douyin    | `DouyinDownloader`   | Real `douyinvod.com` URLs |
| YouTube   | `YoutubeDownloader`  |                        |
| Xiaohongshu | `XiaohongshuDownloader` |                  |
| WeChat MP | `WechatMpDownloader` | Cookies supported     |

Whisper engines: `whisper`, `whisperx`, `faster-whisper`, `chunked`.

---

## Documentation

Full docs at [docs_site/](docs_site/):

- [Getting started](docs_site/getting-started.md)
- [Plugins](docs_site/plugins.md)
- [Long-video chunking](docs_site/chunking.md)
- [Performance profiling](docs_site/performance.md)
- [OpenTelemetry observability](docs_site/observability.md)
- [Browser extension](docs_site/browser-extension.md)
- [Web UI](docs_site/web-ui.md)
- [MCP server](docs_site/mcp.md)
- [Platforms](docs_site/platforms.md)
- [WeChat MP support](docs_site/wechat-mp.md)
- [E2E testing](docs_site/e2e-testing.md)
- [CI matrix](docs_site/ci-matrix.md)
- [Benchmarking](docs_site/benchmarking.md)
- [Contributing](docs_site/contributing.md)
- [License (MIT)](docs_site/license.md)
- [Changelog](docs_site/changelog.md)

---

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

- [OpenAI Whisper](https://github.com/openai/whisper) — transcription
- [WhisperX](https://github.com/m-bain/whisperX) — word-level timestamps
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — generic video downloader
- [Playwright](https://playwright.dev/python/) — browser automation
- [OpenTelemetry](https://opentelemetry.io/) — observability inspiration
- All open-source contributors — ❤️

---

**Full changelog**: [CHANGELOG.md](CHANGELOG.md)
**Full release notes**: [RELEASE_NOTES_v3.1.0.md](RELEASE_NOTES_v3.1.0.md)
