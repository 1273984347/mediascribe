# GitHub Copilot workspace instructions for MediaScribe
# Read alongside AGENTS.md (the universal AI-agent guide).

## Project summary
MediaScribe is an **offline** video transcription tool. It downloads videos
from Bilibili, Douyin, YouTube, Xiaohongshu, WeChat MP and local files, then
transcribes them with local AI models (Whisper, WhisperX, faster-whisper,
whisper.cpp). No cloud services, no API keys, no data leaves the machine.

## Tech stack
- Python 3.11+ (3.12 recommended; 3.13 supported)
- **Single package**: `mediascribe/` — there is no second top-level package
- ASR backends: `mediascribe/transcribers/` (whisper, whisperx, faster_whisper,
  whisper_cpp, chunked)
- Downloaders: `mediascribe/downloaders/` (douyin, wechat_mp, xiaohongshu,
  youtube, ytdlp)
- Web UI: FastAPI + vanilla ES modules (`mediascribe/web/`)
- CLI entry points: `mediascribe` (sub-commands) and `mediascribe-batch`
  (creator-archive batch mode, implemented in `mediascribe/batch_cli.py`)

## Code conventions
- Use `pathlib.Path` for all paths.
- Type hints on all public functions. English docstrings.
- User-facing strings **must** go through `t()` from `mediascribe/i18n.py`.
  Add new keys to the `Messages` class as `{"en": "...", "zh": "..."}` dicts.
- Python >= 3.11, so PEP 604 (`X | Y`) / PEP 585 (`list[int]`) need no
  `from __future__ import annotations`.

## Test commands
- All tests: `python scripts/run_tests.py` (CI-same selection)
- Or via Makefile / justfile: `make test` / `just test`
- Lint: `make lint` (or `python -m ruff check . && python -m ruff format --check .`)
  — **ruff is the only lint source of truth** (not black / flake8)
- Format: `make format` (or `python -m ruff check --fix . && python -m ruff format .`)
- Types: `mypy mediascribe` (blocking in CI)

## Hard rules (never break)
- Never commit: `output/`, `models/`, `*.cookies`, `cookies.txt`, `.env`,
  `my_config.json`, `config.json`, `site/`, `archive/`.
- Never add new top-level dependencies without updating both
  `pyproject.toml` and `requirements.txt`.
- Always validate user URLs with `mediascribe.security.is_safe_url()`.
- Always sanitize user filenames with
  `mediascribe.security.sanitize_filename()`.
- Don't hard-code platform names or domains in business logic — use
  `mediascribe.security.TRUSTED_DOMAINS`.
- `mediascribe.agent-output/v1` is a **stable schema**: its field values
  (e.g. `"command": "douyin_batch_v3"`) are consumed downstream and must not
  change without bumping the schema version.
- New pages under `docs/` must be added to the `nav` in `mkdocs.yml` — CI
  builds the site with `--strict` and unlisted pages fail the build.

## Full guide
Read `AGENTS.md` for the complete agent-friendly guide: repository map,
scripts/ layout, common task recipes, and "what NOT to do" rules.
