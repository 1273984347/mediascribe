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
* **CI green before review.**  Please use the repository CI matrix; it is
  the authoritative cross-platform check.

## Local development setup

```bash
git clone https://github.com/1273984347/mediascribe
cd mediascribe
python -m pip install -e ".[dev,ocr,web,mcp,faster-whisper]"
pytest tests/ -q
```

## Pull request checklist

* [ ] I have read `CONTRIBUTING.md` (this file).
* [ ] I have added or updated tests.
* [ ] `ruff check .` is clean.
* [ ] `pytest tests/ -q` is green locally.
* [ ] I have updated the docs in `docs/` if the user-facing
  behaviour changed.
* [ ] `python -m mkdocs build --strict` passes for documentation changes.
* [ ] I have checked commands, tool names, parameters, and progress / error
  semantics against the implementation rather than copying an older roadmap.
* [ ] I have not added a hard dependency without an
  `[project.optional-dependencies]` entry first.

## Implementation boundaries to preserve

* **Engine selection:** omitting `engine` prefers installed
  `faster-whisper`, then falls back to `whisper`.  `whisperx` is opt-in.
  `transcribers/factory.py` constructs one engine; it has no fallback chain.
  Resource-driven selection belongs to `runtime_guard`, and explicit user
  choices are protected.
* **Web progress and cancellation:** document the six progress identifiers
  (`preflight`, `model_prepare`, `download`, `extract_audio`,
  `transcribe`, `assemble`) and `progress_known`.  Boundary counters are not
  real intra-stage percentages.  Cancellation is a single-job
  `cancel_requested` → safe-point terminal transition; siblings continue.
* **Web artifacts and events:** open / copy / download are UI actions over
  the result and artifact routes.  `POST /api/jobs/{id}/events` records only
  content-free action metadata.  Do not document raw URLs, transcripts, or
  server paths as event payloads.
* **MCP:** `TOOL_LIST` is the tool-name / parameter source of truth.  The
  protocol normalizes exceptions and `ok: false` / `error_code` results to
  `isError: true` plus structured recovery.  Do not promise raw exception
  text, protocol-level cancellation, or a concurrent stdio server.
* **Long-video chunking:** `ChunkedTranscriber.transcribe()` takes
  `output_dir` as a keyword-only option.  Probe failure aborts; missing
  ffmpeg after a successful probe falls back to one copied chunk.  VAD and
  `PersistentChunkCache` exist in Python, but are not wired into ordinary
  CLI / Web / MCP transcription.
* **Profiling:** use `python -m mediascribe profile <timings.jsonl>` or
  `python -m mediascribe.profile_cli`.  Python callers use keyword-only
  `Pipeline(profile=..., profile_log=...)`; the ordinary entry points do not
  expose those parameters.  Do not add `--watch`, CSV, or other unsupported
  profile flags to examples.

See [Web UI](web-ui.md), [MCP server](mcp.md),
[Long videos](chunking.md), and [Performance](performance.md) for the user-facing
contracts.

## Areas that need help

* **More platforms.**  The plugin system makes adding one
  ~50 lines of code; see [Plugins](plugins.md).
* **Wire long-video VAD into a product entry point.**  VAD-aware splitting
  already exists in the Python `ChunkedTranscriber` API, but ordinary CLI,
  Web, and MCP paths do not use it.  A proposal needs explicit lifecycle,
  cancellation, and cache semantics rather than assuming automatic wiring.
* **GPU support for benchmarks.**  `scripts/bench/asr/bench_asr.py` already
  supports CPU / CUDA planning and resource guards for `faster_whisper` and
  `openai_whisper`.  Additional real-engine adapters, including `whisperx`,
  would be useful; `scripts/bench/benchmark_transcribers.py` still has a fake
  smoke path and an unimplemented real-engine stub.
* **Internationalisation.**  The CLI is bilingual (en / zh).  Python-facing
  messages use `i18n.t()`; the current Web UI strings live in static HTML /
  JS and need a deliberate extraction plan before they can use the same
  message catalog.

## Reporting a security issue

Please use GitHub Private Vulnerability Reporting rather than opening a
public issue. See [`SECURITY.md`](https://github.com/1273984347/mediascribe/blob/main/SECURITY.md)
for the disclosure policy.
