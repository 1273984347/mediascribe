# GitHub Copilot workspace instructions for Video2Text
# Read alongside AGENTS.md (the universal AI-agent guide).

## Project summary
Video2Text is an offline video transcription tool. It downloads videos from
Bilibili, Douyin, and local files, then transcribes audio with local AI models
(Whisper, WhisperX, faster-whisper). No cloud services, no API keys, no
cookies for Douyin.

## Tech stack
- Python 3.8+ (3.12 recommended)
- Core library: `video2text/`
- Batch library: `douyin_batch/`
- CLI entry: `douyin_batch_v3.py`

## Code conventions
- Use `pathlib.Path` for all paths.
- Type hints on all public functions. English docstrings.
- User-facing strings **must** go through `t()` from `douyin_batch/i18n.py`.
  Add new keys to the `Messages` class as `{"en": "...", "zh": "..."}` dicts.
- PEP 604 (`X | Y`) or PEP 585 (`list[int]`) syntax requires
  `from __future__ import annotations` for Python 3.8 compatibility.

## Test commands
- All tests: `python run_tests.py` (38 tests, ~5 s, must all pass)
- Or via Makefile: `make test`
- Lint: `make lint` (or `python -m black --check . && python -m flake8 .`)
- Format: `make format` (or `python -m black . && python -m isort .`)

## Hard rules (never break)
- Never commit: `output/`, `models/`, `*.cookies`, `.env`, `my_config.json`.
- Never add new top-level dependencies without updating both
  `pyproject.toml` and `requirements.txt`.
- Always validate user URLs with `douyin_batch.security.is_safe_url()`.
- Always sanitize user filenames with
  `douyin_batch.platform_compat.safe_filename()`.
- Don't hard-code platform names or domains in business logic — use
  `douyin_batch.security.TRUSTED_DOMAINS`.

## Full guide
Read `AGENTS.md` for the complete agent-friendly guide: file map, common task
recipes, and "what NOT to do" rules.
