# CI matrix

The repo runs the full test suite on every push and PR.

## Matrix

| Axis | Values | Count |
|------|--------|-------|
| Python | 3.8, 3.9, 3.10, 3.11, 3.12 | 5 |
| OS | Ubuntu, macOS, Windows | 3 |
| **Total cells** | | **15** |

The matrix lives in `.github/workflows/test.yml` under
`jobs.test.strategy.matrix`.  WhisperX is only compatible with
Python ≥ 3.9; on 3.8 the corresponding import test is allowed
to fail gracefully.

## Jobs

| Job | Purpose | Runs on |
|-----|---------|---------|
| `test` | The 15-cell matrix above | each cell |
| `lint` | `ruff check` + `ruff format --check` | Ubuntu + py3.11 |
| `coverage` | `pytest --cov` with a 60 % floor | Ubuntu + py3.11 |
| `build-docs` | `mkdocs build --strict` (best-effort) | Ubuntu + py3.11 |

The lint and coverage jobs are **gating**: a red light on
either blocks merge.  The build-docs job is **soft** because
docs may not always be in sync with code.

## Concurrency

We cancel in-progress runs of the same branch unless the branch
is `main` (so we always see the result of every commit on
`main`).

## Artifacts

On the `test` job, the build artifacts (`dist/*.whl` and
`dist/*.tar.gz`) are uploaded for the **Ubuntu + py3.11** cell
so the release job can download them without rebuilding.

## Adding a new Python or OS

1. Edit the matrix in `.github/workflows/test.yml`.
2. Update the `requires-python` field in `pyproject.toml` if
   you are adding a new floor / ceiling.
3. Update the `include:` block if some combinations need
   special handling.
4. Push — the matrix will refresh on the next run.
