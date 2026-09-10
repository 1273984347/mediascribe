# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.3.0] - 2026-09-10

### Fixed (engineering / packaging)

- **Docker image build** (P1-5): `Dockerfile` now copies `douyin_batch/` and
  `douyin_batch_v3.py` into the build context so `pip install .` no longer
  fails with "package directory 'douyin_batch' does not exist".
- **Wheel contents & entry points** (P1-6): `pyproject.toml` now packages
  `video2text.web`, `video2text.plugins` and the `examples.plugins` entry
  point targets, and declares `douyin_batch_v3` as a top-level py-module, so
  `video2text-batch` and the vimeo/tcn plugin entry points resolve after
  install. Verified with `pip wheel` + unpack inspection.
- **`openai` dependency declared** (P1-3): new `llm` extra
  (`openai>=1.0.0`) for `video2text.llm_post_process`; included in `all`.
- **`otel` extra** (P2-12): `opentelemetry-sdk>=1.20` for the optional
  OpenTelemetry export backend in `video2text.observability`.
- **`make lint` / `make format`** (P1-4): now run `ruff check .` /
  `ruff format` instead of the uninstallable flake8+black combo; dead
  `BLACK`/`ISORT`/`FLAKE8` variables removed; `test-i18n`/`test-cross`/
  `test-imports`/`test-syntax` added to `.PHONY`.
- **LLM post-process defaults are safe** (P1-5): `enabled` now defaults to
  `False` and must be turned on explicitly via `VIDEO2TEXT_LLM_ENABLED`;
  `api_base` no longer silently defaults to a vendor endpoint — enabling
  without configuring it raises a clear error at `Settings` construction;
  truncation keeps the head (comment corrected) and `finish_reason=length`
  now logs a truncation warning.
- **`batch` subcommand exit code** (P1-6): partial failures now exit `1`
  instead of `0`; `--device` accepts `auto`.
- **`Settings` validation** (P1-7): engine/model validated at construction
  (`ValueError` on unknown values, aligned with `transcribers/factory.py`
  and the web layer); missing cookie files log a warning instead of failing
  silently; `VIDEO2TEXT_WORKSPACE` env is honoured (explicit argument still
  wins, env beats the `./output` default).
- **Docker Compose** (P1-7): image pinned to the local build
  (`video2text:local`) instead of a drifting `web-3.1.0` tag; `/etc/localtime`
  mount commented out with a Windows-compatibility note; comments match
  actual behaviour.
- **Packaging metadata** (P1-1): `license = {text = "MIT"}` (setuptools>=61
  compatible), classifier downgraded to *Development Status :: 4 - Beta*
  to match the `3.2.0a` pre-release.
- **requirements single-sourcing** (P2-1): `requirements.txt` is now a thin
  shim (`-e .[web,ocr,faster-whisper,mcp]`) over `pyproject.toml`;
  `requirements-dev.txt` is `-e .[dev]`.
- **CI drift** (P2-3): `bandit.yml` also triggers on `develop`;
  `release.yml` test job installs the same extras as `test.yml`;
  duplicate non-strict `build-docs` job removed from `test.yml`
  (`docs.yml` with `--strict` is authoritative); dependabot comment now
  describes the real PR matrix.
- **ruff config** (P2-4): removed `ignore` entries for rules that were never
  selected (`B008`/`UP045`/`SIM`/`B`); `E501` comment now reflects reality.
- **`.gitignore`** (P2-5): `output/` → `output/*` + `!output/.gitkeep` so
  the negation actually works; added `.ruff_cache/`, `test-ws-*/`,
  `pytest-cache-files-*/`.
- **Tracked residue removed** (P2-6/P2-7): `test_chunked_src.md`,
  `docs/lint-report.json`, `docs/lint-report.md` and the orphaned
  `douyin_batch/tests/test_skill_frontmatter.py` (asserted a `.trae/`
  directory that is not in the repo) deleted.
- **profile CLI docs** (P2-10): documented command corrected to
  `python -m video2text.profile_cli`; the CLI exits `2` with a clean
  message on missing file / bad `--since`; README no longer advertises a
  CSV output format.

### Fixed (transcribers / pipeline core)

- **`ChunkedTranscriber` interface** (P0): `transcribe` now matches the
  `Transcriber` base contract; the inner engine is called with keyword
  arguments, so chunked transcription works with every built-in engine
  instead of raising `TypeError`. Chunk windows no longer double-apply the
  overlap (each chunk boundary was re-transcribed twice), merged segments
  drop timeline-overlapping duplicates, and the temporary chunk directory is
  cleaned up in `finally` (previously leaked ~19 MB per 10 min of audio).
- **WhisperX model caching** (P1): the main model and alignment models are
  now cached module-level (double-checked lock) instead of being reloaded on
  every transcription; `clear_model_cache()` releases VRAM.
- **faster-whisper model cache LRU** (P1): `_MODEL_CACHE` is now an
  `OrderedDict` LRU capped at 2 models, so switching model/device combos no
  longer accumulates VRAM until CUDA OOM.
- **`torch.load` monkeypatch scoped** (P2): `video2text/transcribers/
  whisper.py` no longer replaces `torch.load` process-wide at import time;
  the compatibility patch now applies only around `whisper.load_model`.
- **Device resolution in the factory** (P1): `Pipeline._create_transcriber`
  now calls `resolve_device()`, so `"auto"` no longer reaches the engines
  (faster-whisper gets `int8_float16` on CUDA; WhisperX alignment no longer
  crashes on `"auto"`).
- **Process-wide GPU semaphore** (P1): GPU concurrency is now shared across
  all `AsyncPipeline` batches, so two concurrent batch jobs can no longer
  double the GPU task count into a CUDA OOM.
- **Cancellation semantics** (P1): cancelling in-flight tasks converts
  `CancelledError` into `_FailedResult` instead of breaking the `gather`
  contract; the cancel event is cleared per `run_batch` and is checked
  cooperatively between stages; `Pipeline.transcribe` accepts
  `cancel_event`.
- **Download cache wiring** (P2): `PersistentDownloadCache` is now actually
  used by `DownloadStage` when `VIDEO2TEXT_DOWNLOAD_CACHE=1` (hit = reuse
  cached file, miss = download + store; off by default).
- **Cache hardening** (P2): cache index is persisted on write/close instead
  of on every hit; file copies are atomic (tmp + `os.replace`), so
  half-written files can no longer be cached as valid; `PersistentChunkCache`
  gained TTL pruning and an LRU entry cap.
- **Audio utilities** (P2): `extract_audio` output names carry a
  path+size+mtime fingerprint so concurrent same-title jobs no longer
  overwrite each other's wav (failures clean up partial files); VAD now
  streams the PCM instead of loading the whole audio into memory.
- **Profile timing isolation** (P2): step timings are contextvars-scoped
  per run, so concurrent `AsyncPipeline` runs no longer clobber each
  other's `STEP_TIMES`.
- **Misc** (P2): download fallback chain deduplicated into
  `_smart_pick_downloader`; `engine_name` declared on `PipelineContext`;
  critical stage-chain `assert`s became real exceptions; `VIDEO2TEXT_CUSTOM_TERMS`
  JSON errors are logged instead of swallowed; active span tracking is
  contextvars-based (concurrency-safe).

### Fixed (web / MCP security)

- **WebSocket authentication** (P1): `/ws/progress/{job_id}` now validates
  the shared token (query `?token=` or `Sec-WebSocket-Protocol`) before
  accept, closing the unauthenticated read/cancel bypass; the built-in UI
  gained an API-token field (persisted in `localStorage`) and sends Bearer
  headers + the WS token.
- **SSRF hardening** (P1): submitted URLs must resolve to public addresses
  (private/loopback/link-local/reserved are rejected fail-closed; userinfo
  tricks rejected); `VIDEO2TEXT_ALLOWED_HOSTS` opt-out for local testing.
- **Local-file exfiltration closed** (P1): the web API no longer accepts
  arbitrary local media paths — only URLs, or paths inside the workspace.
- **MCP HTTP transport** (P1): optional `VIDEO2TEXT_MCP_TOKEN` (401),
  Host allow-list (403, anti DNS-rebinding), 1 MiB body cap (413), malformed
  JSON-RPC no longer kills the stdio loop, `max_videos` clamped to schema.
- **Rate limiter** (P2): expired buckets are swept globally (60 s interval);
  docstring matches behaviour and documents the `--proxy-headers` caveat.
- **Hardening sweep** (P2): `file://` CORS origin removed; JSON endpoints
  reject non-`application/json` content types (415); batch thread no longer
  swallows `BaseException`; internal exception details are logged, not
  echoed; duplicate URLs are deduplicated on submit; WS sessions get max
  duration + idle timeouts via a per-job event bridge (no more 0.5 s
  thread-pool polling per connection); progress events queue is bounded;
  generated INSTALL.md uses `VIDEO2TEXT_PUBLIC_BASE_URL` or a sanitised
  request origin.

### Fixed (downloaders / douyin_batch)

- **Douyin downloader** (P0): missing Playwright now returns `(None, None)`
  and falls back to yt-dlp instead of crashing on tuple unpacking.
- **`--config` entry point** (P0): `BatchConfig` gained `user_url`, so
  config-file-driven runs work as documented.
- **Config contract** (P1): env prefix corrected to `DOUYIN_BATCH_*` (old
  `DOYIN_BATCH_*` still read with a deprecation warning); `TranscriberPool`
  now honours `whisper_model`/`language` from the config; `workers > 1`
  actually parallelises with a thread pool; `scroll_pause`/
  `max_scroll_rounds` wired into the browser session.
- **Resume semantics** (P1): failed videos are no longer marked processed
  (they retry on the next run) and their audio files survive cleanup.
- **WeChat MP extractor** (P1): `js_content` extraction balances nested
  `<div>`s instead of truncating at the first inner `</div>` (silently
  dropping the rest of the article).
- **Downloader convergence** (P1): new shared `downloaders/_http_download.py`
  (retrying session, `.part` temp file, failure cleanup) used by douyin /
  xiaohongshu / wechat_mp; xiaohongshu image notes fail with a clear
  message instead of feeding a `.jpg` to ffmpeg.
- **Hygiene** (P2): PaddleOCR reader cached (was re-initialised per image);
  uuid-suffixed filenames prevent same-second collisions; library prints →
  logger (stdout stays clean under `--json`); batch cache writes are atomic
  with corruption quarantine; version sourced from
  `douyin_batch.__version__`; yt-dlp fetches metadata once with throttled
  progress hooks; ffmpeg merge failures log stderr; `BrowserManager` no
  longer launches a browser on cache-hit shutdown; deprecated
  `locale.getdefaultlocale()` replaced; report filenames ASCII-fied for the
  i18n CLI; short-link host matching uses parsed hostname (closing the
  `http://b23.tv@127.0.0.1/` bypass).


## [3.1.0] - 2026-06-04

### Added
- **Browser Side Panel support**
  ([manifest.json](file:///d:/1/video2text/extension/manifest.json),
  [background.js](file:///d:/1/video2text/extension/background.js),
  [popup.html](file:///d:/1/video2text/extension/popup.html),
  [popup.js](file:///d:/1/video2text/extension/popup.js),
  [popup.css](file:///d:/1/video2text/extension/popup.css)):
  the Chrome / Edge extension now opens the same UI in the browser
  Side Panel (``chrome.sidePanel``) as in the toolbar popup.
  ``manifest.json`` declares ``sidePanel`` permission and
  ``side_panel.default_path = "popup.html"`` so users can pin the
  panel to the right edge of the window for hands-free transcript
  reading while the page scrolls on the left.
  - The popup page detects which surface it is on via
    ``chrome.sidePanel`` presence and applies a wider layout for
    the panel (``body.is-side-panel``).
  - The popup shows a new "Open in side panel" button that calls
    ``chrome.sidePanel.open`` locally, with a message-bus fallback
    through the background worker for older Chromium builds.
  - ``background.js`` configures the side panel on install, falls
    back to opening the panel when the toolbar icon is clicked with
    no popup, and exposes a ``v2t/openSidePanel`` message handler.
  - Styles are extracted into ``popup.css`` with light/dark theme
    variables and ``prefers-color-scheme`` support so both surfaces
    look identical.
  - ``minimum_chrome_version`` bumped to ``114`` (the Side Panel
    API shipping version).
  - 31 new tests in
    [test_extension_side_panel.py](file:///d:/1/video2text/douyin_batch/tests/test_extension_side_panel.py)
    cover the manifest contract, HTML element wiring, JS function
    presence, and ``node --check`` syntax validation.
- **One-click extension ZIP download**
  ([extension_builder.py](file:///d:/1/video2text/video2text/web/extension_builder.py),
  [web/app.py](file:///d:/1/video2text/video2text/web/app.py),
  [static/index.html](file:///d:/1/video2text/video2text/web/static/index.html)):
  the Web UI now lets end users grab a self-contained ZIP of the
  ``extension/`` tree without leaving the browser, so they can
  ``chrome://extensions`` → "Load unpacked" on Chrome / Edge
  without cloning the repo.  Three new endpoints:
  - ``GET /extension`` — dark-theme landing page that mirrors the
    README install steps and links to the ZIP / install doc.
  - ``GET /api/extension/download`` — streams the ZIP with
    ``Content-Disposition: attachment``,
    ``X-Content-Type-Options: nosniff`` and
    ``Cache-Control: no-store`` so a misclick never caches a stale
    build.
  - ``GET /api/extension/install.md`` — same steps as
    Markdown, served as ``text/markdown`` when ``?raw=1`` is set
    and as a styled HTML page otherwise.
  ``build_extension_zip`` refuses path-traversal entries (any
  segment containing ``..``, blank names, absolute paths) and
  skips ``__pycache__`` / ``.pyc`` / ``.swp`` / ``.tmp`` / ``.bak``;
  per-file size is capped at 5 MiB.  An ``INSTALL.md`` with the
  real Web UI origin is injected at build time so users do not
  have to edit the manifest.  A footer link in the Web UI
  surfaces the new page next to ``/api/health``.  24 new tests
  in
  [test_extension_download.py](file:///d:/1/video2text/douyin_batch/tests/test_extension_download.py)
  cover ZIP contents, path-traversal rejection, endpoint
  responses and rate-limit interaction.

### Security
- **Docker hardening** ([Dockerfile](file:///d:/1/video2text/Dockerfile)):
  the runtime image now runs as a dedicated non-root user
  (``video2text``, uid 1000), declares an inline ``HEALTHCHECK``
  that probes ``/api/health`` every 30 s, and pip uses
  ``--no-cache-dir`` in the builder.  A subtle bug in the previous
  ``chmod 777 /workspace`` was also fixed: the directory is now
  owned by the service account from the start.
- **SourceRef typing** ([models.py](file:///d:/1/video2text/video2text/models.py)):
  introduced ``SourceKind = Literal[...]`` and the
  ``is_known_kind`` property so downloader dispatch tables can
  rely on a closed set without breaking on unknown URLs.
- **Web app rate limiting** ([web/app.py](file:///d:/1/video2text/video2text/web/app.py)):
  ``/api/transcribe`` is now protected by a per-client sliding-window
  rate limiter (default 10 requests / 60 s, configurable via
  ``VIDEO2TEXT_RATE_LIMIT`` and ``VIDEO2TEXT_RATE_LIMIT_WINDOW``,
  disable with ``=0``).  Rejected requests return ``HTTP 429`` with
  ``Retry-After`` and ``X-RateLimit-*`` headers (RFC 6585).
  ``/api/health`` reports the active configuration so dashboards
  can surface a banner.

### Fixed
- **MCP `transcribe_video` tool** ([mcp_server.py](file:///d:/1/video2text/video2text/mcp_server.py))
  used an invented ``Pipeline(source=..., language=..., output_dir=..., whisper_model=...)``
  constructor and a non-existent ``Pipeline.run()`` method.  The tool
  now builds a real ``Settings``, instantiates ``Pipeline``, and
  calls ``Pipeline.transcribe(source_input=...)``.  Three new tests
  in ``test_mcp_server.py`` lock the contract in place.
- **MCP `get_transcript` path-traversal** ([mcp_server.py](file:///d:/1/video2text/video2text/mcp_server.py)):
  any caller could ask for ``/etc/passwd`` or ``~/.ssh/id_rsa``.
  The tool now pins reads to a configurable
  ``VIDEO2TEXT_TRANSCRIPT_ROOT`` (default ``./output/transcripts``),
  refuses symlink escapes via ``Path.relative_to``, blocks files
  larger than 5 MiB, and rejects non-UTF-8 bytes.
- **Web app auth + CORS** ([web/app.py](file:///d:/1/video2text/video2text/web/app.py)):
  ``/api/transcribe`` now requires a ``Bearer`` token when
  ``VIDEO2TEXT_API_TOKEN`` is set (constant-time comparison via
  ``hmac.compare_digest``).  CORS middleware pre-allows
  ``chrome-extension://<id>``, ``moz-extension://<id>``,
  ``file://``, ``http(s)://(localhost|127.0.0.1|host.docker.internal)``
  via a regex matcher, with the explicit ``VIDEO2TEXT_CORS_ORIGINS``
  env var to override.
- **Flaky OCR partial-success test**:
  ``test_ocr_images_partial`` asserted an order-dependent list from
  a ``ThreadPoolExecutor``.  Replaced with ``assertCountEqual``.
- **Web app job-id collision risk**: ``__import__('time').time()`` +
  ``abs(hash(url)) % 10**8`` replaced with top-level ``time`` +
  ``uuid.uuid4().hex[:8]``.
- **`safe_stem` Windows-hostile regex** ([inputs.py](file:///d:/1/video2text/video2text/inputs.py)):
  the previous ``re.sub(r'[^\w\-_]', '_', name)`` left Windows
  reserved names (``CON``, ``PRN``, ``COM1``…) and control characters
  untouched, which could crash downloads on titles like
  ``report:2024`` or ``v?.mp4``.  ``safe_stem`` now delegates to
  ``douyin_batch.platform_compat.safe_filename`` so the same
  sanitisation rules apply across the CLI, the Web UI and the
  MCP ``safe_filename`` tool.

### CI
- **Dependabot** ([.github/dependabot.yml](file:///d:/1/video2text/.github/dependabot.yml)):
  weekly PRs for ``pip``, ``github-actions`` and ``docker``
  ecosystems, with grouped bumps for the ``whisper``, ``media``
  and ``ml`` dependency stacks so the queue stays reviewable.
- **Bandit security scan** ([.github/workflows/bandit.yml](file:///d:/1/video2text/.github/workflows/bandit.yml)):
  weekly + on-PR scan of ``video2text/`` and ``douyin_batch/``
  with the test tree excluded.  Reports uploaded as build
  artifacts and printed to the workflow log; ``-ll`` threshold
  keeps the signal-to-noise ratio high.

### Added
- **One-click Web UI launcher** (`scripts/one_click_up.py` + `.sh` + `.ps1`):
  cross-platform daemon-style launcher that starts the FastAPI server
  in the background, polls `/api/health` for 30 s, opens the browser,
  and prints the browser-extension install hints.  Sub-commands
  `up` / `stop` / `status`; flags `--daemon`, `--no-browser`,
  `--mode docker`, `--reload`, `--host`, `--port`.  PID and log
  files at the project root (`.one-click.pid`, `.one-click.log`).
- **Docker support** (`Dockerfile` + `docker-compose.yml`): multi-stage
  build (`python:3.11-slim` + ffmpeg + tini) that ships a small
  runtime image.  Compose service exposes port 8000, mounts
  `./web-workspace` for transcript persistence, and includes an
  HTTP healthcheck.  Optional GPU passthrough via the commented
  `deploy.resources` block.
- **Make / just convenience targets** (`Makefile` + `justfile`):
  `up`, `up-daemon`, `down`, `status`, `logs`, `docker-up` — each
  delegates to the Python launcher.
- **Browser extension Docker host permission**: `extension/manifest.json`
  now lists `http://host.docker.internal/*` so the extension can
  reach a containerised Web UI from the host browser.
- **One-click launch docs** (`docs_site/one-click.md`): TL;DR for
  Linux/macOS, Windows and Docker; full lifecycle command table;
  Docker specifics; troubleshooting matrix.  Linked from the
  mkdocs nav.
- **One-click tests** (`douyin_batch/tests/test_one_click.py`,
  17 cases): launcher module API, sub-commands, idempotent
  stop, shell wrapper existence + AST parse, PowerShell wrapper
  parse, Docker artefacts, Makefile / justfile targets, extension
  manifest, mkdocs nav, documentation page.
- **Plugin system** (`video2text.plugins`): third-party `Downloader` /
  `Transcriber` / `URLTransformer` registration via `entry_points`.
  Includes hookspecs, an in-tree example (`examples/plugins/`), and
  a `clear_cache()` helper for tests.
- **Long-video chunking** (`video2text.transcribers.chunked`):
  `ChunkedTranscriber` splits long audio into overlapping windows
  (default 600 s + 5 s overlap) and merges per-chunk segments with
  global timestamps.  Includes `probe_duration`, `split_audio`,
  `merge_texts`, `merge_segments` helpers.
- **Performance utilities** (`video2text.performance`):
  `@profile_step` decorator, `PerformanceReport` (with
  mean / min / max / total per step), `parallel_map` (thread pool
  mapper), and `DownloadCache` (URL → path single-run cache).
- **Observability mini-SDK** (`video2text.observability`):
  zero-dependency OpenTelemetry-compatible `Tracer` / `Meter`
  that records to an in-memory `OBSERVABILITY` dict.  One call
  (`install_opentelemetry_exporter`) upgrades to the real
  `opentelemetry-sdk` when available.
- **Browser extension** (`extension/`): Chrome / Edge Manifest v3
  popup that POSTs the active tab URL to a self-hosted Web UI.
  Bundled icons (16/48/128), options page, service worker.
- **Web UI** (`video2text.web`): FastAPI + vanilla HTML app
  exposed via `python -m video2text.web.app`.  Pydantic-validated
  `/api/transcribe` endpoint.  Pyproject `[web]` extra.
- **E2E recording script** (`scripts/capture_e2e_recording.py`):
  bundles real-URL E2E + WeChat MP E2E into a self-contained
  timestamped package with a top-level `README.md` and
  `SUMMARY.json`, ready to attach to a GitHub release.
- **mkdocs documentation site** (`docs_site/` + `mkdocs.yml`):
  18 pages including getting-started, platforms, engines,
  plugins, WeChat MP, web UI, browser extension, MCP server,
  benchmarking, chunking, performance, observability, E2E
  testing, CI matrix, changelog, contributing, license.  Material
  theme with light/dark palette toggle.  GitHub Pages workflow
  in `.github/workflows/docs.yml`.
- **CI matrix** (`.github/workflows/test.yml`): 5 Python × 3 OS
  = 15 cells.  Separate `lint`, `coverage`, `build-docs` jobs
  with concurrency cancellation.  System ffmpeg install step per OS.
- **Three SKILL.md files** under `.trae/skills/` for agent
  integration: `video2text`, `video2text-wechat`, `video2text-benchmark`.

### Fixed
- WeChat MP detector now accepts both `/s?__biz=` and `/s/<id>?__biz=`
  URL forms (the latter is the more common in-the-wild shape).

### Tests
- 240+ tests passing (was 232).  New test files:
  - `test_wechat_mp_detector.py` (4 tests)
  - `test_benchmark_transcribers.py` (5 tests)
  - `test_web_app.py` (4 tests)
  - `test_extension_manifest.py` (6 tests)
  - `test_skill_frontmatter.py` (3 tests)
  - `test_plugin_system.py` (10 tests)
  - `test_chunked_transcriber.py` (5 tests)
  - `test_performance_utilities.py` (9 tests)
  - `test_observability.py` (8 tests)
  - `test_docs_site.py` (11 tests)

## [3.2.0a] - 2026-06-06

> **Alpha milestone** — Tier 2 polish for v3.1.0.  Four new subsystems land
> behind a single `--mode` switch and stay disabled-by-default at the
> CLI / Web level.  No breaking API changes.  Test count jumps from
> **473 → 612 passed (+139 new)**, 9 skipped, 0 failed, ruff clean.

### Added
- **VAD-based long-video chunking**
  ([audio_utils.py](file:///d:/1/video2text/video2text/audio_utils.py),
  [transcribers/chunked.py](file:///d:/1/video2text/video2text/transcribers/chunked.py)):
  chunk splits are now driven by `webrtcvad` voice-activity detection
  when the optional dependency is installed.  Audio is scanned in
  20 ms frames at mode-3 aggressiveness; chunks are sealed on the
  first non-speech window of at least 700 ms, guaranteeing a real
  breath or sentence boundary instead of a hard 30 s cut.  A 5 s
  overlap is retained at the chunk edge so ASR boundary effects
  do not eat the first/last word.  When `webrtcvad` is not
  importable the code falls back to the legacy fixed-duration
  splitter, so existing callers are unaffected.  20 new tests in
  [test_vad_chunking.py](file:///d:/1/video2text/douyin_batch/tests/test_vad_chunking.py)
  cover the activation flag, silence/speech mix, fallback path,
  overlap stitching and PCM16 round-trip.
- **Cross-run persistent cache**
  ([cache.py](file:///d:/1/video2text/video2text/cache.py)):
  downloads and transcriptions are now memoised to disk across
  processes, not just within a single Python run.  Layout follows
  the XDG Base Directory spec — the cache lives at
  ``$XDG_CACHE_HOME/video2text`` (or ``~/.cache/video2text`` on
  Linux, ``%LOCALAPPDATA%\video2text\cache`` on Windows, and
  ``~/Library/Caches/video2text`` on macOS) and is overridable via
  the ``VIDEO2TEXT_CACHE_DIR`` environment variable.  Each entry is
  a SHA-256 of `(platform, url, params)` + a per-key JSON payload,
  with a single index file per category (``downloads.json`` /
  ``transcripts.json``) for O(1) lookup.  An LRU cap (default 1 GiB
  / 4096 entries) and a 30-day TTL prune are enforced on every
  write; ``Cache.prune()`` is exposed so cron / CI can garbage
  collect manually.  Stale or corrupt index files are detected via
  a monotonic schema version and silently rebuilt.  21 new tests
  in
  [test_persistent_cache.py](file:///d:/1/video2text/douyin_batch/tests/test_persistent_cache.py)
  cover XDG fallback, env override, LRU eviction at zero cap,
  TTL=0 semantics, OSError on ``_save_index``, and 4 KB
  payload round-trip.
- **`profile` CLI sub-command**
  ([__main__.py](file:///d:/1/video2text/video2text/__main__.py),
  [profile_cli.py](file:///d:/1/video2text/video2text/profile_cli.py)):
  ``python -m video2text profile <run.jsonl>`` aggregates pipeline
  profiling JSONL output into a Markdown report, a JSON summary,
  or a CSV — the 3-axis breakdown (stage / event / percentiles)
  is identical to the Web UI.  The dispatcher preserves the v3.1.0
  ``transcribe`` / ``batch`` entry points verbatim, so existing
  scripts and CI pipelines are not touched.  New flags:
  ``--top N`` (default 10) limits the slowest-events table,
  ``--since ISO`` filters records older than the given
  timestamp, ``--format {md,json,csv}`` switches the output
  format, and ``--tz-aware`` normalises naive timestamps to
  UTC before comparison.  26 new tests in
  [test_profile_cli.py](file:///d:/1/video2text/douyin_batch/tests/test_profile_cli.py)
  cover parse-error fallback, percentile math, CSV escaping,
  timezone-aware filtering, top-N clamping to 0, and exit codes
  on empty input.
- **WebSocket real-time job progress**
  ([progress.py](file:///d:/1/video2text/video2text/progress.py),
  [web/app.py](file:///d:/1/video2text/video2text/web/app.py)):
  long-running ``/api/transcribe`` and ``/api/batch`` jobs now
  publish a 3-bar progress event (download / transcribe /
  assemble) that the dashboard can stream without polling.  The
  new ``ProgressRegistry`` lives on ``app.state.jobs`` and exposes
  ``create(url)``, ``get(job_id)``, ``cancel(job_id)``,
  ``list()`` and ``purge(max_age_seconds)``.  Three new endpoints
  wire it into FastAPI:
  - ``GET  /api/jobs/{job_id}`` — JSON snapshot of stage
    percentages, current event name, started / updated / finished
    timestamps and final result.
  - ``POST /api/jobs/{job_id}/cancel`` — cooperative
    cancellation; the worker checks the flag between chunk
    boundaries so partial chunks are not orphaned on disk.
    Unknown ``job_id`` returns ``404`` (not 200 + ``cancelled=False``).
  - ``WS   /ws/progress/{job_id}`` — bi-directional stream that
    pushes a snapshot on connect and then a ``progress`` /
    ``completed`` / ``cancelled`` / ``error`` event for every
    stage update.  Uses ``asyncio.to_thread`` to bridge the
    background thread that owns the queue, so the event loop
    never blocks on a ``queue.get()``.  Unknown ``job_id``
    triggers a 4404 close frame with an ``error`` payload so the
    client can show a clean message.
  19 new tests across
  [test_job_progress.py](file:///d:/1/video2text/douyin_batch/tests/test_job_progress.py)
  and
  [test_web_app_security.py](file:///d:/1/video2text/douyin_batch/tests/test_web_app_security.py)
  cover registry lifecycle, cancellation between stages, WS
  snapshot replay, 4404 on unknown id, and the 404 / 200
  semantics of the REST helpers.

### Changed
- **`__main__.py` refactored into a dispatch shim**
  ([__main__.py](file:///d:/1/video2text/video2text/__main__.py)):
  the entry point now parses the first positional argument and
  routes to ``_run_legacy`` (``transcribe`` / ``batch`` /
  ``-h``) or to the new ``profile`` sub-command.  All v3.1.0
  CLI flags and exit codes are preserved bit-for-bit, so
  ``python -m video2text --help`` still exits 0 with the same
  argparse output, and ``python -m video2text batch ...``
  behaves exactly as before.

### Test infrastructure
- **Coverage gap round 3**
  ([test_coverage_gaps_3.py](file:///d:/1/video2text/douyin_batch/tests/test_coverage_gaps_3.py)):
  16 new tests target the 0%-or-low-% branches surfaced by the
  v3.2.0a code review — corrupted index recovery, TTL = 0
  short-circuit, LRU eviction with cap = 0, OSError handling in
  ``_save_index``, ``_parse_iso`` invalid input fallback,
  unparseable-timestamp passthrough, ``--top 0`` rendering,
  timezone-aware vs naive filtering, WebSocket unknown-id close,
  WebSocket snapshot replay, REST 404 / 200 semantics and
  ``purge`` cut-off behaviour.  Result: ``cache.py`` 92% → 94%,
  ``profile_cli.py`` 93% → 97%, ``web/app.py`` 71% → 82%,
  ``progress.py`` 100%.

### Quality gate
```
612 passed, 9 skipped in 213.52s
ruff check .                            # 0 errors
coverage report -m (video2text scope)   # 75% overall, all v3.2.0a modules ≥ 89%
```

## [Unreleased]

### Added
- **AI agent compatibility**: `AGENTS.md`, `CLAUDE.md`, `.cursorrules`,
  `.clinerules`, `.windsurfrules`, `.github/copilot-instructions.md`,
  `.cody.yml`, `.continue/config.json`, `.aider.conf.yml`, `.traerules`
- **MCP (Model Context Protocol) server**: `python -m video2text.mcp_server`
  with 8 tools (`transcribe_video`, `batch_transcribe_creator`,
  `get_cache_stats`, `validate_url`, `sanitize_filename`, `detect_platform`,
  `get_transcript`, `transcribe_wechat_mp`) over stdio or HTTP
- **`--json` output mode** for `douyin_batch_v3.py` with stable schema
  `video2text.agent-output/v1` (see `douyin_batch/agent_output.py`)
- **`--bilingual-json` flag** to attach localised human labels alongside
  stable English constants (`platform_label`, `status_label`, `stage_label`)
- **`--platform` filter** on `douyin_batch_v3.py` (repeatable, e.g.
  `--platform youtube --platform wechat_mp`) to restrict the batch to
  a whitelist of platforms; non-matching videos return `status=skipped`
- **`Makefile`** + **`justfile`** with agent-friendly targets:
  `make install`, `make test`, `make lint`, `make format`, `make verify`
- Bilingual (English / Chinese) UI for `douyin_batch_v3.py` via the `i18n` module
- New `--lang {en,zh}` CLI flag with auto-detect default
- `douyin_batch/security.py` — URL/filename safety utilities
- `douyin_batch/platform_compat.py` — cross-platform helpers (FFmpeg, paths, filenames)
- Cross-platform unit tests (Windows / macOS / Linux path handling, ffmpeg detection)
- `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `.editorconfig`, `.gitignore`
- GitHub Actions CI matrix (Python 3.8–3.12 on Ubuntu / macOS / Windows)
- Issue & pull-request templates
- **YouTube downloader** (`YouTubeDownloader`) — wraps `yt-dlp` with tuned
  `player_client` rotation (web_safari / ios / android / web_embedded) to
  dodge 403 / SABR / login walls
- **Xiaohongshu downloader** (`XiaohongshuDownloader`) — uses Playwright to
  extract real media URLs from explore / discovery / xhslink short links;
  handles video notes and image notes
- **WeChat MP downloader** (`WechatMpDownloader`) — pulls `mp.weixin.qq.com`
  articles, extracts `#js_content` text and meta, and (for video messages)
  downloads the embedded mp4. Text articles skip ASR; video messages go
  through the standard pipeline
  - 3-mode dispatcher: text article, video message, image article
  - **OCR engine fallback chain** (`paddleocr` → `pytesseract` → `easyocr`)
    with `ocr_engine` / `ocr_lang` / `save_images` options
  - 3-tier cookie injection (dict > file > env var)
  - **Bilingual subtitle output** for video messages when
    `Pipeline.transcribe(..., bilingual=True)` is set
- **`DOWNLOADERS.md`** — per-platform reference, fallback chain, and
  instructions for adding new downloaders
- **`*_label_i18n_lang` markers** in `AgentOutput` JSON to identify the
  locale used for each `*_label` field, plus a top-level `i18n_lang`
  marker on the whole document
- **`wechat_mp_status`** (success / partial) on the metadata to distinguish
  fully-extracted text articles from those with partial OCR failures
- **OCR engine selection** in `transcribe_wechat_mp` MCP tool:
  `ocr_engine` enum (auto / paddleocr / pytesseract / easyocr),
  `ocr_lang` (default `chi_sim+eng`), `save_images` flag
- **Ruff lint config** in `pyproject.toml` (replaces ad-hoc checks);
  `make lint` and `make verify` now run ruff with 0-error policy
- **Stage cancel mechanism (v3.2.0e)**: `PipelineCancelled` exception +
  `PipelineContext.raise_if_cancelled()` — each `Stage.run()` checks
  `cancel_event` before/after blocking operations (download / ffmpeg / ASR /
  LLM post-process / disk write); `run_with_progress` skips 100% emit on
  cancel. `AsyncPipeline.run_batch` wraps cancelled tasks into
  `_FailedResult(src, PipelineCancelled)` for caller-side distinction
- **GPU VRAM-aware concurrency (v3.2.0e)**: `_gpu_aware_concurrency()` caps
  `max_concurrent` by `free_vram // vram_per_task_mb` on CUDA; falls back to
  `base` on CPU / metal / unknown VRAM. `_GpuHealthCache` TTL cache (5s,
  thread-safe) avoids re-probing `gpu_health()` per batch.
  New env var `VIDEO2TEXT_VRAM_PER_TASK_MB` (default 3000, based on large-v3
  ~5GB / medium ~5GB / small ~2GB)

### Changed
- `douyin_batch_v3.py` is now fully i18n-aware (CLI help, banner, status messages)
- README is bilingual; English-first while preserving the Chinese section
- `pyproject.toml` updated with optional `whisperx` / `faster-whisper` extras
- `requirements.txt` and `requirements-dev.txt` aligned
- `Logger.set_quiet()` for clean `--json` mode output
- `AgentOutput._apply_labels` accepts a `lang` parameter; if given, the
  global i18n language is temporarily switched so the rendered labels
  actually match that locale (then restored)
- `Pipeline.transcribe` accepts a new `bilingual` keyword for WeChat MP
  video messages; the markdown output and JSON metadata carry i18n labels
- `process_single_video_safe` accepts a `platform_filter` list

### Fixed
- Hard-coded Chinese strings in `douyin_batch_v3.py` replaced with `t()` calls
- `safe_filename()` strips control chars and Windows reserved names consistently
- MCP server's stdio parser now handles UTF-8 BOM (some agents add it)
- `Pipeline._get_downloader` fallback chain ensures every platform gets
  a working downloader even if its dedicated class fails to import
- `Settings.wechat_cookies_file` is now properly stored as a `Path` after
  the CLI parses `--wechat-cookie-file`

### Tests
- 38 basic unit tests
- 14 agent-output tests (JSON schema, Unicode, save/load, etc.)
- 15 MCP server tests (JSON-RPC, stdio round-trip, tool dispatch, errors)
- 21 new downloader tests (YouTube / Xiaohongshu routing, URL detection, CDN regex)
- 20 pipeline router tests (fallback chain, WeChat MP text/video split)
- 33 i18n + cookies + MCP wechat_mp tests (platform labels, status/stage
  constants, cookie parsing, env injection, MCP tool dispatch)
- 29 bilingual + OCR + CLI tests (i18n bilingual mode, image extraction,
  OCR graceful degradation, WeChat CLI flags, platform detection)
- 20 round-5 tests (ocr_engine schema, video-article bilingual markdown,
  --platform filter, *_label_i18n_lang markers, WeChat MP download options)
- **190 tests total, all passing in ≈ 21 s**
- **Lint**: 0 ruff errors / 71 files compile / 0 syntax issues

### Tests (coverage补齐)
- 49 tests in `test_coverage_gaps.py` covering previously-untested branches:
  - `url_utils.extract_bvid` / `is_short_url` / `resolve_short_url` (incl.
    HTTP 403 fallback to identity), `normalize_bilibili_url`, `normalize_url`
  - `inputs.is_audio_file` / `is_video_file` / `safe_stem`
  - `chunked.merge_texts` / `merge_segments` / `probe_duration` WAV happy
    and failure paths
  - `performance.to_markdown` (empty + with steps),
    `PerformanceReport.write`, `parallel_map` (empty / ordered / errors),
    `DownloadCache` round-trip
  - `observability.set_status` / `record_exception`, nested spans sharing
    a `trace_id`, `meter.counter` / `meter.histogram`,
    `install_opentelemetry_exporter` unavailable returns `False`
  - Web app `/api/extension/install.md` HTML and raw markdown responses
  - `cache.ProcessCache` corrupt JSON, `mark` / `filter` / `user_videos` /
    `clear`
  - `retry.retry` with `max_retries=0`, 3-sleep backoff sequence
    `[0.1, 0.2, 0.4]`, `on_retry` callback, unrelated exception not
    retried
  - `security.safe_join_path` path-traversal attempts, `validate_video_id`,
    `check_url_safety`, `sanitize_filename` edge inputs
  - `platform_compat.safe_filename` / `normalize_path` / `check_ffmpeg` /
    `get_os` / `check_python_version`
- 33 tests in `test_coverage_gaps_2.py` filling deeper gaps:
  - `models.SourceRef.display_name` (three fallbacks), `is_known_kind`,
    `KNOWN_SOURCE_KINDS`, `TranscriptResult` defaults
  - `config.Settings.ensure_directories`, `_parse_cookie_string` (JSON and
    Netscape), `_load_cookie_file` (missing)
  - `observability.install_opentelemetry_exporter` returns `False` when
    SDK unavailable
  - `factory.get_transcriber` / fallback chain / unknown engine raises,
    `chunked.probe_duration` raises `RuntimeError` for unreadable mp3
  - `Downloader()` is abstract, `YouTubeDownloader.supports` true/false,
    `YtdlpDownloader` name + `_ydl is None`
  - `--help` exits 0; plugin registry `list_downloaders` /
    `list_transcribers` / `clear_cache`
  - i18n English/Chinese messages, format kwargs, unknown key returns
    `[key]`, `detect_language` / `init_language` / `get_language` round-trip
  - Logger singleton, `set_level` + `set_quiet`, `add_file_handler`
    auto-creates parent directory
  - `platform_compat.find_ffmpeg` returns `None` / `Path` depending on PATH
  - Retry backoff does not leak `jitter` / `delay` / `backoff` kwargs to
    wrapped function
- **82 new tests** this round. **473 tests passing, 9 skipped in ≈ 231 s**
- **Coverage**: 66% → 68% project-wide. Per-module highlights:
  - `cache.py` 84% → **100%**
  - `models.py` 91% → **100%**
  - `logger.py` 75% → 97%
  - `performance.py` 94% → 97%
  - `config.py` 95% → 97%
  - `security.py` 67% → 91%
  - `i18n.py` 92% → 93%
  - `url_utils.py` 45% → 86%
  - `inputs.py` 73% → 81%
  - `observability.py` 77% → 79%
  - `chunked.py` 73% → 77%
- **Lint**: 0 ruff errors / 0 syntax issues

### Tests (coverage round 2)
- 12 tests in `test_report_module.py` covering `report.generate_summary_report`:
  - Empty / all-success / all-failed / mixed results
  - `transcript` field is `None`, missing, or points at a non-existent file
    (each safely skipped instead of raising)
  - URL field fallback to constructed `https://www.douyin.com/video/{id}`
  - Unknown `status` counted as failure
  - Empty transcript body suppresses the "转录内容" block
  - JSON sidecar written with `ensure_ascii=False`
  - Status icons `✅` / `❌` rendered
  - Output directory created recursively if missing
- 7 tests in `test_transcribe_module.py` covering `transcribe.TranscriberPool`:
  - Singleton identity across multiple constructor calls
  - `_initialized` short-circuit (no second `Pipeline()` instantiation)
  - `__new__` returns the pre-set instance even if `__init__` is bypassed
  - `transcribe()` returns `transcript_path` on success, `None` on exception
  - Failure path prints "转录失败: …" with the underlying error
  - Module-level `transcribe_audio()` delegates to the pool, propagates
    `None` on failure
- 18 tests in `test_browser_module.py` covering `browser.py` (all
  Playwright interactions mocked, no real browser launched):
  - `BrowserManager` singleton + `new_page` / `close` lifecycle
  - `close()` swallows exceptions from `_context` / `_browser` / `_playwright`
  - `get_user_url_from_video`: matches the JSON `sec_uid` pattern, the query
    string pattern, picks the first hit across polls, returns `None` when
    no match, returns `None` on `goto` exception
  - `get_user_videos`: collects multiple unique videos, deduplicates, caps
    at `max_videos`, returns `[]` on `goto` exception, stops when no new
    videos appear in 2 consecutive rounds
  - `get_media_url_fast`: captures `media-audio.douyinvod.com` URLs,
    ignores non-audio `douyinvod.com` responses, returns `None` on no
    capture and on `goto` exception
- **37 new tests** this round. **510 tests passing, 9 skipped in ≈ 241 s**
- **Coverage**: 71.7% → **89.2%** project-wide. Per-module highlights:
  - `report.py` 10.4% → **100%**
  - `transcribe.py` 33.3% → **100%**
  - `browser.py` 11.0% → 92.4%
  - `cache.py` 100% / `models.py` 100% (retained)
- **Bug fix**: `report.generate_summary_report` previously crashed with
  `TypeError` when a successful result had `transcript=None` and with
  `PermissionError: '.'` when the `transcript` key was missing. Now skips
  such rows cleanly while still counting them in the success total.
- **Lint**: 0 ruff errors / 0 syntax issues

### Tests (coverage round 3)
- 37 tests in `test_coverage_gaps_3.py` covering the remaining <70% modules:
  - `retry.download_media_with_retry`: success first try, success after
    retries (correct sleep count), final failure returns `False` with
    "下载最终失败" print, parent directory auto-created, on_retry callback
    prints "⚠️ 第 N 次重试", empty `iter_content` chunks filtered out
  - `config.BatchConfig`: `from_env` overrides defaults for all 9 env vars,
    yes/no truthy parsing, missing env keeps defaults, `merge_cli_args`
    for num / workers / no_headless / retries, `save` creates deep parent
    dirs with `ensure_ascii=False`, `__str__` lists every field,
    `from_dict` filters unknown keys, `from_file` on missing returns
    defaults
  - `platform_compat.find_ffmpeg`: PATH hit, common-path hit, no hit,
    unknown OS
  - `platform_compat.install_ffmpeg_instructions`: Windows (gyan +
    choco + scoop), macOS (brew + port), Linux (apt + dnf + pacman),
    unknown fallback
  - `platform_compat.open_file_location`: Windows `explorer /select,`,
    macOS `open -R`, Linux `xdg-open <parent>`, subprocess failure
    returns `False`
  - `platform_compat.get_temp_dir`, `normalize_path` (`expanduser` +
    `resolve`), `get_os` branches (Linux / Darwin / Windows / unknown),
    `get_python_info` includes `ffmpeg_available`
- **37 new tests** this round. **547 tests passing, 9 skipped in ≈ 175 s**
- **Coverage**: 89.2% → **96.6%** project-wide. Per-module highlights:
  - `retry.py` 54.1% → **100%**
  - `config.py` 63.0% → **100%**
  - `platform_compat.py` 70.7% → **100%**
  - **7 modules at 100%**: `__init__`, `agent_output`, `cache`, `config`,
    `platform_compat`, `report`, `retry`, `transcribe`
- **Lint**: 0 ruff errors / 0 syntax issues

### CI (matrix split)
- Reworked [`.github/workflows/test.yml`](.github/workflows/test.yml) into
  six independent jobs that can run in parallel:
  - `lint` — ruff check + format check (≈ 15 s)
  - `unit` — py3.11 + ubuntu, skips `integration` / `network` markers
    (≈ 60 s) — this is the **PR feedback path**
  - `integration` — py3.9/3.11/3.12 × ubuntu/windows, runs only on push
    to main, tag push, manual dispatch, or nightly cron (UTC 02:00)
  - `matrix` — full 5 py × 3 OS, runs only on tag push (release gate)
    or develop merges
  - `coverage` — py3.11 + ubuntu, `--cov-fail-under=60` → `=90` to
    match the 97 % project coverage
  - `build-docs` — soft check, non-blocking
- Added `markers` declaration to `[tool.pytest.ini_options]`: `integration`,
  `network`, `slow`. Tests can opt in via `@pytest.mark.integration`.
- **PR feedback time**: from ≈ 5 min (full 5×3 matrix) to ≈ 30 s
  (lint + unit).
- Bumped coverage gate: `--cov-fail-under=60` → `=90`.

### Release (v3.1.0 tag)
- Initialised video2text as a standalone git repo (was a subdirectory of
  `D:/1`). Created branch `main` with 199 files in the first commit.
- Annotated tag `v3.1.0` pointing at the release commit.
- New files:
  - [GITHUB_RELEASE_v3.1.0.md](GITHUB_RELEASE_v3.1.0.md) — full Markdown
    release description ready to paste into GitHub Releases.
  - [PUBLISH_v3.1.0.md](PUBLISH_v3.1.0.md) — step-by-step push +
    release guide (web UI + `gh` CLI options).
  - [docs_site/roadmap-v3.2.md](docs_site/roadmap-v3.2.md) — 4-tier
    v3.2.0 plan covering VAD chunking, persistent caches, profile CLI
    透出, CI matrix split (done), WhisperX default, plugin CLI,
    marketplace page, WebSocket progress, transcript diff, and
    multi-engine consensus.
- `mkdocs.yml` adds `Roadmap v3.2` to the nav.

## [2.1.0] - 2026-05-15

### Added
- `douyin_batch` library: logger, config, cache, retry, progress, report
- Smart downloader routing (`DouyinDownloader` vs `YtDlpDownloader`) without cookies
- Markdown output with auto-segmented paragraphs and metadata header

### Changed
- Default transcription engine: OpenAI Whisper
- Output format unified to `.md` (Markdown)

## [2.0.0] - 2026-04-20

### Added
- Initial integration of yt-dlp, bili2text, WhisperX, whisper.cpp
- Support for Bilibili and Douyin platforms
- CLI entry point `python -m video2text`

### Known Limitations
- Douyin required cookies in early versions
- Markdown output added in 2.1.0

[3.3.0]: https://github.com/yourusername/video2text/compare/v2.1.0...v3.3.0
[2.1.0]: https://github.com/yourusername/video2text/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/yourusername/video2text/releases/tag/v2.0.0
