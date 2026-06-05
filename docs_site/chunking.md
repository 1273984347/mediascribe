# Long videos

For audio longer than ~30 minutes, feeding the whole file into
a Whisper model at once:

* blows the GPU memory budget on consumer cards (8–12 GB),
* produces inferior alignment (Whisper's attention is local),
* makes failures unrecoverable — one bad chunk kills the run.

Video2Text solves all three with a **chunk + overlap + merge**
strategy implemented in `video2text.transcribers.chunked.ChunkedTranscriber`.

## Quick start

```python
from video2text.transcribers import (
    get_transcriber, ChunkedTranscriber,
)

inner = get_transcriber("faster-whisper", model="small")
ct = ChunkedTranscriber(inner, chunk_seconds=600, overlap_seconds=5)
result = ct.transcribe("long_audio.wav", "transcript.md")
```

The result has:

* `text` — the merged transcript,
* `segments` — segments with **global** timestamps (offset back
  into the original audio),
* `chunks` — per-chunk metadata (start, end, error if any),
* `sidecar_path` — a `.chunks.json` file with the chunking plan.

## Configuration

| Parameter | Default | What it controls |
|-----------|---------|------------------|
| `chunk_seconds` | 600 | Target chunk size in seconds (10 min) |
| `overlap_seconds` | 5 | Overlap between consecutive chunks |
| `inner` | required | The actual ASR engine to call per chunk |

If the inner transcriber fails on a chunk, the wrapper records
the error and continues.  The final `.md` will contain text from
the chunks that succeeded; the `.chunks.json` will mark the
failures.

## Audio probe

The `split_audio` function uses `ffprobe` to determine the
audio length.  If `ffprobe` is missing and the file is `.wav`,
the stdlib `wave` module is used as a fallback.  If neither
works, `probe_duration` raises `RuntimeError` and the wrapper
returns a single-chunk result that effectively disables
chunking.

## Time-stamp merging

Each chunk's segments are offset by `chunk.start` so the merged
`segments` list is in global coordinates.  Overlap regions
may contain duplicate words; the merger does NOT deduplicate
(it's better to over-include than to lose words).  If you need
strict deduplication, post-process with `rapidfuzz` or
`difflib.SequenceMatcher`.

## Limitations

* No VAD-based splitting (silence detection).  VAD is left to
  a future version because the optional `silero-vad` package
  is heavy and not always desired.
* Chunks are decoded from the source every time.  A persistent
  chunk cache would be a v3.2 feature.
