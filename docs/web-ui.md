# Web UI

A minimal browser interface to the MediaScribe pipeline.  The page submits
one or more sources via `POST /api/jobs`, creates one background job per
unique source, streams stage-level progress over
`WS /ws/progress/{job_id}`, and fetches the final Markdown via
`GET /api/jobs/{job_id}/result`.

## Quick start

```bash
pip install "mediascribe[web,faster-whisper]"
python -m mediascribe.web.app --port 8000
```

Open `http://127.0.0.1:8000`.  The bundled UI is vanilla HTML / CSS / JS;
there is no front-end build step.

The footer, `/api/health`, and the OpenAPI document all receive the version
from the package's single version source.  Do not hard-code a release number
in the page or API responses.

### Readiness before submitting

`GET /api/health` is a liveness response with a nested `readiness` object:

| `readiness.status` | Meaning | UI submit button |
|---|---|---|
| `READY` | An engine is available and the default `small` weights are cached | Enabled |
| `READY_WITH_WARNINGS` | Automatic fallback to `whisper`, or the default weights are not cached | Enabled; follow `warnings` / `actions` |
| `BLOCKED` | No supported ASR engine is available (`ENGINE_UNAVAILABLE`) | Disabled |

The UI also disables submission while readiness is unknown.  A successful
liveness response (`ok: true`) is not by itself proof that transcription is
ready; clients must inspect `readiness.status`.  Missing model weights are a
warning rather than a blocker.  Model downloads still require explicit
authorization through `allow_model_download` (default `false`).

## Endpoints

### `GET /`

The HTML page.  It polls `/api/health` every 5 s to render environment,
readiness, and GPU status.

### `GET /api/health`

Returns liveness, package version, authentication / rate-limit state,
`device_hint`, cached GPU information, and `readiness`:

```json
{
  "ok": true,
  "version": "<package version>",
  "auth_enabled": false,
  "gpu": { "...": "..." },
  "readiness": {
    "status": "READY",
    "engine": "faster-whisper",
    "model": "small",
    "model_cached": true,
    "blockers": [],
    "warnings": [],
    "actions": []
  }
}
```

The GPU probe is cached for 1 s server-side.  This endpoint also performs
opportunistic cleanup of expired live jobs and SQLite history.

### `POST /api/jobs`

Submit one or more sources as background jobs.  `urls` must contain 1–20
entries; invalid Pydantic input returns `422`.  Duplicate sources are
removed while preserving first-seen order, and the response reports both
`requested` and `unique` counts.

Omitting `engine` and `model` selects the shared automatic recommendation:
`faster-whisper` when installed, otherwise `whisper`; the default model is
`small`, subject to the resource guard.  An explicit engine / model always
wins.  Any resource-driven adjustment is returned in `notices`.

```json
{
  "urls": ["https://www.youtube.com/watch?v=..."],
  "language": "en",
  "ocr_engine": "auto",
  "allow_model_download": false
}
```

Request fields:

| Field | Default | Notes |
|---|---|---|
| `urls` | required | 1–20 HTTP(S) URLs or workspace-contained local paths |
| `engine` | `null` | Automatic recommendation; see allowed values below |
| `model` | `null` | Defaults to `small`; the Web schema does not accept `auto` |
| `language` | `null` | Automatic language detection when omitted |
| `ocr_engine` | `auto` | `auto`, `paddleocr`, `pytesseract`, `easyocr`, or `none` |
| `wechat_cookies` | `null` | Cookie string for WeChat MP |
| `save_images` | `false` | Persist WeChat article images |
| `bilingual` | `false` | Bilingual WeChat video-article output |
| `allow_model_download` | `false` | Authorize missing-weight downloads |

Response:

```json
{
  "jobs": [
    {
      "job_id": "abc123",
      "attempt_id": "<attempt id>",
      "retry_of": null,
      "url": "https://...",
      "status": "queued",
      "ws": "/ws/progress/abc123",
      "result": "/api/jobs/abc123/result"
    }
  ],
  "requested": 1,
  "unique": 1,
  "notices": []
}
```

### `GET /api/jobs/{job_id}`

Return a live or persisted job snapshot: `status`, `stage`, `stage_index`,
`stage_current`, `stage_total`, `progress_known`, `attempt_id`, `retry_of`,
`finished`, `cancelled`, `error`, `error_code`, structured `recovery`, and a
nested `checkpoint` containing committed segment count/index/end and safe `mode`
(`segment`, `window`, or `null` before identity is established). Unknown IDs
return `404`; purged live entries fall back to SQLite history. Internal audio
paths, fingerprints, decode hashes, and word timestamps are not exposed.

### `GET /api/jobs`

List live jobs plus SQLite history so refresh and server restart do not make
finished work disappear.  Live registry entries are newest-first and take
precedence over a same-ID history row; remaining history is appended
newest-first.  Entries include status, resume eligibility/reason, progress,
attempt, and checkpoint fields needed to rebuild the queue.

Live registry entries are purged after one hour once finished.  Terminal
SQLite rows are retained for seven days by default, and unfinished rows from
a previous process are marked as interrupted on startup.  Running jobs are
never purged.  The on-disk Markdown files are not deleted by these sweeps.

### `POST /api/jobs/{job_id}/cancel`

Request cancellation for **one job**.  The server sets that job's
`cancel_requested` state and calls `AsyncPipeline.cancel_one()` for its
source; sibling jobs in the same submission are not cancelled.  A queued
job can be interrupted before its runner starts.

The cancellation lifecycle is `cancel_requested` → safe-point completion
(`cancel_done()`, conceptually `cancelled_done`) → terminal snapshot.  The
actual terminal WebSocket event is named `cancelled`.  The request itself
does not claim that the worker has stopped:

```json
{"job_id": "abc123", "accepted": true, "status": "cancelling", "finished": false, "cancelled": true}
```

At the response point `cancelled` is true but `finished` may still be false.
The worker sets `finished=true` only after reaching a cooperative safe point
(stage boundaries, checkpoint-window boundaries, checks around blocking
operations, or the GPU-budget wait). If a worker raises after cancellation was
requested, the durable terminal state is still `cancelled`, never `failed`.
Python cannot forcibly interrupt an arbitrary native ASR call, so
completion can take time.  Clients should treat `finished && cancelled` as
the terminal REST state.  Unknown IDs return `404`; cancellation of an
already-terminal known job returns `200` with `accepted=false` and the
existing terminal status.

### `POST /api/jobs/{job_id}/resume`

Explicitly resume an interrupted durable job for `faster-whisper`, `whisper`,
`whisperx`, or `whisper-cpp`. Startup only marks unfinished jobs interrupted;
it never enqueues resume automatically. The server validates the audio
fingerprint, effective decode identity, segment/word timelines, segment
indexes/hashes, output path, checkpoint identity, and original download
authorization before creating a new `attempt_id`. Resume is offline: it reuses
the durable audio and stored source reference without DNS checks, short-link
resolution, source parsing, or downloader contact. New submissions still use
the normal SSRF/local-path validation. The `job_id` and output path stay stable.

Two checkpoint modes are available:

| Engine | Mode | Resume boundary |
|---|---|---|
| `faster-whisper` | `segment` | One-second overlap after the last committed segment |
| `whisper` | `window` | Recompute the active window, or start the next completed window, with 2-second overlap |
| `whisperx` | `window` | ASR+alignment per window; diarization reruns over the complete resumed aligned result |
| `whisper-cpp` | `window` | Only exit-code-0 windows with complete valid JSON are committed |

Window engines use fixed 60-second windows with 2-second overlap (58-second
stride). The last window is clamped to duration and ends the schedule: a
118-second file has exactly `[0, 60]` and `[58, 118]`, with no redundant tail.
A durable window cursor advances independently of the last segment cursor, so
a silent window still makes progress. An interrupted window may have durable
segments but no completion marker; resume recomputes that whole window and
deduplicates covered segments. Completed windows are skipped, giving a normal
recomputation bound of one window under **at-least-once** semantics, not
exactly-once.

Resume reconstructs the final transcript from the durable prefix plus accepted
suffix segments. If segment persistence or window completion fails, the window
is not marked complete. Audio/decode/window identity drift or ambiguous overlap
text fails closed. Cancellation is checked at safe window boundaries; Python
cannot interrupt an arbitrary in-flight native ASR call.

Example window-mode response:

```json
{"job_id": "abc123", "attempt_id": "new-attempt", "status": "queued", "resume_start": 58.0, "next_segment_index": 1, "checkpoint_mode": "window", "next_window_index": 1}
```

For `faster-whisper`, `checkpoint_mode` is `segment` and `next_window_index` is
`null`. Public job snapshots expose only checkpoint segment counts, indexes,
end times, and `mode`, not internal window scheduling or secrets. A changed
language, automatic/domain prompt, engine/model/device, or runtime fingerprint
returns HTTP `409 RESUME_INPUT_MISMATCH`; the interrupted snapshot, attempt ID,
and event history remain unchanged.

WhisperX diarization tokens and platform cookies/API keys are never persisted in
request, selection, snapshot, or checkpoint event data. If a diarization resume
requires a Hugging Face token that is absent from the current runtime, the
server returns HTTP `409` with `resume_block_reason=RESUME_AUTH_REQUIRED`
before creating a new attempt. Other ineligible jobs return `409` with their
resume block reason; unknown IDs return `404`.

The same generic **Resume/继续** button handles all four engines. A successful
resume replaces the attempt binding and clears stale result state; it does not
merge an old checkpoint object into the new attempt.

### `POST /api/jobs/{job_id}/retry`

Create a new attempt for a failed, non-cancelled live job whose structured
recovery metadata says `retryable: true`.  The original request snapshot
must still be available, and each original job can be linked to one retry.
The retry preserves the original selections and output path.

The response is the same shape as `POST /api/jobs`.  The new job has its own
`attempt_id` and `retry_of=<original job_id>`; the original result exposes
`retry_job_id`.  Ineligible jobs return `409`, and unknown / purged live IDs
return `404`.  This is not an automatic retry loop.

### `GET /api/jobs/{job_id}/result`

Return `finished`, `cancelled`, `status`, safe error / recovery metadata,
Markdown, summary metadata, and an `artifact` descriptor.  Before completion,
`markdown` is `null`.  If the live registry no longer has the job, the route
falls back to SQLite and the recorded output path.

### `GET /api/jobs/{job_id}/artifact`

Serve a completed Markdown file as `text/markdown` with inline content
disposition and a filename.  The response does not expose the server-side
path.  Missing jobs or files return `404`.  This is the URL used by the UI's
**Open transcript** action.

### `POST /api/jobs/{job_id}/events`

Record a content-free local artifact interaction:

```json
{"event": "artifact_opened", "action": "open"}
```

`action` is `open`, `copy`, or `download`; success returns `204`.  The UI
uses these actions for **Open transcript**, **Copy transcript**, and
**Download .md**.  Only event metadata is stored — not the transcript,
source URL, or server path.  The endpoint requires a completed, successful
live job; purged history is not a substitute for the live registry check.

### `WS /ws/progress/{job_id}`

The server first sends a `snapshot`, then streams job events:

| Event | Meaning |
|---|---|
| `stage_start` | A stage started; inspect `progress_known` |
| `stage_progress` | Stage counter advanced |
| `stage_done` | Stage reached its terminal counter value |
| `cancel_requested` | Cancellation was requested; the worker may still be running |
| `cancelled` | Worker reached a safe cancellation point; terminal |
| `succeeded` / `failed` | Terminal success / failure |
| `snapshot` | Current state on connect and at the end of the stream |
| `ping` | Keepalive plus a current job snapshot |

The stage identifiers, in progress-contract order, are:

```text
preflight → model_prepare → download → extract_audio → transcribe → assemble
```

The pipeline preserves the six real stage names. Download progress uses exact
byte totals when available; ASR segment progress uses committed segment end
against the probed audio duration, while completed window boundaries advance
progress even when the window contains no speech segments. Stages without a
reliable numerator remain
`progress_known: false` and render as processing. Boundary-only stages may use
a synthetic `0/1` or `1/1` counter until the first measured sample upgrades
them to `0/100`; always inspect `progress_known` before rendering a percentage.
No overall percentage or ETA is synthesized.

A client may send `{"event": "cancel"}` as an alternative to the REST cancel
endpoint.  It has the same single-job semantics.  The server sends `ping`
while idle; the client should answer with `{"event": "pong"}`.  Only a
received response refreshes the idle timer.  Defaults are a 300 s idle
timeout and a 3600 s maximum session, configurable through
`MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS` and
`MEDIASCRIBE_WS_MAX_SESSION_SECONDS`.

When authentication is enabled, use `Authorization: Bearer <token>` for HTTP
and either `Sec-WebSocket-Protocol: <token>` (preferred for browsers) or
`?token=<token>` for the socket.  Fatal close codes are `4404` (unknown job),
`1008` (authentication failed), and `1011` (internal server error).

### `GET /api/events`, `GET /api/events/export`, `DELETE /api/events`

Inspect the local-only event store:

* `GET /api/events?limit=100` returns `summary` and up to 1,000 events
  (default 100).
* `GET /api/events/export` returns a timestamped JSON attachment.
* `DELETE /api/events` clears the store and returns `{"cleared": N}`.

Events use a privacy whitelist and SQLite storage.  Retention defaults to
90 days; set `MEDIASCRIBE_EVENT_RETENTION_DAYS=0` to disable recording, or
use `MEDIASCRIBE_EVENT_DB` to relocate the database.  These routes require
the same bearer token as job routes when configured.

### `POST /api/transcribe` (legacy)

Synchronous transcription retained for backwards compatibility.  New
integrations should use `POST /api/jobs` plus the result / artifact routes.
It accepts the same source, engine / model, OCR, cookie, image, and bilingual
options; omitting `engine` uses automatic recommendation.

## Settings

* `engine` ∈ `whisper | faster-whisper | whisperx | whisper-cpp`
* `model` ∈ `tiny | base | small | medium | large | large-v1 | large-v2 |
  large-v3 | distil-large-v2 | distil-large-v3`
* `ocr_engine` ∈ `auto | paddleocr | pytesseract | easyocr | none`

`whisperx` is opt-in; it is not the automatic default.  Engine construction
has no fallback chain.  Automatic selection and resource-driven adjustments
are handled by the shared configuration / runtime guard, while explicit user
choices are protected.

## CLI flags

```bash
python -m mediascribe.web.app --host 127.0.0.1 --port 8000 --reload
python -m mediascribe.web.app --workspace "D:/MediaScribe/web-workspace"
```

## Production notes

* **Non-loopback binds require a token.**  Set `MEDIASCRIBE_API_TOKEN` before
  binding to `0.0.0.0` or another non-loopback host.  Startup rejects an
  unprotected non-loopback bind.  An explicit
  `MEDIASCRIBE_ALLOW_UNSAFE_BIND=1` development override exists, but must not
  be used for an exposed deployment.
* **CORS is restrictive by default.**  The built-in allowlist covers local
  HTTP origins, extension origins, and `file://`.  Set
  `MEDIASCRIBE_CORS_ORIGINS` to an explicit list (or `*` only when intended).
* **SSRF and local-file checks apply at submission.**  Dangerous URL schemes,
  private / reserved network targets, and local paths outside the workspace
  are rejected by default.
* **Pipeline reuse is bounded and thread-safe.**  The Web cache holds up to
  eight transcribers keyed by `(engine, model, device, language)`.  Cache
  hits share the heavy transcriber but use a fresh per-request `Settings`;
  downloaders are not shared across requests.
* **Job execution is not a durable distributed queue.**  SQLite preserves
  checkpoint state for single-server restart recovery and explicit resume, but
  does not provide multi-process claiming or high availability. Use a real
  queue for multi-process deployment.
* **Outputs live under the workspace.**  Markdown is written to
  `web-workspace/out/` by default, with `jobs.sqlite3` in the workspace root.
  Add an external cleanup policy for old artifacts; the job sweeps do not
  remove Markdown files.
