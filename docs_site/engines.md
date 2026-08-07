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

## GPU VRAM-aware concurrency (v3.2.0e)

When `AsyncPipeline.run_batch` starts a batch on a CUDA device,
`_gpu_aware_concurrency()` caps `max_concurrent` by
`free_vram // vram_per_task_mb` to avoid CUDA OOM.  It falls back to
the configured `base` (CPU / metal / unknown VRAM).

| Per-task VRAM budget | Source |
|---------------------|--------|
| `VIDEO2TEXT_VRAM_PER_TASK_MB` env var | explicit override |
| Default 3000 MB | large-v3 ~5GB / medium ~5GB / small ~2GB → conservative |

GPU health is probed via `gpu_health()` and cached in `_GpuHealthCache`
(TTL 5 s, thread-safe) so consecutive batches in the same process
share a single probe.

## Subprocess timeout control (v3.2.0f)

Two subprocess invocations now honour explicit timeouts to avoid
blocking the host process indefinitely:

| Subprocess | Env var | Default | Behaviour on timeout |
|------------|---------|---------|----------------------|
| FFmpeg audio extraction (`audio_utils.extract_audio`) | `VIDEO2TEXT_FFMPEG_TIMEOUT` | 600 s | raises `subprocess.TimeoutExpired` |
| MCP batch transcribe (`mcp_server._tool_batch_transcribe_creator`) | `VIDEO2TEXT_BATCH_TIMEOUT` | 1800 s | returns `{ok: false, error: "batch transcribe timed out ..."}` |

Invalid env values fall back to the default and emit a `WARNING` log
line so misconfiguration is observable.

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
