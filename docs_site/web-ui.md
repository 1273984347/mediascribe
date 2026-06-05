# Web UI

A minimal browser interface to the Video2Text pipeline.  The
page calls `POST /api/transcribe` for each URL pasted in the
textarea and renders the returned Markdown.

## Quick start

```bash
pip install "video2text[web]"
python -m video2text.web.app --port 8000
```

Open `http://127.0.0.1:8000`.

## Endpoints

### `GET /`

The HTML page.  Pure vanilla JS — no React, no build step.

### `GET /api/health`

Liveness probe.  Returns `{"ok": true, "version": "3.0.0"}`.

### `POST /api/transcribe`

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
python -m video2text.web.app --host 0.0.0.0 --port 8000 --reload
```

## Production notes

* The default FastAPI CORS is permissive (`*`); tighten in
  production if you do not want any browser to call the API.
* The `/api/transcribe` endpoint runs synchronously; for a
  busy deployment, put it behind a queue (Celery / RQ / arq).
* The transcripts are written to `web-workspace/out/<id>.md`
  on the server.  There is no built-in cleanup; add a cron job
  to delete files older than 7 days.
