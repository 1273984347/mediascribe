# CI matrix

The repo runs different job sets depending on the trigger:
**PR / push to main** runs the fast gate; **tags and develop** run
the full matrix. Source of truth: `.github/workflows/test.yml`.

## Jobs

| Job | Purpose | Trigger | Matrix |
|-----|---------|---------|--------|
| `lint` | `ruff check .` + `ruff format --check .` (ruff pinned in dev extra) | every PR/push | 1 cell |
| `unit` | fast unit suite (`-m "not integration and not network"`) **with coverage** (`pytest --cov`, **75 %** floor matching the README badge; Codecov + `coverage.xml` artifact uploaded here) | every PR/push | Ubuntu + py3.11 |
| `integration` | E2E (`MEDIASCRIBE_E2E=1`, incl. Playwright — browsers pulled through an `actions/cache` keyed on `pyproject.toml`; Windows ffmpeg comes from the same BtbN static build as Linux) | push to main / tags / nightly cron / manual | Ubuntu+Windows × py3.11, 3.12 |
| `matrix` | full cross-platform smoke | tags + develop | Ubuntu/macOS/Windows × py3.11, 3.12, 3.13 (macOS: 2 of 3) |

There is no standalone `coverage` job since 2026-11-21: it duplicated
the `unit` selection exactly, so `pytest --cov` now runs **inside**
`unit` (one install, one test pass).

Dependencies install via **`uv sync --locked`** since 2026-11-22
(`uv.lock` pins 207 packages — CI fails if the lock is stale versus
`pyproject.toml`).  `integration` no longer runs the whole unit suite
on every push: it runs only the 15 real-URL E2E tests (`-m integration`),
except the **Windows py3.11 cell which keeps the full suite** as the
per-push Windows regression guard.  WhisperX is installed only in the
nightly (schedule) run and the tag/develop matrix — no test imports it
directly.

Measured wall times for main-push `Tests` runs (diagnosis report:
`output/reports/ci-perf-diagnosis-2026-11-22.md`): 6m43s (pre-4811cff)
→ 5m51s (dedup round) → ~3-4m expected with a warm uv cache.

The `lint` and `unit` jobs are **gating**: a red light
on any of them blocks merge.  `bandit.yml` runs a single-Ubuntu
Bandit scan on paths touching production code (MEDIUM+ fails);
the release workflow re-runs it gating on HIGH only.

## Python floor

`requires-python = ">=3.11"` (pyproject.toml).  3.8/3.9/3.10 are
EOL; the oldest interpreter CI exercises is 3.11.  WhisperX is
only exercised on 3.11/3.12 (its support for brand-new CPython
releases lags).

## Concurrency

We cancel in-progress runs of the same branch unless the branch
is `main` or a tag (so we always see the result of every commit
on `main`, and never cancel a release run).

## Adding a new Python or OS

1. Edit the matrix in `.github/workflows/test.yml`.
2. Update the `requires-python` field and classifiers in
   `pyproject.toml` if you are changing the floor.
3. Update this page so the table above stays honest.
4. Push — the matrix will refresh on the next run.
