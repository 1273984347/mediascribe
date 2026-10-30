# Benchmarking

The repo ships a self-contained benchmark that compares ASR engines along
the axes that matter in practice: **throughput (RTF), error rate, peak VRAM
and peak RAM** — plus a second suite that compares **front-end frameworks**
and a third that compares **workbench delivery shapes**.

!!! warning "What changed on 2026-10-23"
    This page used to document a CLI that never existed
    (`--real`, `--audio-path`, `--engine`, `--audio-seconds`, `--iterations`
    and a `./bench-results/` output directory). The real-engine path in
    `scripts/bench/benchmark_transcribers.py` was a `NotImplementedError` stub, so
    the documented commands could not have worked. Both were replaced — see
    [Current suites](#current-suites) below.

## Current suites

| Suite | Script | Measures | Output |
|---|---|---|---|
| **L3 — ASR engines** | `scripts/bench/asr/bench_asr.py` | RTF, error rate, peak VRAM, peak RAM, GPU temperature | `output/bench/asr/` |
| **L3 — whisper.cpp** | `scripts/bench/asr/bench_whispercpp.py` | same metrics, same corpus, for the GGML/C++ engine | `output/bench/asr_cpp/` |
| **L1 — front-end frameworks** | `scripts/bench/frontend/bench_frontend.py` | build time, bundle size, dependency size, cold start, render time, filter latency, scroll frame rate | `scripts/bench/frontend/results.json` (local-only, not committed) |
| **L2 — workbench shells** | `scripts/bench/shell/bench_shell.py` | cold start, resident memory, distribution size, offline self-containment, cross-platform | `scripts/bench/shell/results.json` (local-only, not committed) |

The full comparison and its conclusions live in the internal tech-selection
report (not published; results tables above are the authoritative summary).

## The corpus is synthesised offline

Real audio corpora are not in the repo (they would be tens of MiB), and
HuggingFace is not reachable from every environment. Instead the corpus is
synthesised locally with **Windows SAPI**:

```bash
python scripts/bench/corpus/gen_corpus_real.py --out output/bench/corpus
```

10 clips / 10.2 minutes: three Mandarin voices, a term-dense lecture script,
colloquial short-video speech, English, code-switched Mandarin/English, plus
light-noise and two degraded ("harsh") variants. Because TTS reads the text
verbatim, **the reference transcript is exact by construction** — so the
measured error rate is pure ASR error, with no annotation noise.

!!! tip "Reproducibility"
    The noise generator's random seed is pinned (`NOISE_SEED` in
    `gen_corpus_real.py`). Two independent runs produce byte-identical WAVs —
    verify with a SHA-256 comparison. Without a fixed seed, the same
    parameters produced error rates anywhere between 23.8 % and 45.9 %.

## Quick start — ASR engines

```bash
# Auto-plan for this machine's hardware (recommended)
python scripts/bench/asr/bench_asr.py \
    --corpus output/bench/corpus \
    --out output/bench/asr

# Restrict to one engine / model / device
python scripts/bench/asr/bench_asr.py --engines faster_whisper --models small
python scripts/bench/asr/bench_asr.py --devices cpu

# CPU-only pass over representative clips
python scripts/bench/asr/bench_asr.py \
    --engines faster_whisper,openai_whisper --models small,medium,large-v3 \
    --devices cpu \
    --files 01_zh_huihui.wav,01h_zh_huihui_harsh.wav,06_en_zira.wav
```

### What it measures

| Metric | What it captures | Units |
|--------|------------------|-------|
| `real_time_factor` | wall clock ÷ audio duration. **< 1.0 means faster than real time** | ratio |
| `error_rate_pct` | Chinese → **character** error rate (CER); English → **word** error rate (WER) | % |
| `sampled_peak_vram_mb` | peak GPU memory for that round | MiB |
| `sampled_peak_rss_mb` | peak resident memory of the worker process | MiB |
| `sampled_peak_gpu_temp_c` | peak GPU temperature during the round | °C |

### Hardware awareness and safety rails

The suite adapts to the machine instead of assuming a fixed target:

* **Auto-planning** — a combination is skipped (with a recorded reason) when
  VRAM or RAM is insufficient; precision is downgraded (`float16` →
  `int8_float16` → `int8`) before anything is dropped.
* **Overload protection** — GPU temperature hard limit, sustained CPU load,
  system-available-memory floor, disk headroom, per-round wall-clock
  timeout. A round that trips a rail is stopped **safely** (terminate, then
  kill) and the run is **resumable**: completed rounds are kept.
* **Progress** — a heartbeat line every few seconds plus
  `output/bench/asr/progress.json` (done/total/ETA/live watermarks).
* **Resume** — re-running skips rounds whose result file already exists and
  has no abort reason.

```bash
# tune the rails
python scripts/bench/asr/bench_asr.py \
    --hard-gpu-temp-c 92 --sys-ram-abort-mb 512 --sys-ram-start-mb 2048 \
    --wait-seconds 600 --timeout-s 1800
```

## Quick start — front-end frameworks

```bash
python scripts/bench/frontend/scaffold_candidates.py   # generate the 5 candidates
python scripts/bench/frontend/bench_frontend.py        # build + measure
python scripts/bench/frontend/bench_frontend.py --only vue   # re-run one candidate
```

All five candidates (vanilla / React / Vue / Svelte / Solid) are generated
from **one template**, so the DOM structure, the stylesheet and the
instrumentation protocol are byte-identical — that is what makes the numbers
comparable. The data file is produced once with a fixed seed and shared by
every candidate.

## Summarising and re-computing

```bash
# Markdown table; distinguishes completed rounds from skipped/failed ones
python scripts/bench/asr/summarize_asr.py --markdown

# Re-compute error rates under the current normalisation
# (re-runs no inference — the hypothesis text is stored in each round file)
python scripts/bench/asr/recompute_cer.py
```

!!! warning "Chinese script normalisation is not optional"
    `openai-whisper` and `whisper.cpp` emit **traditional** Chinese;
    `faster-whisper` emits **simplified**. Comparing them with a raw
    character-level CER reports *script differences* as *recognition errors* —
    measured swings of 33.61 % → 0.41 % and 81.56 % → 1.19 %. Every
    comparison normalises both sides to simplified first
    (`scripts/bench/asr/zh_normalize.py`, zero third-party dependencies).

## Limitations

* The corpus is **synthetic**. TTS speech is cleaner and more uniform than
  real recordings, so absolute error rates are optimistic — use it for
  **relative** comparison between engines.
* On Windows, `nvidia-smi` cannot report **per-process** VRAM (WDDM mode);
  the suite therefore uses the **whole-device occupancy delta** as the VRAM
  metric, and reports PyTorch's exact `max_memory_allocated()` separately
  for PyTorch-based engines.
* A model whose weights are not in the local cache **cannot be measured**
  offline. Missing weights are reported as a skip reason, never as a result.
* The front-end suite renders 10,000 rows **without virtualisation** by
  design — it measures "can the framework keep up with a large plain DOM",
  not "can the framework virtualise well".
* `bench_shell.py` measures cold start to a **health check returning 200**
  for the web variants and to an **app-usable marker** for Electron; the two
  are not perfectly symmetric (noted in `scripts/bench/shell/README.md`).
