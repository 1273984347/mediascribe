# Video2Text v3.2.0a — Tier 2 Polish Release

> **Released:** 2026-06-06
> **Tag:** `v3.2.0a` (annotated)
> **Status:** Alpha — four new subsystems, no breaking API changes
> **Tests:** 612 passed (+139 from v3.1.0's 473), 9 skipped, 0 failed
> **Ruff:** 0 errors

This alpha drops four independently-usable subsystems that bring the
v3.1.0 toolchain closer to a self-driving nightly batch pipeline.  None
of them change the existing CLI or HTTP contract; everything is additive
and falls back to the legacy implementation when the optional dependency
or the feature flag is missing.

---

## 🚀 What's new

### 1. VAD-based long-video chunking

[audio_utils.py](file:///d:/1/video2text/video2text/audio_utils.py) ·
[transcribers/chunked.py](file:///d:/1/video2text/video2text/transcribers/chunked.py)

Long videos no longer get sliced on a hard 30 s clock.  When
[`webrtcvad`](https://pypi.org/project/webrtcvad/) is installed, the
chunked transcriber walks the audio in 20 ms PCM-16 frames at
aggressiveness mode 3 and seals each chunk on the first non-speech
window of **≥ 700 ms**.  A 5 s overlap is retained at the chunk edge so
ASR boundary effects never eat the first or last word of a sentence.

The legacy fixed-duration splitter is still used as a transparent
fallback when `webrtcvad` is not importable, so existing callers are
unaffected.

```python
from video2text.transcribers.chunked import ChunkedTranscriber

transcriber = ChunkedTranscriber(
    model="base",
    vad=True,            # auto-detects webrtcvad presence
    overlap_seconds=5,   # overlap between adjacent chunks
)
```

| Test class | What it covers |
|---|---|
| `TestWeBrTcVadToggle` | `webrtcvad` presence flag, fallback to fixed split |
| `TestVadBoundaries` | silence/speech mix, mid-word cut protection |
| `TestOverlapStitch` | 5 s overlap stitching, dedup at chunk boundary |
| `TestPcm16RoundTrip` | wav → int16 → VAD → chunks → reassembly |

20 new tests in
[test_vad_chunking.py](file:///d:/1/video2text/douyin_batch/tests/test_vad_chunking.py).

---

### 2. Cross-run persistent cache

[cache.py](file:///d:/1/video2text/video2text/cache.py)

Downloads and transcriptions are now memoised to **disk across
processes**, not just within a single Python run.  Layout follows the
[XDG Base Directory spec](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html):

| Platform | Default cache root |
|---|---|
| Linux | `$XDG_CACHE_HOME/video2text` (or `~/.cache/video2text`) |
| macOS | `~/Library/Caches/video2text` |
| Windows | `%LOCALAPPDATA%\video2text\cache` |

Override with the `VIDEO2TEXT_CACHE_DIR` environment variable.

Each entry is keyed by `SHA-256(platform, url, params)` and lives in
one of two index files (`downloads.json` / `transcripts.json`) for
O(1) lookup.  An **LRU cap (default 1 GiB / 4096 entries)** and a
**30-day TTL** prune are enforced on every write.  Stale or corrupt
index files are detected via a monotonic schema version and silently
rebuilt.  `Cache.prune()` is exposed for cron / CI garbage collection.

```python
from video2text.cache import Cache

c = Cache()                                # XDG-default location
c.put("downloads", "douyin:ABC", payload)   # persisted
c.get("downloads", "douyin:ABC")            # next process picks it up
c.prune()                                   # remove expired / over-cap
```

21 new tests in
[test_persistent_cache.py](file:///d:/1/video2text/douyin_batch/tests/test_persistent_cache.py)
cover XDG fallback, env override, LRU eviction at zero cap, TTL=0
semantics, OSError on `_save_index`, and 4 KB payload round-trip.

---

### 3. `profile` CLI sub-command

[__main__.py](file:///d:/1/video2text/video2text/__main__.py) ·
[profile_cli.py](file:///d:/1/video2text/video2text/profile_cli.py)

`python -m video2text profile <run.jsonl>` aggregates pipeline
profiling JSONL output into a Markdown report, a JSON summary, or a
CSV — the 3-axis breakdown (stage / event / percentiles) is identical
to the Web UI.  The dispatcher preserves the v3.1.0 `transcribe` /
`batch` entry points verbatim, so existing scripts and CI pipelines
are not touched.

```bash
# Markdown report, slowest 10 events
python -m video2text profile runs/2026-06-06.jsonl

# JSON summary, only events after 09:00 UTC, slowest 5
python -m video2text profile runs/2026-06-06.jsonl \
    --format json --top 5 --since 2026-06-06T09:00:00Z

# CSV, all events, naive timestamps treated as UTC
python -m video2text profile runs/2026-06-06.jsonl \
    --format csv --tz-aware
```

| Flag | Default | Meaning |
|---|---|---|
| `--top N` | 10 | Clamp the slowest-events table (0 = render header only) |
| `--since ISO` | — | Drop records whose timestamp is older than the given ISO-8601 |
| `--format` | `md` | `md` / `json` / `csv` |
| `--tz-aware` | off | Treat naive timestamps as UTC before comparison |
| `--output PATH` | stdout | Write to file instead of stdout |

26 new tests in
[test_profile_cli.py](file:///d:/1/video2text/douyin_batch/tests/test_profile_cli.py).

---

### 4. WebSocket real-time job progress

[progress.py](file:///d:/1/video2text/video2text/progress.py) ·
[web/app.py](file:///d:/1/video2text/video2text/web/app.py)

Long-running `/api/transcribe` and `/api/batch` jobs now publish a
**3-bar progress event** (download / transcribe / assemble) that the
dashboard can stream without polling.

The new `ProgressRegistry` lives on `app.state.jobs` and exposes
`create(url)`, `get(job_id)`, `cancel(job_id)`, `list()` and
`purge(max_age_seconds)`.  Three new endpoints wire it into FastAPI:

| Endpoint | Method | Behaviour |
|---|---|---|
| `/api/jobs/{job_id}` | `GET` | JSON snapshot of stage %, current event, timestamps, result. 404 if unknown. |
| `/api/jobs/{job_id}/cancel` | `POST` | Cooperative cancellation between chunk boundaries. 404 if unknown. |
| `/ws/progress/{job_id}` | `WS` | Snapshot on connect, then `progress` / `completed` / `cancelled` / `error` events. 4404 close if unknown. |

The WS handler bridges the background worker thread that owns the
queue via `asyncio.to_thread`, so the event loop never blocks on
`queue.get()`.  Unknown `job_id` triggers a **4404 close frame with
an `error` payload** so the client can show a clean message instead
of hanging.

```javascript
// Browser / dashboard snippet
const ws = new WebSocket(`ws://${host}/ws/progress/${jobId}`);
ws.onmessage = (e) => {
    const evt = JSON.parse(e.data);
    if (evt.type === "progress") {
        setBar("download",   evt.stages.download);
        setBar("transcribe", evt.stages.transcribe);
        setBar("assemble",   evt.stages.assemble);
    } else if (evt.type === "completed") {
        ws.close();
    } else if (evt.type === "error") {
        showError(evt.message);
    }
};
```

19 new tests across
[test_job_progress.py](file:///d:/1/video2text/douyin_batch/tests/test_job_progress.py)
and
[test_web_app_security.py](file:///d:/1/video2text/douyin_batch/tests/test_web_app_security.py)
cover registry lifecycle, cancellation between stages, WS snapshot
replay, 4404 on unknown id, and the 404 / 200 semantics of the REST
helpers.

---

## 📊 Coverage gap round 3

[test_coverage_gaps_3.py](file:///d:/1/video2text/douyin_batch/tests/test_coverage_gaps_3.py)

16 new tests target the 0%-or-low-% branches surfaced by the v3.2.0a
code review.  Net result:

| Module | v3.1.0 | v3.2.0a | Δ |
|---|---:|---:|---:|
| `video2text/progress.py` | — | 100% | new |
| `video2text/cache.py` | 92% | 94% | +2 |
| `video2text/profile_cli.py` | 93% | 97% | +4 |
| `video2text/transcribers/chunked.py` | 89% | 89% | flat |
| `video2text/web/app.py` | 71% | 82% | +11 |

---

## 🔧 Quality gate

```text
612 passed, 9 skipped in 213.52s
ruff check .                            # 0 errors
coverage report -m (video2text scope)   # 75% overall, all v3.2.0a modules ≥ 89%
```

---

## 📦 Install / upgrade

```bash
git fetch --tags
git checkout v3.2.0a
pip install -e ".[dev]"
pip install webrtcvad          # optional, for VAD chunking
pytest -q                      # 612 passed
python -m video2text --help    # transcribe / batch / profile sub-commands
```

---

## 🛣️ What's next

v3.2.0b (planned) will tackle:

- **Fully asynchronous pipeline** — `asyncio.gather` parallel
  download + transcode, with a queue-bounded worker pool sized from
  CPU count.
- **GPU acceleration end-to-end** — detect CUDA / Metal / ROCm and
  route `faster-whisper` / `whisperx` models through the device
  automatically.
- **Benchmark vs whisperx** — side-by-side WER + wall-clock on a
  fixed 1-hour corpus, with reproducible scripts under
  [scripts/benchmark_transcribers.py](file:///d:/1/video2text/scripts/benchmark_transcribers.py).

See [docs_site/roadmap-v3.2.md](file:///d:/1/video2text/docs_site/roadmap-v3.2.md)
for the full milestone breakdown.

---

## 📝 License

MIT — see [LICENSE](file:///d:/1/video2text/LICENSE).

## 🤝 Contributing

PRs welcome — see
[CONTRIBUTING.md](file:///d:/1/video2text/CONTRIBUTING.md) and
[CODE_OF_CONDUCT.md](file:///d:/1/video2text/CODE_OF_CONDUCT.md).
