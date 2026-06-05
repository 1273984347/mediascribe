# Video2Text Browser Extension

A small Chrome/Edge extension (Manifest v3) that sends the URL of
the active tab to a self-hosted **Video2Text Web UI** and shows the
returned Markdown transcript.  The same UI runs in the toolbar
popup and in the browser **Side Panel** so you can keep a
transcript open while the source page scrolls.

> **Chrome / Edge 114 or newer is required** for Side Panel
> support.  Older Chromium builds still get the toolbar popup
> but the side panel button is hidden.

The extension itself is **client-side only** — it makes HTTP
requests to a Web UI endpoint that you control.  No data leaves
your machine except the URL you choose to send.

## Install (developer mode)

### Option A — download the ZIP from a running Web UI

If you have the Web UI running locally, point your browser at:

```
http://127.0.0.1:8000/extension
```

and click the **Download extension ZIP** button.  The ZIP is
self-contained and embeds your Web UI origin in
`INSTALL.md` so you do not have to edit the manifest.

Then:

1. Extract the ZIP somewhere permanent (e.g. `~/video2text/extension`).
2. Open `chrome://extensions/` (or `edge://extensions/`).
3. Enable **Developer mode** (top right).
4. Click **Load unpacked** and select the extracted directory.
5. Click the Video2Text icon in the toolbar → the popup opens.
   On first run, the **options** page asks for the Web UI URL
   (default `http://127.0.0.1:8000`).

### Option B — clone the repo

1. Start the Web UI on your machine:
   ```bash
   pip install "video2text[web]"
   python -m video2text.web.app --port 8000
   ```
2. Open `chrome://extensions/` (or `edge://extensions/`).
3. Enable **Developer mode** (top right).
4. Click **Load unpacked** and select the `extension/` directory.
5. Click the Video2Text icon in the toolbar → the popup opens.
   On first run, the **options** page asks for the Web UI URL
   (default `http://127.0.0.1:8000`).

## Toolbar popup vs Side Panel

| Surface | How to open | Best for |
|---------|-------------|----------|
| **Toolbar popup** | Click the toolbar icon | Quick one-off transcripts |
| **Side Panel** | Right-click toolbar icon → *Always show in side panel*, or click *Open in side panel* inside the popup | Long transcripts, hands-free reading |

Both surfaces share the same `popup.html` / `popup.js` / `popup.css`,
detect which one is active via the `chrome.sidePanel` API presence,
and apply a wider layout for the panel.

## Files

| File | Purpose |
|------|---------|
| `manifest.json` | Manifest v3 declaration (declares `sidePanel` permission, `minimum_chrome_version: 114`) |
| `popup.html` / `popup.js` | The toolbar popup / side panel UI (shared) |
| `popup.css` | Extracted stylesheet with light/dark theme variables |
| `options.html` / `options.js` | The settings page |
| `background.js` | Service worker — configures the side panel on install, handles the `v2t/openSidePanel` message bus |
| `icons/` | PNG icons at 16/48/128 px (provide your own) |

## How it talks to the Web UI

The popup POSTs to `<endpoint>/api/transcribe`:

```json
{
  "urls": ["https://www.youtube.com/watch?v=..."],
  "engine": "whisper",
  "model": "small"
}
```

The response is rendered in a `<pre>` block; users can copy
the markdown to clipboard with the system shortcut.

## Web UI endpoints used by the extension

| Endpoint | Direction | Purpose |
|----------|-----------|---------|
| `POST /api/transcribe` | extension → Web UI | Submit a URL for transcription |
| `GET /api/health` | extension → Web UI | Health check on popup open |
| `GET /extension` | browser → Web UI | Landing page with the *Download extension ZIP* button |
| `GET /api/extension/download` | browser → Web UI | Stream a self-contained ZIP of `extension/` |
| `GET /api/extension/install.md` | browser → Web UI | Install steps as Markdown (`?raw=1`) or styled HTML |

## Limitations

* Only Manifest v3 is supported. Firefox MV3 is in progress upstream.
* The icon files in `icons/` are placeholders. Drop in your own
  PNGs of the same names.
* CORS: the Web UI's `FastAPI` must allow requests from
  `chrome-extension://<id>`. Default FastAPI CORS is permissive
  (`*`); tighten it in production.
* The `extension_builder` ZIP only bundles the `extension/`
  tree.  It does **not** include the Web UI source — you still
  need to install `pip install "video2text[web]"` to serve the
  backend.
