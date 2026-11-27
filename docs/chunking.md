# Long videos

<img src="../assets/compact/c4-timeline.jpg" alt="A long media strip cut into overlapping segments with chapter markers" width="420" />

*A long recording cut into overlapping segments, then merged back into one transcript.*

For audio longer than ~30 minutes, feeding the whole file into a Whisper
model at once can exhaust consumer GPU memory, produce weaker alignment, and
make one bad chunk lose the whole run.  MediaScribe provides an optional
Python API implementing **chunk + overlap + merge** in
`mediascribe.transcribers.chunked.ChunkedTranscriber`.

!!! warning "Python API only"
    `ChunkedTranscriber` is not selected by the ordinary CLI, Web UI, or MCP
    transcription tools.  There is no `chunked` engine name or public
    `--chunk-seconds` option.  Use the Python API below when you explicitly
    want this wrapper.

## Quick start

Install an inner ASR engine, then wrap it:

```bash
pip install "mediascribe[faster-whisper]"
```

```python
from pathlib import Path

from mediascribe.transcribers import ChunkedTranscriber, get_transcriber

inner = get_transcriber("faster-whisper", model="small")
ct = ChunkedTranscriber(inner, chunk_seconds=600, overlap_seconds=5)
result = ct.transcribe("long_audio.wav", output_dir="transcripts")

print(result.text)
print(result.output_path)  # transcripts/long_audio.md
print(result.sidecar_path)  # transcripts/long_audio.md.chunks.json
```

`output_dir` is **keyword-only**.  Passing a second positional argument is
invalid; write `ct.transcribe(path, output_dir=...)`.

Without `output_dir`, the merged result is returned in memory only, temporary
chunk audio is removed, and `output_path` / `sidecar_path` are `None`.  With
`output_dir`, the wrapper atomically writes `<stem>.md` and
`<stem>.md.chunks.json`, then removes temporary `chunk_*.wav` files while
preserving those final outputs.

The result is dict-compatible and also exposes attributes:

* `text` / `result["text"]` — merged transcript;
* `segments` — segments with global timestamps relative to the source audio;
* `chunks` — per-chunk `ChunkResult` metadata, including any `error`;
* `output_path` / `sidecar_path` — persisted outputs, or `None`.

## Configuration

| Parameter | Default | What it controls |
|---|---|---|
| `inner` | required | The actual ASR engine called once per chunk |
| `chunk_seconds` | `600` | Target chunk size in seconds (10 min) |
| `overlap_seconds` | `5` | Overlap between consecutive fixed / long-speech windows |
| `use_vad` | `False` | Opt into `webrtcvad`-aligned splitting when compatible |
| `vad_aggressiveness` | `2` | VAD aggressiveness passed to speech detection |
| `output_dir=` | `None` | Keyword-only output directory for Markdown + sidecar |

If the inner transcriber fails on one chunk, the wrapper records the error
and continues.  The final Markdown contains text from successful chunks,
and `.chunks.json` records per-chunk failures.  Setup failures before the
per-chunk loop are different: they abort the call rather than producing a
partial transcript.

## Audio probe and split fallbacks

`probe_duration()` first uses the shared `ffprobe`-backed duration helper,
then tries the stdlib `wave` module for `.wav` files.  If both fail it raises
`RuntimeError`; that exception propagates out of `ChunkedTranscriber.transcribe`.
A failed duration probe does **not** return a single-chunk result.

`split_audio()` has two separate fallback cases:

* If the duration is known but `ffmpeg` is missing, it copies the source to
  `chunk_000.wav` and returns one chunk.  This effectively disables splitting
  and transcoding; it is not a probe-failure fallback.
* If VAD is requested but unavailable, incompatible with the source format,
  or fails at runtime, the splitter falls back to fixed-duration windows.

Errors from the actual ffmpeg extraction command propagate.  Temporary files
are cleaned in `finally` on both success and failure.

## Overlap de-duplication

Each chunk's segment timestamps are offset by `chunk.start`, producing a
global timeline.  `merge_segments()` sorts those segments and drops a segment
when its time range overlaps an already-retained range by more than a 10 ms
epsilon.  Touching endpoints are not treated as duplicates.  This prevents
the same time span from being emitted twice because adjacent audio windows
overlap.

This is timestamp-range de-duplication, not fuzzy text matching.  It can also
drop genuinely overlapping speech, and it does not compare transcript text.
If no chunk supplies segments, `merge_texts()` falls back to joining the
per-chunk text in order, without overlap de-duplication.

## VAD and persistent caches

!!! note "Implemented classes, limited wiring"
    * VAD support exists in the Python chunking API.  Install
      `mediascribe[vad]` (`webrtcvad`) and pass `use_vad=True`.  Compatible
      PCM16 mono WAV input can be split around detected speech regions; long
      speech regions are still capped and overlapped.  VAD is off by default.
    * `mediascribe.cache.PersistentChunkCache` exists as a standalone class
      keyed by source path, mtime, size, and split parameters.  It is not
      called by `ChunkedTranscriber` or wired into the ordinary CLI, Web, or
      MCP paths. `python -m mediascribe cache info` can inspect its existing
      index, and `cache clear --yes` can remove indexed chunk directories
      without enabling chunk caching.

Do not confuse `PersistentChunkCache` with either of these:

* `mediascribe.performance.DownloadCache`, which is a single-run URL → path
  cache; or
* `mediascribe.cache.PersistentDownloadCache`, which is a separate cross-run
  download cache wired into the pipeline only when
  `MEDIASCRIBE_DOWNLOAD_CACHE=1`.

See [Performance](performance.md) for profiling and cache boundaries.

## Durable Web checkpoints are a separate mechanism

The optional `ChunkedTranscriber` above is **not** the durable Web recovery
scheduler. Ordinary Web jobs use the checkpoint capability of their selected
engine:

| Engine | Durable checkpoint mode | What becomes resumable |
|---|---|---|
| `faster-whisper` | `segment` | Each accepted segment during decoding |
| `whisper` | `window` | Complete 60-second transcription windows |
| `whisperx` | `window` | Complete ASR **and alignment** windows; diarization runs afterward |
| `whisper-cpp` | `window` | Complete windows with exit code 0 and complete valid JSON |

Window engines use fixed 60-second windows with 2 seconds of overlap (58-second
stride). The final window is clamped to the audio duration and ends the schedule;
for example, 118 seconds produces exactly `[0, 60]` and `[58, 118]`. These window
parameters are not user-configurable and are unrelated to `ChunkedTranscriber`'s
600/5 defaults or its optional VAD.

The durable **window cursor** records the end of the last completed window,
independently of the **last segment end**. A silent window therefore still
advances recovery. Segments from an interrupted window may already be durable,
but without its completion marker the whole window is recomputed on resume.
Completed windows are not decoded again, so the normal recomputation bound is
one window. This is **at-least-once**, not exactly-once.

Resume is always an explicit action after server restart recovery marks a job
interrupted. It is offline and reuses durable audio without contacting the
original source. Validated durable prefix segments are combined with accepted
suffix segments, with overlap de-duplication, so resumed Markdown retains the
prefix. Segment starts must be nonnegative, ends must stay within the stored
audio duration (plus 0.05 seconds tolerance) and advance beyond the durable
cursor; overlapping starts are valid only when the end advances. Persisted
words must be objects with finite, ordered timestamps inside their segment
within the same tolerance. Corrupt timelines fail closed with
`CHECKPOINT_CONFLICT`. A segment commit failure leaves the current window
uncommitted. A missing or inconsistent audio/decode/window identity or
ambiguous boundary text fails closed rather than guessing.

WhisperX persists aligned word timestamps for transcript reconstruction, but
does not persist diarization state: diarization reruns over the complete resumed
aligned result. whisper.cpp rejects nonzero exits even if a JSON file exists,
and rejects missing, malformed, or incomplete JSON. Its official
`result.language` is persisted at each completed window and reused for later
and resumed windows when language detection is automatic. None of these engines
commits partially streamed segments from stdout or stdout text.

Cookies, Hugging Face tokens, API keys, and other secrets are never persisted in
checkpoint request, selection, snapshot, or event data. A WhisperX diarization
resume that needs a token checks the current runtime configuration before
creating a new attempt and returns `RESUME_AUTH_REQUIRED` if unavailable. See
[Web UI](web-ui.md#post-apijobsjob_idresume) for the explicit resume contract.
