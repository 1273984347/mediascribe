# MediaScribe

<img src="assets/compact/c1-video-to-text.jpg" alt="A video turning into clean Markdown transcript lines" width="520" />

*Local ASR turns a video into a clean Markdown transcript; network access is only needed for URL downloads, model-weight acquisition, or an explicitly enabled LLM.*

> Transcribe videos from 6 platforms (Bilibili, Douyin, YouTube, Xiaohongshu,
> WeChat MP, TikTok) — plus local files — to Markdown using local Whisper models.

MediaScribe is a single tool that turns a video URL into a clean
Markdown transcript.  It handles every step of the pipeline:

1. **Detect** the platform from the URL.
2. **Download** the source media (audio + video).
3. **Transcribe** with `faster-whisper` + `small` by default.
4. **Render** to a Markdown file with a metadata header and segmented body.

ASR inference stays on your machine. URL fetching and missing model-weight
downloads need network access; local files with pre-provisioned weights can run
fully offline. Optional LLM post-processing is off by default and, when enabled,
sends truncated transcript text plus the title and source URL to your configured
OpenAI-compatible endpoint.

The tool ships with four interfaces:

* **CLI** — `python -m mediascribe transcribe "<URL>"`
* **MCP server** — exposes the same operations as LLM-callable tools
* **Web UI** — FastAPI + HTML for non-technical collaborators
* **Browser extension** — sends the current tab URL to a self-hosted Web UI

## Why MediaScribe?

* **One tool, six platforms** — no need to remember which downloader
  to invoke per site.
* **Predictable engine default** — `faster-whisper` + `small`; if
  faster-whisper is unavailable, `whisper` is the compatibility fallback with
  a visible notice. WhisperX is opt-in, not an install-time priority.
* **Resource-aware** — on a memory-constrained host it may adjust a
  non-explicit engine/model choice and records the reason instead of dying.
* **Bilingual output** — WeChat MP videos with subtitles can be
  rendered as original + English side-by-side.
* **Honest about failure** — the JSON sidecar records every step
  and its error so a partial transcript is still useful.

## Quick start

```bash
pip install "mediascribe[faster-whisper]"

# Online first use: explicitly authorize missing model weights
python -m mediascribe transcribe \
  "https://www.youtube.com/watch?v=..." \
  --allow-model-download

# Offline after pre-provisioning the engine's model cache
python -m mediascribe doctor --offline
python -m mediascribe transcribe "video.mp4"
```

You will find the transcript at
`output/transcripts/<title>-<timestamp>.md` and a JSON sidecar at
`output/metadata/<title>-<timestamp>.json`. The Markdown body has no timestamp
prefixes unless you pass `--timestamps`.

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
| Version | 3.4.0a1 |
| License | MIT |
| Python | 3.11 – 3.13 |
| Tests | live CI badge (1000+ tests) |
| Platforms | 6 (+ local files) |
| Engines | 4 (faster-whisper default; WhisperX opt-in) |

## Local observability

Inspect privacy-whitelisted local events and first-use metrics without uploading
them:

```bash
python -m mediascribe events list
python -m mediascribe events export --output output/events.json
python -m mediascribe events metrics
python -m mediascribe events clear
```

Events are retained locally for 90 days by default. `clear` keeps the install
identity but removes event history. See
[Getting started](getting-started.md#9-local-events-and-metrics) for metric
denominators and post-clear behaviour.

## More

* [Getting started](getting-started.md) — install, first run, CLI reference
* [Engines](engines.md) · [Platforms](platforms.md) · [Plugins](plugins.md)
* [MCP server](mcp.md) — the agent-facing interface
* [Benchmarking](benchmarking.md) — how the engine numbers were measured
* [Contributing](contributing.md) · [Changelog](changelog.md) · [License](license.md)
