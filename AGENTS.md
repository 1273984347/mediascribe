# AGENTS.md — Instructions for AI Coding Agents

> **Universal project guide for AI coding agents** (Claude Code, Cursor, Cline,
> Windsurf, GitHub Copilot, Aider, Cody, Continue, Trae, Roo Code, Zed AI, etc.).
>
> If your tool only reads a specific file, use the symlinks at the bottom of
> this document.

## What this project is

**Video2Text** is an offline video transcription tool. It downloads videos
from Bilibili, Douyin, and local files, then transcribes the audio with local
AI models (Whisper / WhisperX / faster-whisper). No cloud, no API keys, no
cookies for Douyin.

## Quick facts (read these first)

- **Language**: Python 3.8+ (3.12 recommended)
- **Layout**:
  - `video2text/` — core library (downloaders, transcribers, pipeline)
  - `douyin_batch/` — batch-processing library (browser automation, cache,
    retry, progress, report, i18n, security, platform-compat)
  - `douyin_batch_v3.py` — main CLI entry point for batch jobs
  - `douyin_batch/tests/` — unit tests (`unittest` style)
- **CLI**: `python douyin_batch_v3.py --user URL --lang en` (bilingual)
- **Tests**: `python run_tests.py` (38 unit tests, ~5 s)
- **Lint/format**: `ruff` (configured in `pyproject.toml`)
- **License**: MIT
- **Default UI language**: English (auto-detected from `LANG` / `VIDEO2TEXT_LANG`)

## Build / test commands

Prefer the `Makefile` targets — they're agent-friendly and cross-platform
(works on Windows with `make` from MinGW / Chocolatey / Scoop, or WSL):

```bash
make install   # pip install -r requirements.txt
make test      # python run_tests.py
make lint      # flake8 + black --check
make format    # black + isort
make clean     # remove __pycache__ and build artifacts
make demo      # python demo_v3.py
make help      # list all targets
```

On Windows without `make`, fall back to:

```powershell
python -m pip install -r requirements.txt
python run_tests.py
python -m black --check .
python -m flake8 .
```

## Project conventions

### Code style
- Python 3.8+ syntax only. PEP 8 + `ruff format` (line-length 100).
- If you use PEP 604 (`X | Y`) or PEP 585 (`list[int]`) syntax in a module,
  that module **must** start with `from __future__ import annotations`. This
  is required for Python 3.8 compatibility.
- Type hints on all public functions. Docstrings in English.
- Keep functions focused and small.

### i18n (internationalization)
- **All user-visible strings** in CLI / log output must go through `t()`
  from `douyin_batch/i18n.py`.
- Add new keys to the `Messages` class as a dict `{"en": "...", "zh": "..."}`.
- Tests in `douyin_batch/tests/test_i18n_integration.py` enforce this.
- Adding a new language: extend each dict with `"ja": "..."` etc., and add
  the language to the `choices` in `douyin_batch_v3.py`'s `--lang` argument.

### Cross-platform code
- Use `pathlib.Path` everywhere. Never hard-code `/` or `\` as a path separator.
- Use `douyin_batch.platform_compat.safe_filename()` for any user-derived
  filename.
- Use `douyin_batch.platform_compat.check_ffmpeg()` to detect ffmpeg.
- Use `douyin_batch.security.is_safe_url()` before fetching any URL.

### Module boundaries
- `video2text/` — the public API. Backwards compatible; add to `__all__`.
- `douyin_batch/` — internal batch utilities; can refactor freely between
  minor versions.
- Root-level `douyin_batch_v*.py` — thin CLI shims around `douyin_batch/`.

## File map (for navigation)

| Path | Purpose |
|---|---|
| `video2text/pipeline.py` | Orchestrates download + transcribe |
| `video2text/downloaders/ytdlp.py` | Generic yt-dlp wrapper (Bilibili, YouTube, etc.) |
| `video2text/downloaders/douyin.py` | Douyin-specific downloader (no cookies) |
| `video2text/transcribers/whisper.py` | OpenAI Whisper wrapper |
| `video2text/transcribers/whisperx.py` | WhisperX wrapper (diarization) |
| `video2text/transcribers/faster_whisper.py` | CTranslate2-based wrapper |
| `douyin_batch/browser.py` | Playwright-based page renderer |
| `douyin_batch/cache.py` | Resume / dedup cache |
| `douyin_batch/retry.py` | Retry with exponential backoff |
| `douyin_batch/progress.py` | Progress bar + ETA |
| `douyin_batch/report.py` | Markdown summary report generator |
| `douyin_batch/i18n.py` | Bilingual message registry |
| `douyin_batch/security.py` | URL / filename safety |
| `douyin_batch/platform_compat.py` | OS / path / ffmpeg detection |
| `douyin_batch/tests/` | 38 unit tests + 4 integration smoke tests |
| `pyproject.toml` | Build config + tool settings (black, isort, mypy, pytest) |
| `Makefile` | Agent-friendly task runner |
| `CHANGELOG.md` | Version history (Keep a Changelog) |
| `CONTRIBUTING.md` | How to contribute (bilingual) |
| `CODE_OF_CONDUCT.md` | Community standards |
| `LICENSE` | MIT |

## What NOT to do

- Do **not** add new user-visible strings outside the `t()` registry.
- Do **not** hard-code `https://` URLs or platform names in business logic;
  use `douyin_batch.security.TRUSTED_DOMAINS`.
- Do **not** introduce new top-level dependencies without updating
  `requirements.txt` and `pyproject.toml`.
- Do **not** add new file types to `.gitignore` exceptions without good
  reason (cookies, models, large downloads are already ignored).
- Do **not** commit `output/`, `models/`, `*.cookies`, or `.env` files.

## Testing expectations

When you change code in `douyin_batch/` or `video2text/`, also:

1. Add or update a unit test in `douyin_batch/tests/`.
2. Run `python run_tests.py` and confirm 38 tests still pass.
3. If you add CLI surface, add or extend `test_i18n_integration.py`.
4. If you touch platform-specific code, add a case to `test_cross_platform.py`.

## Common tasks recipes

### Add a new CLI option
1. Add the key to `Messages` in `douyin_batch/i18n.py` (`CLI_HELP_*`).
2. Register the argument in `douyin_batch_v3.py` using `t("CLI_HELP_*")`.
3. Add an integration test in `test_i18n_integration.py`.

### Add a new downloader
1. Subclass `Downloader` in `video2text/downloaders/base.py`.
2. Implement `download()` and `supports(source)`.
3. Add to `video2text/downloaders/__init__.py`.
4. Add a unit test in `douyin_batch/tests/`.

### Add a new language
1. In `douyin_batch/i18n.py`, add `"<code>": "..."` to every dict in
   `Messages`.
2. Add the code to `set_language()` validation and the `--lang` argparse
   `choices`.
3. Update `README.md` i18n section.
4. Update `CHANGELOG.md`.

## Symlinks for tool-specific files

If your agent only reads one of these, point it at `AGENTS.md`:

- `CLAUDE.md` → `AGENTS.md` (Claude Code)
- `.cursorrules` → `AGENTS.md` (Cursor)
- `.clinerules` → `AGENTS.md` (Cline / Roo Code)
- `.windsurfrules` → `AGENTS.md` (Windsurf)
- `.github/copilot-instructions.md` → `AGENTS.md` (GitHub Copilot)
- `.aider.conf.yml` references `AGENTS.md` (Aider)
