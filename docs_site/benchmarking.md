# Benchmarking

The repo ships a self-contained benchmark that compares
`whisper` / `faster-whisper` / `whisperx` along the axes
that matter most in practice: cold-start latency, per-call
throughput, and peak memory.

## Quick start

```bash
# Mocked (default; safe in CI)
python scripts/benchmark_transcribers.py

# Real engines (requires the corresponding package to be installed)
python scripts/benchmark_transcribers.py --real \
  --audio-path ./sample.wav

# Restrict to one engine
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

## Cost profile (mocked mode)

The default cost profile mirrors widely-reported CPU benchmarks:

| Engine | cold_start_ms | ms_per_audio_sec |
|--------|---------------|------------------|
| `whisper` | 4500 | 800 |
| `faster-whisper` | 1200 | 220 |
| `whisperx` | 5500 | 150 |

These are scaled by `BENCH_SCALE` (default `0.01`) so the
mock run finishes in a few seconds instead of an hour.
Set `BENCH_SCALE=1.0` for a more realistic wall-clock estimate.

## Limitations

* Mocked mode does NOT download models; real-mode measurements
  include model load time but NOT video download or audio
  extract.
* Memory measurement uses `tracemalloc` (Python-only); it
  under-counts native allocations from `ctypes` (used by
  faster-whisper) and PyTorch.
* No quality (WER) comparison.  Use a labelled dataset for that.
