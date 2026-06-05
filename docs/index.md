# Video2Text Documentation

Welcome to the **Video2Text** documentation. This is a single-page index
pointing at every reference document and the most useful entry points
into the codebase.

## Quick start

```bash
# Install
pip install -r requirements.txt

# Transcribe a single video
python -m video2text --url "https://www.bilibili.com/video/BVxxxxx"

# Batch a creator's feed
python douyin_batch_v3.py --user "https://www.douyin.com/user/xxxxx"

# Start the MCP server (for Claude Code / Cursor / Cline)
python -m video2text.mcp_server
```

## Reference

| Document | Purpose |
|----------|---------|
| [README.md](../README.md) | User-facing overview, install, CLI examples |
| [CHANGELOG.md](../CHANGELOG.md) | All notable changes (Keep a Changelog format) |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | How to contribute (PR, dev setup, style) |
| [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | Community guidelines |
| [DOWNLOADERS.md](../DOWNLOADERS.md) | Per-platform downloader reference |
| [AGENTS.md](../AGENTS.md) | Notes for AI agents consuming this repo |
| [CLAUDE.md](../CLAUDE.md) | Claude Code specific guidance |

## Generated docs

| Document | Purpose |
|----------|---------|
| [lint-report.md](lint-report.md) | Output of `make lint` / `make verify` |
| [cli-screenshots/index.md](cli-screenshots/index.md) | Captured CLI help output (text snapshots) |
| [e2e-results.md](e2e-results.md) | Manual E2E run results (real URLs, real ASR) |

## Source layout

```
video2text/                      # core library
├── __main__.py                  # python -m video2text entry
├── pipeline.py                  # Pipeline.transcribe()
├── platform.py                  # detect_platform() URL router
├── config.py                    # Settings + cookie parsing
├── models.py                    # SourceRef, DownloadResult, TranscriptResult
├── mcp_server.py                # 8 MCP tools
├── downloaders/                 # platform-specific downloaders
│   ├── ytdlp.py
│   ├── youtube.py               # 4 player_client rotations
│   ├── xiaohongshu.py           # Playwright
│   ├── wechat_mp.py             # 3-mode dispatcher + OCR fallback
│   └── ...
├── transcribers/                # ASR backends
│   ├── whisper.py
│   ├── faster_whisper.py
│   └── whisperx.py
└── url_utils.py

douyin_batch/                    # batch CLI library
├── __init__.py
├── agent_output.py              # video2text.agent-output/v1 schema
├── config.py                    # BatchConfig dataclass
├── i18n.py                      # 104 i18n keys
├── platform_compat.py           # cross-platform helpers
├── security.py                  # URL/filename sanitisation
├── wechat_cookies.py            # 3-tier cookie injection
└── tests/                       # 200+ unit tests

douyin_batch_v3.py               # python douyin_batch_v3.py entry

docs/                            # this directory
├── index.md                     # ← you are here
├── lint-report.md
├── cli-screenshots/
└── e2e-results.md
```

## Testing

```bash
# Default: 195 unit tests, no network
make test
# or
python -m pytest douyin_batch/tests/ -q

# With E2E (real network, real cookies, real ASR)
VIDEO2TEXT_E2E=1 python -m pytest douyin_batch/tests/test_e2e_real_urls.py -v

# Lint
make lint
```

## Releases

See [CHANGELOG.md](../CHANGELOG.md) and the GitHub Releases page.

| Version | Date | Highlights |
|---------|------|------------|
| Unreleased | (in progress) | 190 tests, 8 MCP tools, --platform filter, OCR fallback, bilingual JSON |
| 2.1.0 | 2026-05-15 | Smart routing without cookies, Markdown output |
| 2.0.0 | 2026-04-20 | Initial yt-dlp + Whisper integration |
