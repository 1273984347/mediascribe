# Engines

MediaScribe supports four ASR engines, with automatic fallback.

| Engine | Throughput | Accuracy | Installation |
|--------|------------|----------|--------------|
| `whisper` | 1x baseline | good | hard dep |
| `faster-whisper` | ~3.5x | good | `pip install faster-whisper` |
| `whisperx` | ~5x | best, with alignment | `pip install whisperx` |
| `whisper-cpp` | ~3x (CPU), lowest memory | good (multilingual) | external binary + GGML weights |

The factory in `mediascribe/transcribers/factory.py` walks the
fallback chain (`whisperx → faster-whisper → whisper → whisper-cpp` by default)
and instantiates the first one that is installed.

!!! tip "Memory-pressure reordering"
    When the host's available memory drops below 4 GB and the device is
    CPU/auto, `whisper-cpp` is moved **ahead of** `faster-whisper`.
    Measured peak RSS (CPU, 2026-10-23): whisper.cpp tiny **291 MiB** vs
    faster-whisper small 952 MiB vs faster-whisper large-v3 3613 MiB.

## Choosing an engine

For CPU-only hosts, `faster-whisper` is the sweet spot.  For
GPU hosts with ≥8 GB VRAM, `whisperx` is best when alignment
matters; otherwise `faster-whisper` is still slightly faster
and uses less VRAM.

For **low-memory or GPU-less hosts** (e.g. 4 GB RAM laptops, VMs),
`whisper-cpp` is the fallback of last resort that still works: it needs
no PyTorch/CTranslate2 at all, just the `whisper-cli` binary and a GGML
weight file.

| Prerequisite | Where |
|---|---|
| Binary | `MEDIASCRIBE_WHISPER_CPP_BIN`, or `~/.cache/mediascribe/whisper-cpp/bin/Release/whisper-cli.exe` |
| Weights | `MEDIASCRIBE_WHISPER_CPP_MODELS` (default `~/.cache/mediascribe/whisper-cpp`), file `ggml-<model>.bin` |
| VAD model | `ggml-silero-v6.2.0.bin` in the same directory — **strongly recommended**: without VAD, noisy audio produces heavy hallucination |

Run `python -m mediascribe doctor` to see which weights are already
pre-seeded on the machine.

## Chinese script (simplified vs traditional)

Whisper's training data mixes simplified and traditional Chinese, and
**different engines default to different scripts** — `faster-whisper`
emits simplified, while `openai-whisper` / `whisperx` / `whisper-cpp`
emit traditional.  That is a *script* difference, not an accuracy
difference (same audio, character error rate 33.61 % vs 0.41 %).

MediaScribe therefore uses two defences:

1. **Source** (works on every platform) — a simplified `initial_prompt`
   is injected for Chinese in all engines, so the model *generates*
   simplified text.
2. **Fallback** (Windows only) — the output layer converts with the
   built-in `LCMapStringW`, zero third-party dependencies.

Disable with `--no-zh-simplify` (CLI), `MEDIASCRIBE_ZH_SIMPLIFY=0`
(env), or `zh_simplify: false` (config file).  Turning it off only
disables the output-layer conversion — the engine-side prompt still applies.

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
| `MEDIASCRIBE_VRAM_PER_TASK_MB` env var | explicit override |
| Default 3000 MB | large-v3 ~5GB / medium ~5GB / small ~2GB → conservative |

GPU health is probed via `gpu_health()` and cached in `_GpuHealthCache`
(TTL 5 s, thread-safe) so consecutive batches in the same process
share a single probe.

## Subprocess timeout control (v3.2.0f)

Two subprocess invocations now honour explicit timeouts to avoid
blocking the host process indefinitely:

| Subprocess | Env var | Default | Behaviour on timeout |
|------------|---------|---------|----------------------|
| FFmpeg audio extraction (`audio_utils.extract_audio`) | `MEDIASCRIBE_FFMPEG_TIMEOUT` | 600 s | raises `subprocess.TimeoutExpired` |
| MCP batch transcribe (`mcp_server._tool_batch_transcribe_creator`) | `MEDIASCRIBE_BATCH_TIMEOUT` | 1800 s | returns `{ok: false, error: "batch transcribe timed out ..."}` |

Invalid env values fall back to the default and emit a `WARNING` log
line so misconfiguration is observable.

## Benchmarking

Real-engine benchmarks live in `scripts/bench/` and need a corpus:

```bash
python scripts/bench/corpus/gen_corpus_real.py --out output/bench/corpus
python scripts/bench/asr/bench_asr.py --corpus output/bench/corpus
```

Results land in `output/bench/asr/` (gitignored) with a Markdown summary
via `python scripts/bench/asr/summarize_asr.py --markdown`.  See
[Benchmarking](benchmarking.md) for the full guide.

## Long videos

For audio longer than ~30 minutes, use the chunked transcriber
to avoid OOM and improve alignment.  See [Long videos](chunking.md).
