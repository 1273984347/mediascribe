# Video2Text

> Transcribe videos from 6 platforms (Bilibili, Douyin, YouTube, Xiaohongshu,
> WeChat MP, TikTok) to Markdown using Whisper.

Video2Text is a single tool that turns a video URL into a clean
Markdown transcript.  It handles every step of the pipeline:

1. **Detect** the platform from the URL.
2. **Download** the source media (audio + video).
3. **Transcribe** with one of three Whisper variants (auto-fallback).
4. **Render** to a Markdown file with a metadata header and segmented body.

The tool ships with three interfaces:

* **CLI** — `python -m video2text --url "..."`
* **MCP server** — exposes the same operations as LLM-callable tools
* **Web UI** — FastAPI + HTML for non-technical collaborators
* **Browser extension** — sends the current tab URL to a self-hosted Web UI

## Why Video2Text?

* **One tool, six platforms** — no need to remember which downloader
  to invoke per site.
* **Engine fallback** — picks `whisperx` if installed, otherwise
  `faster-whisper`, otherwise `whisper`.  No more "engine not found".
* **Bilingual output** — WeChat MP videos with subtitles can be
  rendered as original + English side-by-side.
* **Honest about failure** — the JSON sidecar records every step
  and its error so a partial transcript is still useful.

## Quick start

```bash
pip install "video2text[all]"

python -m video2text \
  --url "https://www.youtube.com/watch?v=..." \
  --model small --lang en
```

You will find the transcript at `output/transcripts/<title>.md`
and a JSON sidecar at `output/metadata/<title>.json`.

## Project layout

```
video2text/
├── pipeline.py              # main entry point
├── transcribers/            # whisper, faster-whisper, whisperx, chunked
├── downloaders/             # one module per platform
├── plugins/                 # entry_points-based extension API
├── web/                     # FastAPI app + static HTML
└── observability.py         # OTel-compatible mini-SDK

scripts/                     # CLI helpers (benchmark, E2E, recording)
extension/                   # Chrome / Edge browser extension
.trae/skills/                # Agent-callable SKILL.md files
```

## Project status

| Item | Value |
|------|-------|
| Version | 3.0.0 |
| License | MIT |
| Python | 3.8 – 3.12 |
| Tests | 240+ |
| Platforms | 6 |
| Engines | 3 (auto-fallback) |
