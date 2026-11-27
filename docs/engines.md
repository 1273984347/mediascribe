# Engines

<img src="../assets/compact/c2-waveform.jpg" alt="Audio waveform and spectrogram analysed by the ASR engines" width="620" />

*Four local ASR engines, with a fixed standard default and explicit opt-ins.*

MediaScribe supports four ASR engines. The standard default is
**`faster-whisper` + `small`**. If faster-whisper is not installed, MediaScribe
falls back to `whisper` and prints a notice. WhisperX is opt-in; installing it
does not change automatic selection.

The throughput figures below are historical reference measurements, not an
installation-time priority order.

| Engine | Throughput | Accuracy | Installation |
|--------|------------|----------|--------------|
| `whisper` | 1x baseline | good | `pip install "mediascribe[whisper]"` (torch stack is opt-in) |
| `faster-whisper` | ~3.5x | good | `pip install "mediascribe[faster-whisper]"` |
| `whisperx` | ~5x | best, with alignment | `pip install "mediascribe[whisperx]"` |
| `whisper-cpp` | ~3x (CPU), lowest memory | good (multilingual) | external binary + GGML weights |

Engine **construction** lives in `mediascribe/transcribers/factory.py`
(name → instance, no fallback chain — the memory-pressure fallback that
once lived there was removed on 2026-10-29; engine *selection* under
resource pressure lives in `mediascribe/runtime_guard.py`).

!!! note "Current default and fallback"
    CLI / Web / MCP / batch all use `faster-whisper` + `small` as the standard
    default. If faster-whisper is unavailable, `whisper` is the compatibility
    fallback and the fallback is reported to the user. An explicit `--engine`
    CLI flag / Web request field / `MEDIASCRIBE_BATCH_ENGINE` value always wins.
    WhisperX requires an explicit engine choice.

!!! note "Resource guard"
    On constrained hosts, the runtime guard may adjust a **non-explicit** engine
    or model choice and records the reason in notices and metadata. Explicit
    engine/model/device choices are protected. This is separate from the
    install-time default and does not make WhisperX an automatic preference.

!!! tip "Memory-pressure reordering"
    When the host's available memory drops below 4 GB and the device is
    CPU/auto, `whisper-cpp` is moved **ahead of** `faster-whisper`.
    Measured peak RSS (CPU, 2026-10-23): whisper.cpp tiny **291 MiB** vs
    faster-whisper small 952 MiB vs faster-whisper large-v3 3613 MiB.

## Choosing an engine

For CPU-only hosts, `faster-whisper` is the standard choice. Use `whisperx`
explicitly when word-level alignment or speaker diarization is required; its
extra models and optional Hugging Face access are part of that opt-in path.

For **low-memory or GPU-less hosts** (e.g. 4 GB RAM laptops, VMs),
`whisper-cpp` is an explicit low-memory option: it needs no PyTorch/CTranslate2,
just the `whisper-cli` binary and a GGML weight file.

| Prerequisite | Where |
|---|---|
| Binary | `MEDIASCRIBE_WHISPER_CPP_BIN`, or `~/.cache/mediascribe/whisper-cpp/bin/Release/whisper-cli.exe` |
| Weights | `MEDIASCRIBE_WHISPER_CPP_MODELS` (default `~/.cache/mediascribe/whisper-cpp`), file `ggml-<model>.bin` |
| VAD model | `ggml-silero-v6.2.0.bin` in the same directory — **strongly recommended**: without VAD, noisy audio produces heavy hallucination |

Run `python -m mediascribe doctor` to see which weights are already
pre-seeded on the machine. Model weights are **not downloaded by default**.
For an online first run, authorize the download explicitly:

```bash
python -m mediascribe transcribe "video.mp4" --allow-model-download
# Verification uses the same opt-in flag:
python -m mediascribe verify output/transcripts/<transcript>.md \
  --allow-model-download
```

For offline use, place the weights in the engine-specific cache first, then run
`python -m mediascribe doctor --offline` and transcribe without
`--allow-model-download`. See
[Configuration](configuration.md#model-weight-caches) for cache locations.

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

`small` is the default model tier. Names such as `large-v3` are **model names**,
not engines; select them with `--model large-v3`. The table is a historical
size/speed/quality reference, while actual runtime choices may be adjusted by
the resource guard when the user has not explicitly fixed them.

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

## Durable Web checkpoint behavior

All four engines support **explicit** resume of interrupted durable Web jobs;
this is not automatic recovery and does not promise exactly-once execution.
The selected engine determines the checkpoint granularity:

| Engine | Checkpoint commit boundary | Resume/finalization behavior |
|---|---|---|
| `faster-whisper` | Each accepted segment during decoding | Resumes from a one-second overlap after the last committed segment; the final Markdown includes the durable prefix |
| `whisper` | A complete 60-second window after `model.transcribe()` returns | Uses 2-second overlap; no segments are committed from an incomplete call |
| `whisperx` | A complete 60-second window after ASR **and** `align()` | Aligned word timestamps are persisted; diarization reruns once over the complete resumed result |
| `whisper-cpp` | A complete 60-second window with exit code 0 and complete valid JSON | Nonzero exit or missing/malformed/incomplete JSON rejects the window even if a file exists; official `result.language` is reused across windows |

Window engines use a fixed 60/2 schedule (58-second stride), independent of the
optional Python `ChunkedTranscriber` settings. The final window is clamped to the
audio duration and terminates the schedule; a 118-second recording has exactly
two windows, `[0, 60]` and `[58, 118]`. A completed window, including a silent
one, advances the independent window cursor. An interrupted window is retried
as a whole, bounding normal recomputation to one window under at-least-once
semantics. Resume combines durable prefix segments with accepted new segments
and removes deterministic overlap duplication.

The WhisperX extra now targets the official `whisperx>=3.8.6,<4` interface,
including `initial_prompt` at model load time. Resume compares the exact
effective language and automatic/domain prompt as well as engine, model,
device, and runtime fingerprint before creating an attempt. faster-whisper's
fingerprint includes its package version and records only kwargs actually sent
to `model.transcribe()`; omitted `temperature`/`beam_size` are library defaults,
not claimed explicit inputs. OpenAI Whisper resolves `auto`/`None` device
selection deterministically without loading a model. Real-model resume quality
and throughput still require qualified-runner evidence; mocked adapter and
browser checks do not establish real ASR performance.

Checkpoints never persist cookies, Hugging Face tokens, API keys, or other
secrets. WhisperX diarization uses a token only from the current runtime; resume
rejects missing required authorization before creating a new attempt. See
[Web UI](web-ui.md#post-apijobsjob_idresume) and
[Long videos](chunking.md#durable-web-checkpoints-are-a-separate-mechanism).

## Long videos

For audio longer than ~30 minutes, Python callers can wrap an engine in
`mediascribe.transcribers.ChunkedTranscriber` to avoid OOM and improve
alignment. Ordinary CLI / Web / MCP `transcribe` does **not** automatically
switch to this wrapper.

## Optional VAD chunking and persistent caches

Install `mediascribe[vad]` only if a Python caller explicitly requests
VAD-aligned chunk boundaries. Pre-provision the inner engine's weights first;
this low-level example does not authorize model downloads:

```python
from pathlib import Path

from mediascribe.transcribers import ChunkedTranscriber, get_transcriber

inner = get_transcriber("faster-whisper", model="small")
chunked = ChunkedTranscriber(
    inner,
    chunk_seconds=600,
    overlap_seconds=5,
    use_vad=True,
)
result = chunked.transcribe(Path("long_audio.wav"), output_dir="output/chunked")
```

Installing `[vad]` does not change normal `transcribe`, and VAD is not required
for fixed-window chunking.

The persistent **download cache is off by default**. Enable it explicitly with
`MEDIASCRIBE_DOWNLOAD_CACHE=1`. `MEDIASCRIBE_CACHE_DIR` sets the root for
persistent download/chunk caches; it is not the ASR model-weight cache.
`PersistentChunkCache` exists as a library class, but it is not wired into the
CLI, Web, MCP, or normal pipeline user path.
