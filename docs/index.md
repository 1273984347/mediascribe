# MediaScribe

> Transcribe videos from 6 platforms (Bilibili, Douyin, YouTube, Xiaohongshu,
> WeChat MP, TikTok) — plus local files — to Markdown using local Whisper models.

MediaScribe is a single tool that turns a video URL into a clean
Markdown transcript.  It handles every step of the pipeline:

1. **Detect** the platform from the URL.
2. **Download** the source media (audio + video).
3. **Transcribe** with one of four ASR engines (auto-fallback).
4. **Render** to a Markdown file with a metadata header and segmented body.

The tool ships with four interfaces:

* **CLI** — `python -m mediascribe transcribe "<URL>"`
* **MCP server** — exposes the same operations as LLM-callable tools
* **Web UI** — FastAPI + HTML for non-technical collaborators
* **Browser extension** — sends the current tab URL to a self-hosted Web UI

## Why MediaScribe?

* **One tool, six platforms** — no need to remember which downloader
  to invoke per site.
* **Engine fallback** — picks `whisperx` if installed, otherwise
  `faster-whisper`, otherwise `whisper`, otherwise `whisper-cpp`.
  No more "engine not found".
* **Resource-aware** — on a memory-constrained host it prefers the
  leanest engine and downgrades the model size instead of dying.
* **Bilingual output** — WeChat MP videos with subtitles can be
  rendered as original + English side-by-side.
* **Honest about failure** — the JSON sidecar records every step
  and its error so a partial transcript is still useful.

## Quick start

```bash
pip install "mediascribe[all]"

python -m mediascribe transcribe \
  "https://www.youtube.com/watch?v=..." \
  -e faster-whisper -m small -l en
```

You will find the transcript at `output/transcripts/<title>.md`
and a JSON sidecar at `output/metadata/<title>.json`.

## Project layout

```
mediascribe/                  # the single product package
├── pipeline*.py              # main entry points
├── transcribers/             # whisper, faster-whisper, whisperx, whisper-cpp, chunked
├── downloaders/              # one module per platform
├── plugins/                  # entry_points-based extension API
└── web/                      # FastAPI app + static ES modules

tests/                        # pytest suite
docs/                         # this documentation site (source)
scripts/                      # dev scripts by role: bench/ e2e/ pipeline/ tools/ launch/ gates/
extension/                    # Chrome / Edge browser extension
examples/plugins/             # third-party plugin samples
```

## Project status

| Item | Value |
|------|-------|
| Version | 3.3.0 |
| License | MIT |
| Python | 3.11 – 3.13 |
| Tests | 1192 (1187 passed / 5 skipped) |
| Platforms | 6 (+ local files) |
| Engines | 4 (auto-fallback) |

## More

* [Getting started](getting-started.md) — install, first run, CLI reference
* [Engines](engines.md) · [Platforms](platforms.md) · [Plugins](plugins.md)
* [MCP server](mcp.md) — the agent-facing interface
* [Benchmarking](benchmarking.md) — how the engine numbers were measured
* [Contributing](contributing.md) · [Changelog](changelog.md) · [License](license.md)
