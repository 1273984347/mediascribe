# Browser extension

A small Chrome / Edge extension (Manifest v3) that sends the
active tab URL to a self-hosted MediaScribe Web UI.  The same UI
runs in the **toolbar popup** and in the browser **Side Panel**,
so you can keep a transcript open while you scroll the source
page.

> **Chrome / Edge 114 or newer is required** for Side Panel
> support.  Older Chromium builds still get the toolbar popup
> but the side panel button is hidden.

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

1. Extract the ZIP somewhere permanent (e.g. `~/mediascribe/extension`).
2. Open `chrome://extensions/` (or `edge://extensions/`).
3. Enable **Developer mode** (top right).
4. Click **Load unpacked** and select the extracted directory.
5. Click the MediaScribe icon in the toolbar → the popup opens.
   On first run, the options page asks for the Web UI URL
   (default `http://127.0.0.1:8000`).

### Option B — clone the repo

1. Start the Web UI on your machine:

   ```bash
   pip install "mediascribe[web]"
   python -m mediascribe.web.app --port 8000
   ```

2. Open `chrome://extensions/` (or `edge://extensions/`).
3. Enable **Developer mode** (top right).
4. Click **Load unpacked** and select the `extension/`
   directory of the cloned repo.
5. Click the MediaScribe icon in the toolbar → the popup opens.
   On first run, the options page asks for the Web UI URL
   (default `http://127.0.0.1:8000`).

## Surface: toolbar popup vs Side Panel

| Surface | How to open | Width | Best for |
|---------|-------------|-------|----------|
| **Toolbar popup** | Click the toolbar icon | ~360 px | Quick one-off transcripts |
| **Side Panel** | Right-click the toolbar icon → *Always show in side panel*  ·  *or*  click the *Open in side panel* button inside the popup | full side-pane width | Long transcripts, hands-free reading while the page scrolls |

The popup HTML / JS / CSS are shared between the two surfaces.
The page detects which surface it is on via the
`chrome.sidePanel` API presence and applies a wider layout for
the panel (`body.is-side-panel`).

The same `prefers-color-scheme` stylesheet is used in both
modes, so light and dark themes look identical.

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
the markdown to clipboard with the system shortcut or download
it as a `.md` file.

## Files

| File | Purpose |
|------|---------|
| `manifest.json` | Manifest v3 declaration (declares `sidePanel` permission, `minimum_chrome_version: 114`) |
| `popup.html` / `popup.js` | The toolbar popup / side panel UI (shared) |
| `popup.css` | Extracted stylesheet with light/dark theme variables |
| `options.html` / `options.js` | The settings page |
| `background.js` | Service worker — configures the side panel on install, handles the `v2t/openSidePanel` message bus |
| `icons/` | PNG icons at 16/48/128 px |

## Web UI endpoints used by the extension

| Endpoint | Direction | Purpose |
|----------|-----------|---------|
| `POST /api/transcribe` | extension → Web UI | Submit a URL for transcription |
| `GET /api/health` | extension → Web UI | Health check on popup open |
| `GET /extension` | browser → Web UI | Landing page with the *Download extension ZIP* button |
| `GET /api/extension/download` | browser → Web UI | Stream a self-contained ZIP of `extension/` |
| `GET /api/extension/install.md` | browser → Web UI | Install steps as Markdown (`?raw=1`) or styled HTML |

`/api/extension/download` returns the ZIP with
`Content-Disposition: attachment`,
`X-Content-Type-Options: nosniff` and
`Cache-Control: no-store` so a misclick never caches a stale
build.  The builder refuses path-traversal entries (any
segment containing `..`, blank names, absolute paths) and
skips `__pycache__` / `.pyc` / `.swp` / `.tmp` / `.bak`.

## Limitations

* Only Manifest v3 is supported. Firefox MV3 is in progress upstream.
* The icon files in `icons/` are placeholders. Drop in your
  own PNGs of the same names.
* CORS: the Web UI's `FastAPI` must allow requests from
  `chrome-extension://<id>`. Default FastAPI CORS is permissive
  (`*`); tighten it in production.
* The `extension_builder` ZIP only bundles the `extension/`
  tree.  It does **not** include the Web UI source — you
  still need to install `pip install "mediascribe[web]"` to
  serve the backend.
