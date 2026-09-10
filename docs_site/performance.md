# Performance

MediaScribe includes a small performance utility module:
`mediascribe.performance`.

It provides three things:

1. **`@profile_step`** — decorator that records the wall-clock
   duration of a function into a global registry.  Useful for
   ad-hoc profiling of the pipeline.
2. **`PerformanceReport`** — collects the registry into a
   JSON-serialisable report (with mean / min / max / total
   per step) that can be written next to the transcript as
   `<name>.perf.json`.
3. **`parallel_map`** — run a callable over a list concurrently
   using a thread pool.  Most downloaders do network I/O, so
   threads are the right primitive.
4. **`DownloadCache`** — a single-run URL → on-disk-path cache
   so a repeated URL within a batch is not re-downloaded.

## Usage

```python
from mediascribe.performance import (
    profile_step, PerformanceReport, parallel_map, DownloadCache,
)

@profile_step("download")
def fetch(url, settings):
    return settings.session.get(url).content

results = parallel_map(lambda u: fetch(u, settings), urls)
report = PerformanceReport.from_registry()
report.write("transcript.perf.json")
```

## Per-step timings

After the run, inspect the report:

```bash
cat transcript.perf.json
```

```json
{
  "steps": {
    "download": { "count": 10, "total_sec": 4.1, "mean_sec": 0.41, ... },
    "transcribe": { "count": 10, "total_sec": 25.7, "mean_sec": 2.57, ... }
  },
  "total_sec": 29.8
}
```

## Why a thread pool for downloads

`yt-dlp`, `requests`, and `httpx` all release the GIL during
network I/O, so threads work well.  Processes (multiprocessing)
adds pickle overhead and a slow startup.  We cap workers at
`min(8, len(items), 2 * cpu_count)` to avoid FD exhaustion on
1000-URL runs.

## The download cache

```python
cache = DownloadCache()  # /tmp/v2t_dl_cache_xxx
cached_path = cache.get(url)   # None on miss
if cached_path is None:
    fresh = download(url)
    cache.put(url, fresh)
```

The cache lives in a temp directory; call `cache.clear()` to
remove it.  It is NOT persistent across runs (that would be a
v3.2 feature); for now it is purely a within-run optimisation
to avoid duplicate downloads in a batch.
