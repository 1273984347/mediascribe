---
name: "video2text-benchmark"
description: "Compares whisper / faster-whisper / whisperx on cost, throughput, and memory. Invoke when the user wants to choose the best ASR engine, profile a specific audio file, or decide between CPU and GPU deployment."
---

# Video2Text — Benchmark

A self-contained, sandbox-safe benchmark for the three supported
ASR engines.  It produces a Markdown + JSON report that compares
cold-start latency, per-call throughput, and peak memory.

## When to use

- The user is choosing between `whisper`, `faster-whisper`, and
  `whisperx` and wants data, not vibes.
- The user wants to know whether their hardware can keep up with
  real-time transcription (target: throughput > 1.0x).
- The user wants to compare CPU vs GPU for a specific clip.

## When NOT to use

- The user wants a single transcription — use the `video2text`
  skill directly, the benchmark adds overhead.
- The user wants ASR quality (WER) numbers — that requires a
  labelled dataset; this benchmark measures speed, not accuracy.

## Quick start

```bash
# Mocked cost profile (default; runs in <1s on any machine)
python scripts/benchmark_transcribers.py

# Real engines (requires the corresponding package to be installed)
python scripts/benchmark_transcribers.py --real \
    --audio-path ./sample.wav

# Restrict to a single engine
python scripts/benchmark_transcribers.py --engine faster-whisper

# Tweak the synthetic audio length
python scripts/benchmark_transcribers.py --audio-seconds 60 --iterations 3
```

## What it measures

| Metric | What it captures | Units |
|--------|------------------|-------|
| Cold start | First-call latency, dominated by model load | ms |
| Mean ms/call | Average of N `transcribe()` calls | ms |
| Throughput | Audio seconds processed per real second | ratio |
| Peak memory | Tracemalloc peak across the run | MB |

The throughput number is the key one: **> 1.0x means the engine
keeps up with real time** on the test machine.

## Output

The script writes three files to `./bench-results/`:

* `benchmark.json` — full machine-readable data
* `benchmark.txt` — pretty-printed ASCII table
* `benchmark.md` — Markdown table for inclusion in PRs / docs

Example mock output:

```
| Engine | Available | Cold start (ms) | Mean ms/call | Throughput | Peak MB |
|--------|-----------|------------------|--------------|------------|---------|
| whisper | yes | 45 | 125.4 | 79.74 | 0.0 |
| faster-whisper | yes | 12 | 34.5 | 289.80 | 0.0 |
| whisperx | yes | 55 | 70.3 | 142.21 | 0.0 |
```

(The `Available` column shows `yes` for all in mock mode; it shows
the real package status when `--real` is used.)

## Cost profile (mocked mode)

The default cost profile mirrors widely-reported CPU benchmarks:

| Engine | cold_start_ms | ms_per_audio_sec |
|--------|---------------|------------------|
| `whisper` | 4500 | 800 |
| `faster-whisper` | 1200 | 220 |
| `whisperx` | 5500 | 150 |

These are scaled by `BENCH_SCALE` (default `0.01`) so the mock run
finhes in a few seconds instead of an hour.  Set
`BENCH_SCALE=1.0` to get a more realistic wall-clock estimate.

## Tests

```bash
python -m pytest douyin_batch/tests/test_benchmark_transcribers.py -v
```

Pins the cost profile, the table renderer, and the JSON report
shape so the benchmark cannot silently regress.

## Limitations

* Mocked mode does NOT download models; real-mode measurements
  include model load time but NOT video download or audio extract.
* Memory measurement uses `tracemalloc` (Python-only); it
  under-counts native allocations from `ctypes` (used by
  faster-whisper) and PyTorch.
* No quality (WER) comparison.  Use a labelled dataset for that.

## Related files

- `scripts/benchmark_transcribers.py` — the runner
- `video2text/transcribers/factory.py` — engine factory
- `docs/cli-screenshots/` — example outputs from the full CLI
