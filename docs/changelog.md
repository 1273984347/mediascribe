# Changelog

All notable changes to this project are documented in
`CHANGELOG.md` in the repo root.  This page is a curated
summary; for the full per-commit history see GitHub.

!!! warning "Historical / Superseded entries"
    These highlights describe what shipped at the time, not necessarily the
    current interface.  VAD and `PersistentChunkCache` remain Python-level
    building blocks and are not wired into ordinary CLI / Web / MCP
    transcription ([Long videos](chunking.md)).  The profile CLI supports
    `--top`, `--by-stage`, `--since`, and `--json`; old `--watch`, `--format`,
    `--tz-aware`, and CSV claims are superseded ([Performance](performance.md)).
    The OTel mini-SDK / `otel` extra were removed on 2026-10-03.  The factory
    now has no fallback chain: automatic selection prefers installed
    `faster-whisper`, then `whisper`, while `whisperx` is opt-in
    ([Engines](engines.md)).

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
  call.  **Historical / superseded:** the module and extra were removed on
  2026-10-03; current local events / metrics are not an OTel exporter.
* **Browser extension**: Chrome / Edge Manifest v3 popup that
  posts the active tab URL to a self-hosted Web UI.
* **E2E recording**: `scripts/e2e/capture_e2e_recording.py` bundles
  a self-contained `e2e-recording/<ts>/` package for attaching
  to releases.
* **CI matrix**: 5 Python × 3 OS = 15 cells.
* **Docs site**: mkdocs + Material, ready for GitHub Pages.

### v3.2.x — Async pipeline + GPU acceleration + LLM post-processing

* **v3.2.0a** — VAD chunking, persistent cache, `profile` CLI,
  WebSocket progress.  **Current boundary:** VAD / chunk caching are not
  wired into ordinary CLI / Web / MCP paths; use the documented Python API.
  Web progress now has six stage identifiers and boundary-only counters,
  not real intra-stage percentages.
* **v3.2.0b** — `AsyncPipeline` with `asyncio.Semaphore`-capped
  worker pool; `resolve_device()` / `gpu_health()` probes;
  `MEDIASCRIBE_MAX_WORKERS` env var.  **Current naming:** use
  `MEDIASCRIBE_JOB_EXECUTORS` for concurrent Web batches and
  `MEDIASCRIBE_BATCH_CONCURRENCY` for in-batch work.  The old name remains a
  fallback for each when its replacement is unset, so setting only the legacy
  name can scale both limits.
* **v3.2.0c** — `@profile_step` decorator, UI GPU pill,
  cancel WebSocket bridge, hidden memory-leak fixes.
* **v3.2.0d** — LLM post-processing skeleton
  (`llm_post_process.py`), 10-Whisper-model selection, 16 real
  Douyin videos transcribed.
* **v3.2.0e** — Stage-level cancel mechanism (`PipelineCancelled`
  + `raise_if_cancelled()` in every `Stage.run()`); GPU
  VRAM-aware concurrency (`_gpu_aware_concurrency` caps
  `max_concurrent` by `free_vram // vram_per_task_mb` on CUDA);
  `_GpuHealthCache` TTL cache (5 s, thread-safe). New env var
  `MEDIASCRIBE_VRAM_PER_TASK_MB` (default 3000).
* **v3.2.0f** — Production hardening sweep:
  SSRF protocol blacklist expanded (browser/script schemes:
  `javascript:`, `vbscript:`, `blob:`, `view-source:`, etc.);
  Web `Pipeline` cache protected by `_PIPELINE_CACHE_LOCK`
  (thread-safe FIFO eviction, per-request isolated `Settings`);
  atomic write `_atomic_write_text` helper across `pipeline.py`,
  `pipeline_stages.py`, `transcribers/chunked.py`, `learn.py`,
  `cache.py` (tmp name carries PID + UUID8 to avoid concurrent
  collisions, failure-cleanup on exception); FFmpeg audio
  extraction `subprocess.run` now honours
  `MEDIASCRIBE_FFMPEG_TIMEOUT` (default 600 s, logs on invalid
  env value); MCP batch transcribe subprocess gains
  `MEDIASCRIBE_BATCH_TIMEOUT` (default 1800 s) with structured
  `TimeoutExpired` response; new `_atomic_write_text` unit tests
  (success / failure-cleanup / parent-dir / atomic-replace).

### v2.x — Bilingual output + WeChat MP hardening

* Bilingual subtitle rendering for WeChat video messages.
* Concurrent OCR (`ThreadPoolExecutor`, up to 4 workers) for
  WeChat image articles.
* `easyocr.Reader` cached module-level (~3 s saved per image).
* Detector fixes for `/s/abc` and `/s?__biz=` URL shapes.
* Real-URL E2E suite (opt-in via `MEDIASCRIBE_E2E=1`).
* WhisperX support with auto-fallback to `faster-whisper`
  then `whisper`.  **Historical / superseded:** current automatic selection
  is `faster-whisper` → `whisper`; WhisperX is explicitly opt-in and the
  factory has no fallback chain.

### v1.x — Initial 6-platform support

* Bilibili, Douyin, YouTube, Xiaohongshu, WeChat MP, TikTok.
* Three engines with manual selection.
* CLI + MCP server (initial).
* 200+ tests, ruff-clean codebase.
