# CLAUDE.md — Claude Code instructions for Video2Text
#
# Claude Code reads this file automatically when working in this repo.
# For the full universal guide that works across all AI agents, see AGENTS.md.

Read `AGENTS.md` first. It contains the full project guide: layout, conventions,
test commands, what NOT to do, and common task recipes.

## Quick reference

- **Test**: `python run_tests.py` (38 tests, ~5s)
- **Lint**: `python -m ruff check .`
- **Format**: `python -m ruff format .`
- **CLI**: `python douyin_batch_v3.py --user <URL> --lang en`

## When asked to change code

1. Read the relevant file in `video2text/` or `douyin_batch/`.
2. If you add user-visible strings, add them to `Messages` in
   `douyin_batch/i18n.py` as `{"en": "...", "zh": "..."}`.
3. If you change a CLI surface, update the test in
   `douyin_batch/tests/test_i18n_integration.py`.
4. Run `python run_tests.py` after any change. All 38 tests must still pass.

## Hard rules (do not break)

- Python 3.8+ syntax. PEP 604/585 only with `from __future__ import annotations`.
- All user-visible strings via `t()` from `douyin_batch.i18n`.
- Never commit `output/`, `models/`, `*.cookies`, `.env`, or `my_config.json`.
- Never add new top-level dependencies without updating `pyproject.toml`
  and `requirements.txt`.

## Bash

- On Windows, this repo expects PowerShell or `cmd`. Use `;` not `&&` to
  chain commands in PowerShell v5.
- FFmpeg must be in `PATH` (or `C:\ffmpeg\bin` on Windows).
