# Changelog

All notable changes to this project are documented in
`CHANGELOG.md` in the repo root.  This page is a curated
summary; for the full per-commit history see GitHub.

## Recent highlights

### v3.0.0 — Plugin system + long-video chunking + OTel

* **Plugins**: third-party `Downloader` / `Transcriber` /
  `URLTransformer` registration via `entry_points`.
* **Long videos**: `ChunkedTranscriber` splits long audio into
  overlapping windows and merges the per-chunk segments with
  global timestamps.
* **Performance**: `@profile_step` decorator, `PerformanceReport`,
  `parallel_map`, `DownloadCache`.
* **Observability**: a zero-deps OpenTelemetry-compatible mini-SDK
  that can be upgraded to the real `opentelemetry-sdk` with one
  call.
* **Browser extension**: Chrome / Edge Manifest v3 popup that
  posts the active tab URL to a self-hosted Web UI.
* **E2E recording**: `scripts/capture_e2e_recording.py` bundles
  a self-contained `e2e-recording/<ts>/` package for attaching
  to releases.
* **CI matrix**: 5 Python × 3 OS = 15 cells.
* **Docs site**: mkdocs + Material, ready for GitHub Pages.

### v2.x — Bilingual output + WeChat MP hardening

* Bilingual subtitle rendering for WeChat video messages.
* Concurrent OCR (`ThreadPoolExecutor`, up to 4 workers) for
  WeChat image articles.
* `easyocr.Reader` cached module-level (~3 s saved per image).
* Detector fixes for `/s/abc` and `/s?__biz=` URL shapes.
* Real-URL E2E suite (opt-in via `VIDEO2TEXT_E2E=1`).
* WhisperX support with auto-fallback to `faster-whisper`
  then `whisper`.

### v1.x — Initial 6-platform support

* Bilibili, Douyin, YouTube, Xiaohongshu, WeChat MP, TikTok.
* Three engines with manual selection.
* CLI + MCP server (initial).
* 200+ tests, ruff-clean codebase.
