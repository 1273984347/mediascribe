# Performance

MediaScribe includes a small performance utility module:
`mediascribe.performance`.

It provides four things:

1. **`@profile_step`** — decorator that records wall-clock duration in the
   active timing registry and can optionally append JSONL records.
2. **`PerformanceReport`** — collects the registry into a JSON-serialisable
   report with count / mean / min / max / total per step.
3. **`parallel_map`** — runs a callable over a list concurrently with a
   thread pool, returning results in input order.  Per-item exceptions are
   returned as exception objects rather than discarded.
4. **`DownloadCache`** — a single-run URL → on-disk-path cache so a repeated
   URL within a batch is not re-downloaded.

## Usage

```python
from pathlib import Path

from mediascribe.performance import (
    DownloadCache,
    PerformanceReport,
    parallel_map,
    profile_step,
)


@profile_step("download", log_to=Path("timings.jsonl"))
def fetch(url, settings):
    return settings.session.get(url).content


results = parallel_map(lambda u: fetch(u, settings), urls)
report = PerformanceReport.from_registry()
report.write("transcript.perf.json")
```

With no active per-run registry, timings use the process-level `STEP_TIMES` registry (each
label is capped at 500 samples).  `Pipeline(profile=True)` creates a
context-local registry so concurrent runs do not mix their timings.

## Per-step timings

After the run, inspect the report:

```bash
cat transcript.perf.json
```

```json
{
  "generated_at": "<UTC timestamp>",
  "steps": {
    "download": { "count": 10, "total_sec": 4.1, "mean_sec": 0.41, "min_sec": 0.2, "max_sec": 0.8 },
    "transcribe": { "count": 10, "total_sec": 25.7, "mean_sec": 2.57, "min_sec": 1.9, "max_sec": 3.4 }
  },
  "total_sec": 29.8
}
```

`PerformanceReport.write()` writes only when called; profiling does not
automatically create a sidecar next to every transcript.

## Profile CLI and Python parameters

The supported commands are:

```bash
python -m mediascribe profile timings.jsonl
python -m mediascribe profile timings.jsonl --top 5 --by-stage download --json
python -m mediascribe.profile_cli timings.jsonl --since 2026-06-01
```

`--by-stage` filters by label substring.  `--since` accepts an ISO date and
`--json` selects JSON instead of Markdown.  The CLI reports count, total,
mean, p50, p95, max, and min.  It does not implement `--watch`, `--format`, or
CSV output.

For Python callers, `Pipeline` accepts keyword-only `profile: bool = False`
and `profile_log: Path | None = None`:

```python
from pathlib import Path

from mediascribe.config import Settings
from mediascribe.pipeline import Pipeline

pipeline = Pipeline(
    Settings(engine="faster-whisper", model="small"),
    profile=True,
    profile_log=Path("timings.jsonl"),
)
```

The ordinary CLI / Web / MCP entry points do not expose these profiling
parameters.  `profile_log` is used only when `profile=True`.

## Why a thread pool for downloads

`yt-dlp`, `requests`, and `httpx` release the GIL during network I/O, so
threads work well.  Multiprocessing adds pickle overhead and slow startup.
The default worker count is capped at
`min(8, len(items), 2 * cpu_count)` to avoid file-descriptor exhaustion on
large runs.  This utility is for I/O-bound mapping; it is not a GPU executor.

## The download cache

```python
cache = DownloadCache()
cached_path = cache.get(url)  # None on miss
if cached_path is None:
    fresh = download(url)
    cache.put(url, fresh)
```

`DownloadCache` lives in a temporary directory; call `cache.clear()` to
remove it.  It is not persistent across runs.

A separate `mediascribe.cache.PersistentDownloadCache` supports cross-run
download reuse and is wired into the ordinary pipeline only when
`MEDIASCRIBE_DOWNLOAD_CACHE=1`. `PersistentChunkCache` also exists, but is
not connected to the ordinary CLI / Web / MCP paths; see
[Long videos](chunking.md).

Use `python -m mediascribe cache info` to inspect indexed entries and disk
usage, or `python -m mediascribe cache info --json` for automation. The command
is read-only and reports the chunk cache as not wired. Clear only indexed
cache entries with `python -m mediascribe cache clear --yes`; model weights,
learned terms, local events, workspace files, and the cache root are preserved.
`--cache-dir PATH` selects a different root, and the same option or the JSON
config key `cache_dir` directs `transcribe` / `batch` to that root.

## Runtime boundaries

* **Automatic engine selection:** `faster-whisper` is preferred when
  installed, with `whisper` as the compatible fallback.  `whisperx` is
  opt-in.  The factory only constructs the requested engine; resource
  selection lives in `runtime_guard`, not a factory fallback chain.
* **Web progress is stage-level and honest:** the six identifiers are
  `preflight`, `model_prepare`, `download`, `extract_audio`, `transcribe`, and
  `assemble`. Exact download-byte totals and committed ASR segment timelines
  produce measured sub-progress; other stages remain unknown. Cancellation is
  per job and becomes terminal only at a safe point. Interrupted faster-whisper
  jobs can be explicitly resumed from durable segment checkpoints; see
  [Web UI](web-ui.md).
* **MCP stdio is serial:** a long transcription blocks later MCP requests,
  and there is no protocol-level cancel.  Tool failures are normalized to
  `isError: true` with structured recovery rather than raw exceptions; see
  [MCP server](mcp.md).
* **Long-video VAD / chunk caching are Python API features:** neither is
  automatically enabled by the ordinary CLI, Web, or MCP entry points; see
  [Long videos](chunking.md).
