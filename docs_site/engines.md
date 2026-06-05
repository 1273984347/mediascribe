# Engines

Video2Text supports three ASR engines, with automatic fallback.

| Engine | Throughput | Accuracy | Installation |
|--------|------------|----------|--------------|
| `whisper` | 1x baseline | good | hard dep |
| `faster-whisper` | ~3.5x | good | `pip install faster-whisper` |
| `whisperx` | ~5x | best, with alignment | `pip install whisperx` |

The factory in `video2text/transcribers/factory.py` walks the
fallback chain (`whisperx → faster-whisper → whisper` by default)
and instantiates the first one that is installed.

## Choosing an engine

For CPU-only hosts, `faster-whisper` is the sweet spot.  For
GPU hosts with ≥8 GB VRAM, `whisperx` is best when alignment
matters; otherwise `faster-whisper` is still slightly faster
and uses less VRAM.

## Models

| Model | VRAM (fp16) | Speed vs tiny | WER (LibriSpeech) |
|-------|-------------|---------------|-------------------|
| tiny | ~1 GB | 1x | 7.6 |
| base | ~1 GB | 2x | 5.0 |
| small | ~2 GB | 4x | 3.4 |
| medium | ~5 GB | 8x | 2.4 |
| large | ~10 GB | 16x | 2.0 |

## Benchmarking

The repo ships a mock-mode benchmark that runs in any sandbox:

```bash
python scripts/benchmark_transcribers.py
```

It writes `bench-results/benchmark.{json,txt,md}`.  See
[Benchmarking](benchmarking.md) for the full guide.

## Long videos

For audio longer than ~30 minutes, use the chunked transcriber
to avoid OOM and improve alignment.  See [Long videos](chunking.md).
