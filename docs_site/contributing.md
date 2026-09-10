# Contributing

Thanks for your interest in MediaScribe!  All contributions are
welcome — bug reports, feature requests, documentation, code.

## Ground rules

* **One concern per PR.**  A bug fix and a refactor do not
  belong in the same pull request.
* **Tests required for code changes.**  Bug fixes need a
  regression test; new features need at least one happy-path
  test and one failure-mode test.
* **Style:** `ruff check .` must pass.  `ruff format` is the
  authoritative formatter — do not hand-format.
* **Commit messages:** imperative mood, ≤ 72 chars on the
  subject line, body explains the *why* not the *what*.
* **CI green before review.**  The 15-cell matrix is fast
  enough to run on every push; please use it.

## Local development setup

```bash
git clone https://github.com/example/mediascribe
cd mediascribe
python -m pip install -e ".[dev,ocr,web,mcp]"
pre-commit install       # optional but recommended
pytest douyin_batch/tests/ -q
```

## Pull request checklist

* [ ] I have read `CONTRIBUTING.md` (this file).
* [ ] I have added or updated tests.
* [ ] `ruff check .` is clean.
* [ ] `pytest douyin_batch/tests/ -q` is green locally.
* [ ] I have updated the docs in `docs_site/` if the user-facing
  behaviour changed.
* [ ] I have not added a hard dependency without an
  `[project.optional-dependencies]` entry first.

## Areas that need help

* **More platforms.**  The plugin system makes adding one
  ~50 lines of code; see [Plugins](plugins.md).
* **Long-video VAD.**  The chunked transcriber currently uses
  fixed windows; a VAD-based splitter would help lecture-style
  content.
* **GPU support for benchmarks.**  The current
  `scripts/benchmark_transcribers.py` only measures CPU; a GPU
  branch using `nvidia-smi` would be a useful complement.
* **Internationalisation.**  The CLI is bilingual (en/zh); the
  Web UI is English-only.

## Reporting a security issue

Please email `security@example.com` rather than opening a
public issue.  See `SECURITY.md` for the disclosure policy.
