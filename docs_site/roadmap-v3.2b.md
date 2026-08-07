# Video2Text v3.2.0b — Planning Draft

> Status: **draft for community review**.  Last updated 2026-06-06.
>
> v3.2.0a just shipped (4 Tier 2 features · 612 tests · 75 % coverage).
> This document plans the next step: a **truly asynchronous pipeline**
> with optional GPU acceleration, capped by a reproducible
> `whisperx`-vs-`faster-whisper` benchmark that decides the v3.3
> default.

## Why v3.2.0b exists

v3.2.0a added disk-backed caching, VAD chunking, the `profile` CLI
and WebSocket progress, but the core pipeline is still serial:

```text
URL ──▶ download ──▶ transcribe ──▶ assemble ──▶ markdown
        (1)         (2)            (3)
        blocks      blocks         blocks
        the next    the next       the next
        stage       stage          stage
```

Three concrete pain points motivate v3.2.0b:

1. **Batch throughput** — a 50-video Douyin creator download spends
   ~80 % wall-clock on `transcribe`, mostly idle on the disk.  A
   queue-bounded worker pool cuts that to the slowest chunk.
2. **GPU underutilisation** — `faster-whisper` / `whisperx` expose a
   CUDA / Metal / ROCm device, but the current `Pipeline` always
   constructs a fresh `WhisperModel()` per call and never moves it
   to a non-CPU device.
3. **No benchmark** — we cannot tell whether `whisperx` (with
   `pyannote` alignment) is worth its install cost for the
   common-case 5-20 min Chinese-language video.

## Scope (must-have for v3.2.0b)

### 1. Fully asynchronous pipeline

**Effort** L · **Impact** High

* Replace the sequential `Pipeline.run` with an `asyncio.Pipeline`
  that runs `download` and `transcribe` concurrently inside a
  bounded `asyncio.Queue`.
* Worker pool size auto-detected from `os.cpu_count()` /
  `torch.cuda.device_count()` and clamped by the new
  `VIDEO2TEXT_MAX_WORKERS` env var (default 2).
* Each chunk carries an explicit `asyncio.Future[bytes]` so the
  transcriber can `await` it without polling.
* `asyncio.to_thread` wraps the blocking ffmpeg / `WhisperModel`
  calls so the event loop never stalls.
* `Pipeline` keeps the synchronous facade (`run`, `run_batch`) so
  existing callers are not broken; the new class is opt-in via
  `Pipeline.async_run(...)`.
* Backpressure: when the disk cache is full, `download` waits for
  the LRU eviction to free a slot before producing the next chunk.
* Cancellation: a single `pipeline.cancel()` cancels all
  in-flight futures; partial chunks on disk are GC'd by the
  cooperative check inside `ChunkedTranscriber`.

Tests: queue full / empty transitions, `asyncio.CancelledError`
propagation, exception in worker does not poison the pool, back-
pressure kicks in at 0-byte cache, `await pipeline.run()` result
matches the synchronous `pipeline.run()` result bit-for-bit.

### 2. GPU acceleration end-to-end

**Effort** M · **Impact** High

* New `device` field on `Settings` — values: `auto` (default),
  `cpu`, `cuda`, `cuda:0`, `metal`, `rocm`.
* `Pipeline._resolve_device()` probes the available backends in
  this order: `torch.cuda.is_available()` → `torch.backends.mps.is_available()` →
  `torch.version.hip is not None` → `cpu`.  Falls back to `cpu`
  with a one-line `logger.info` if the requested device is missing.
* `faster_whisper.WhisperModel(device=...)` is wired through.
* `whisperx.load_model(device=...)` is wired through.
* New `WhisperModel` is **cached** at module scope so repeated
  pipeline runs on the same machine reuse the loaded weights; a
  per-process `LRU(1)` keyed on `(model_name, device, compute_type)`.
* `nvidia-smi` / `rocm-smi` health is exposed at `GET /api/health`
  as a new `gpu` block (`name`, `vram_total`, `vram_used`,
  `temperature`, `utilisation_pct`).

Tests: device resolution table, `cuda` requested but missing →
`cpu` + warning, model cache hit on second call, `/api/health` shape.

### 3. Reproducible `whisperx` vs `faster-whisper` benchmark

**Effort** M · **Impact** High (decides v3.3 default)

* Ship a fixed 1-hour corpus under
  [`scripts/benchmark_corpus/`](file:///d:/1/video2text/scripts/benchmark_corpus/)
  with 4 audio-only WAV files (5 min / 15 min / 30 min / 60 min),
  16 kHz mono, in 3 languages (Mandarin / English / mixed).
* New [`scripts/benchmark_transcribers.py`](file:///d:/1/video2text/scripts/benchmark_transcribers.py)
  CLI:
  ```bash
  python -m scripts.benchmark_transcribers \
      --corpus scripts/benchmark_corpus \
      --engines faster_whisper,whisperx \
      --models base,small,medium \
      --device auto \
      --output runs/bench-2026-06-06.json
  ```
  Reports per-file: `wall_clock_s`, `real_time_factor`, `wer_pct`,
  `peak_vram_mb`, `first_token_latency_ms`.
* Default `wer_pct` is computed against a hand-corrected reference
  transcript bundled in the corpus.
* CI integration: a new `bench` workflow runs on self-hosted GPU
  runners weekly; a comment bot posts the diff on PRs that touch
  `transcribers/`.

Tests: schema is stable, run with `--engines fake` returns canned
data so the CI does not need a GPU.

## Scope (should-have, target if the above finishes early)

### 4. `pipeline.profile` decorator that emits to the existing JSONL

**Effort** S · **Impact** Medium

* `Pipeline` gains a `profile=True` flag that wraps each stage in
  the existing `@profile_step` decorator from `performance.py`,
  so the v3.2.0a `profile` CLI can be reused without code change.

### 5. Web UI: per-job GPU pill

**Effort** S · **Impact** Low (UX)

* `static/index.html` reads `GET /api/health` and renders a small
  pill ("GPU: RTX 4090 · 24 GiB · 41 % util") next to the existing
  health probe badge.

### 6. Cooperative cancellation through `WebSocket`

**Effort** S · **Impact** Medium

* The browser's `cancel` button (already shipped in the v3.2.0a
  WS demo) is wired to the new `Pipeline.cancel()` so a long
  batch can be aborted cleanly from the dashboard.

## Out of scope (won't-have in v3.2.0b)

* **Distributed pipeline** (multi-machine) — deferred to v3.3.
* **Real-time streaming transcription** — explicitly out per the
  v3.2.0a roadmap.
* **WhisperX consensus mode** — already tracked in v3.2.0 Tier 3
  item 10; if the benchmark proves `whisperx` strictly better we
  may promote it to default in v3.3.

## Suggested milestone split

| Milestone | Scope                                  | Target  |
|-----------|----------------------------------------|---------|
| 3.2.0a    | VAD / cache / profile CLI / WS progress | ✅ shipped 2026-06-06 |
| 3.2.0b    | Async pipeline + GPU + benchmark       | T+2-3 weeks |
| 3.2.0c    | profile decorator + GPU pill + cancel WS | T+4 weeks |
| 3.3.0     | Default engine = benchmark winner      | T+6 weeks |

## Quality gate

```text
612 → ~680 passed (≈ +70 new tests for async pipeline + GPU + bench)
coverage: video2text/pipeline.py ≥ 90 %, video2text/performance.py ≥ 90 %
ruff: 0 errors, mypy --strict on video2text/{pipeline,performance}.py: 0 errors
bench JSON schema is stable across the v3.2.0b → v3.3 cycle
```

## Open questions

1. **`whisperx` install cost** — `pyannote.audio` pulls in
   `torch` + `torchaudio` (~ 2 GiB).  Should v3.3 make it
   default, or stay opt-in?
2. **`asyncio.Pipeline` API shape** — should it be a context
   manager (`async with Pipeline.async_run(...) as p:`) or a plain
   coroutine?  The context-manager form is more Pythonic but
   requires `__aenter__` / `__aexit__` to be added.
3. **Benchmark corpus licensing** — the 1-hour corpus is currently
   CC-BY (we wrote the reference transcripts).  Is CC-BY-SA a
   better fit for the community remix case?
4. **GPU health probe rate** — exposing `nvidia-smi` per
   `/api/health` call costs ~ 30 ms; rate-limit to 1 Hz via a
   1-second LRU cache, or accept the cost?

---

**Total Tier 1 work**: 3 items · L + M + M = roughly 2-3 weeks of
focused engineering.

**Test budget**: 60-80 new tests across v3.2.0b.  Maintain 75 %+
overall coverage and ≥ 90 % on the two new hot files
(`pipeline.py` + `performance.py`).
