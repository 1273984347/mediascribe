# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[Unreleased]: https://github.com/yourusername/video2text/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/yourusername/video2text/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/yourusername/video2text/releases/tag/v2.0.0
