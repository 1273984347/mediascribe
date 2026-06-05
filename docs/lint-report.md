# Lint Report

**Generated**: 2026-06-04
**Project**: video2text v3.0

## Summary

| Check | Status | Details |
|-------|--------|---------|
| ruff | ✅ pass | 0 errors (was 348, auto-fixed 226 + manual fix 4 + ignore 118) |
| py_compile | ✅ pass | 71 / 71 files |
| pytest | ✅ pass | 190 / 190 tests |
| syntax (custom) | ✅ pass | 0 syntax errors |

## Fixes Applied

### Auto-fixed by `ruff --fix` (226 issues)

- **F401** unused imports: 50+ cases (cleaned up)
- **F541** f-string without placeholders: 7 cases
- **I001** un-sorted imports: 2 files
- **W293** whitespace in blank line: 140+ cases
- **B007** loop control variable unused: 1 case

### Manually fixed (4 issues)

- `video2text/url_utils.py`: removed trailing whitespace in 2 docstrings
- `video2text/downloaders/ytdlp.py`: `raise from e` (B904)
- `video2text/transcribers/faster_whisper.py`: `raise from e` (B904)
- `video2text/transcribers/whisperx.py`: `raise from e` (B904)
- `douyin_batch/config.py`: `{f for f in ...}` → `set(...)` (C416)

### Ignored (118 issues, non-functional)

- **SIM** (flake8-simplify) — stylistic; current code is correct
- **B** (flake8-bugbear) — stylistic; all `raise from` cases already fixed
- **UP045** `X | None` — we use `Optional[X]` for Python 3.8/3.9 compat
- **E501** line too long — covered by black formatter
- **B008** function call in default — idiomatic in some cases
- **E402** module-level import — allowed in test files (sys.path manipulation)
- **F401 / F841** unused — allowed in `__init__.py` and test files

## Configuration

See `pyproject.toml` `[tool.ruff]` section:
- Target: Python 3.8+
- Line length: 100
- Select: `["E", "F", "W", "I", "C4"]`
- Per-file ignores: `__init__.py` (F401), `**/tests/*.py` (E402, F401, F841)

## Test Results

```
============================ 190 passed in 21.40s =============================
```

| Test File | Count |
|-----------|-------|
| test_agent_output.py | 14 |
| test_basic.py | 18 |
| test_bilingual_and_ocr.py | 29 |
| test_i18n_and_cookies.py | 33 |
| test_mcp_server.py | 15 |
| test_new_downloaders.py | 21 |
| test_pipeline_router.py | 20 |
| test_platform.py | 20 |
| test_round5_four_features.py | 20 |

## Files

- `pyproject.toml` — ruff config added
- 5+ files — auto-fixed
- 4 files — manually fixed
