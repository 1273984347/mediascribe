# Web UI

A minimal browser interface to the MediaScribe pipeline.  The
page submits URLs via `POST /api/jobs`, which spawns one background
job per URL.  Stage-level progress streams back over a WebSocket
(`GET /ws/progress/{job_id}`) and the final Markdown is fetched via
`GET /api/jobs/{job_id}/result`.

## Quick start

```bash
pip install "mediascribe[web]"
python -m mediascribe.web.app --port 8000
```

Open `http://127.0.0.1:8000`.

## Endpoints

### `GET /`

The HTML page.  Pure vanilla JS — no React, no build step.  Polls
`/api/health` every 5 s to render a GPU pill.

### `GET /api/health`

Liveness probe.  Returns `{"ok": true, "version": "3.2.0c", "gpu": {...}}`.

The `gpu` block is cached for 1 s server-side (the dashboard polls
every few seconds; each `torch.cuda.*` probe costs ~30 ms).

### `POST /api/jobs`

Submit one or more URLs as background jobs (v3.2.0c Tier 1).

```json
{
  "urls": ["https://www.youtube.com/watch?v=..."],
  "engine": "whisper",
  "model": "small",
  "language": "en",
  "ocr_engine": "auto",
  "wechat_cookies": "wxuin=abc; pass_ticket=xyz",
  "bilingual": false
}
```

Body is validated by Pydantic — `urls` must be a non-empty list of
length ≤ 20, or the server returns `422` (not `400`).

Response:

```json
{
  "jobs": [
    {"job_id": "abc123", "url": "https://...", "stage": "queued"}
  ]
}
```

### `GET /api/jobs/{job_id}`

Snapshot of a job's progress — `stage`, `stage_current`,
`stage_total`, `cancelled`, `finished`, `error`.

### `POST /api/jobs/{job_id}/cancel`

Cooperatively cancel a running job.  Sets `job.cancelled = True` and
`job.finished = True`, then if an `AsyncPipeline` is registered for
the job, calls `ap.cancel()` to tear down in-flight `asyncio.Task`s.

**v3.2.0e**: cancel propagation now reaches inside each `Stage.run()`.
Each stage checks `ctx.raise_if_cancelled()` before and after blocking
operations (download / ffmpeg / ASR / LLM post-process / disk write)
and raises `PipelineCancelled` when the event is set.
`run_with_progress` skips the 100% emit on cancel (cancellation =
explicitly unfinished, only 0.0 start is emitted).
`AsyncPipeline.run_batch` wraps cancelled tasks into
`_FailedResult(src, PipelineCancelled)` for caller-side distinction.

Returns `{"job_id": "...", "cancelled": true}`.  `404` if the job
is unknown.

### `GET /api/jobs/{job_id}/result`

Final result of a finished (succeeded / failed / cancelled) job.
Returns `{"finished": true, "cancelled": false, "markdown": "...", ...}`.

If the job is not yet finished, `markdown` is `null`.

### `WS /ws/progress/{job_id}`

Bidirectional WebSocket.  Server pushes stage events:

* `stage_start` — a stage (`download` / `extract_audio` /
  `transcribe` / `merge`) has begun
* `stage_progress` — intra-stage progress (`current` / `total`)
* `stage_done` — the stage has finished
* `cancelled` — `POST /api/jobs/{id}/cancel` was called (or the
  client sent `{"event": "cancel"}` over the socket).  The job is
  now marked terminal (`finished=True, cancelled=True`); the WS
  loop exits immediately after delivering this event.  The in-flight
  worker thread will observe the cancel at the next stage boundary
  and raise `JobCancelled` — but its terminal state is already
  reflected, so consumers do not need to wait for an additional
  "done" event.  (Direct `job.events` subscribers in-process will
  still see a separate `cancelled_done` event when the worker
  raises `JobCancelled`; this is not delivered over the WS.)
* `succeeded` / `failed` — terminal

Client may send `{"event": "cancel"}` over the socket as an
alternative to the REST endpoint.

### `POST /api/transcribe` (legacy)

Synchronous transcription endpoint retained for backwards
compatibility.  New integrations should use `POST /api/jobs` +
`GET /api/jobs/{id}/result`.

```json
{
  "urls": ["https://www.youtube.com/watch?v=..."],
  "engine": "whisper",
  "model": "small",
  "language": "en",
  "ocr_engine": "auto",
  "wechat_cookies": "wxuin=abc; pass_ticket=xyz",
  "save_images": false,
  "bilingual": false
}
```

Response:

```json
{
  "results": [
    {
      "url": "https://...",
      "ok": true,
      "engine": "whisper",
      "markdown": "# Title\n\n...",
      "title": "...",
      "wechat_mp_status": null,
      "ocr_success": 0,
      "ocr_total": 0
    }
  ]
}
```

## Settings

* `engine` ∈ `whisper | faster-whisper | whisperx`
* `model` ∈ `tiny | base | small | medium | large`
* `ocr_engine` ∈ `auto | paddleocr | pytesseract | easyocr | none`

## CLI flags

```bash
python -m mediascribe.web.app --host 0.0.0.0 --port 8000 --reload
```

## Production notes

* **CORS is restrictive by default.**  The built-in allowlist covers
  `http(s)://(localhost|127.0.0.1|host.docker.internal)(:port)?`,
  `chrome-extension://<id>`, `moz-extension://<id>`, and `file://`.
  Wildcard (`*`) is intentionally *not* the default — set
  `MEDIASCRIBE_CORS_ORIGINS=*` (or a comma-separated explicit list)
  to override.
* **SSRF protection (v3.2.0f).**  `POST /api/transcribe` and
  `POST /api/jobs` reject URLs whose scheme is in a denylist that
  covers both network protocols (`file://`, `ftp://`, `gopher://`,
  `data:`, `dict://`, `ldap://`, …) and browser / script schemes
  (`javascript:`, `vbscript:`, `blob:`, `view-source:`,
  `chrome-extension:`, `moz-extension:`, …).  Plain local file
  paths and platform short-link text (no `://`) are still
  accepted for the local CLI use-case.
* **Pipeline cache is thread-safe (v3.2.0f).**  The
  `(engine, model, device, language)`-keyed `Pipeline` cache
  (capacity 8, FIFO eviction) is guarded by
  `_PIPELINE_CACHE_LOCK`.  Cache hits construct a fresh `Pipeline`
  that shares the cached `transcriber` / `downloader` (heavy
  resources) but takes a per-request `Settings`, so concurrent
  requests cannot pollute each other's state.
* **Atomic writes (v3.2.0f).**  All pipeline text output
  (`pipeline.py`, `pipeline_stages.AssembleStage`,
  `transcribers/chunked.py`, `learn.py`, `cache.py`) goes through
  `_atomic_write_text`: write a temp file with PID + UUID8 in
  its name, then `os.replace` it onto the target.  Crashes
  mid-write never leave a half-written file; the temp file is
  cleaned up on exception.
* `POST /api/jobs` is asynchronous (background `ThreadPoolExecutor`,
  `max_workers=2`).  For a busy deployment, raise `max_workers` or
  front the API with a real queue (Celery / RQ / arq).
* `POST /api/transcribe` is synchronous; for a busy deployment put
  it behind a queue as well.
* The transcripts are written to `web-workspace/out/<id>.md` on
  the server.  `ProgressRegistry.purge(older_than_seconds=3600)`
  is invoked opportunistically on each `/api/health` probe (the
  dashboard polls every 5s, so finished jobs are reaped within a
  few seconds of the 1-hour TTL); the on-disk transcripts are not
  auto-deleted — add a cron job to delete files older than 7 days.
* On shutdown, `app.state.job_executor.shutdown(wait=False,
  cancel_futures=True)` is called to release worker threads and GPU
  memory.  Running tasks cooperate via the `job.cancelled` flag
  between stages.
