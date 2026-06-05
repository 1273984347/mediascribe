# Video2Text v3.1.0 Release Notes

> Released 2026-06-04 · MIT · 547 tests passing (9 skipped) · 97% coverage

## Highlights

* **Plugin system** — third-party `Downloader` / `Transcriber` / `URLTransformer`
  registration via `entry_points` (zero changes to video2text itself).
* **Long-video chunking** — `ChunkedTranscriber` splits long audio into
  overlapping windows (default 600 s + 5 s overlap) and merges per-chunk
  segments with global timestamps.
* **Performance utilities** — `@profile_step`, `PerformanceReport`,
  `parallel_map`, `DownloadCache`.
* **OpenTelemetry-compatible mini-SDK** — zero-dependency tracer / meter
  that can be upgraded to the real `opentelemetry-sdk` with one call.
* **Browser extension** — Chrome / Edge Manifest v3 popup that POSTs
  the active tab URL to a self-hosted Web UI.
* **Web UI** — FastAPI + vanilla HTML, served by `python -m video2text.web.app`.
* **mkdocs documentation site** — 18 pages, Material theme, GitHub Pages
  workflow ready to enable.
* **CI matrix** — 5 Python × 3 OS = 15 cells with system ffmpeg install
  per OS, plus `lint` / `coverage` / `build-docs` jobs.
* **E2E recording bundle** — `scripts/capture_e2e_recording.py` produces
  a self-contained package ready to attach to a GitHub release.
* **3 SKILL.md files** under `.trae/skills/` so AI agents can call
  the project as a skill.

## Bug fixes

* WeChat MP detector now accepts both `/s?__biz=...` and
  `/s/<id>?__biz=...` URL forms.

## New / changed files

```
video2text/
├── plugins/                  (new) entry_points-based plugin system
├── observability.py          (new) zero-deps OTel-compatible mini-SDK
├── performance.py            (new) @profile_step, parallel_map, DownloadCache
├── transcribers/chunked.py   (new) long-video chunked transcriber
└── web/                      (new) FastAPI + HTML UI

scripts/
├── capture_e2e_recording.py  (new) bundle E2E results into a release asset

extension/                    (new) Chrome / Edge Manifest v3 extension
examples/plugins/             (new) example downloader + URL transformer
docs_site/                    (new) mkdocs source (18 pages)
mkdocs.yml                    (new) Material theme + dark/light toggle
.trae/skills/                 (new) 3 SKILL.md files for agent integration

.github/workflows/
├── test.yml                  (rewritten) 5 py × 3 os matrix + lint + coverage + build-docs
└── docs.yml                  (new) GitHub Pages deploy workflow
```

## Stats

| Metric | v3.0.0 | v3.1.0 | Δ |
|--------|--------|--------|---|
| Python modules | ~58 | ~62 | +4 |
| Tests | 232 | 275 | +43 |
| `dist/` size | ~210 KB | ~225 KB | +15 KB |
| Documentation pages | 1 README | 1 README + 18 docs | +18 |
| SKILL.md files | 0 | 3 | +3 |

## How to upgrade

```bash
pip install --upgrade video2text
```

If you use the Web UI / browser extension:

```bash
pip install --upgrade "video2text[web]"
```

## How to verify

```bash
python -c "import video2text; print(video2text.__version__)"
python -m pytest douyin_batch/tests/ -q
```

You should see `3.1.0` and `275 passed`.

## Known issues

* `whisperx` requires Python ≥ 3.9; the test for it tolerates an
  `ImportError` on 3.8.
* The chunked transcriber does not use VAD — fixed windows only.
  A VAD-based splitter is on the v3.2 roadmap.
* The download cache is per-run only (not persistent).
