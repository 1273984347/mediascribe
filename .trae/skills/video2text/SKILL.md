---
name: "video2text"
description: "Transcribes videos from 6 platforms (Bilibili, Douyin, YouTube, Xiaohongshu, WeChat MP, TikTok) to Markdown using Whisper. Invoke when the user wants to transcribe a single video URL, batch-transcribe a creator's feed, or extract text from a WeChat MP article."
---

# Video2Text

A unified Python tool that downloads a video from one of six supported
platforms, optionally extracts its audio, and transcribes it with one of
three Whisper variants.  Output is always Markdown with a metadata
header (title, source URL, engine, language, segment count).

## When to use this skill

- The user pastes a single video URL and asks for a transcript.
- The user wants to transcribe a creator's full feed (give a user page
  URL or BV/UID).
- The user pastes a WeChat MP article and wants the text + image OCR.
- The user wants a side-by-side comparison of `whisper` vs
  `faster-whisper` vs `whisperx` on a specific file.
- The user wants to spin up a local Web UI to share with non-technical
  collaborators.

## When NOT to use this skill

- The user wants a quick *summary* of a long video → use the
  transcript and ask a separate summarizer skill.
- The user wants to translate the transcript → re-prompt with the
  transcript markdown.
- The user wants live captions → use a streaming ASR skill, not this
  batch tool.

## How to invoke

### 1. Single video (most common)

```bash
python -m video2text --url "https://www.youtube.com/watch?v=..." \
    --model small --lang en
```

Output: a single `.md` file plus a sibling `.json` metadata file in
`output/transcripts/`.

### 2. WeChat MP article (text + image OCR)

```bash
python -m video2text --url "https://mp.weixin.qq.com/s/xxx?__biz=..." \
    --wechat-cookies "wxuin=abc; pass_ticket=xyz" \
    --ocr-engine easyocr --save-images
```

### 3. Batch from a creator's user page

```bash
python douyin_batch_v3.py --user "https://www.bilibili.com/..." \
    --max 10 --platform bilibili --lang zh --json
```

### 4. MCP tool (when the agent is an MCP client)

The project ships an MCP server exposing two tools:

* `transcribe_url(url, engine, model, language, ocr_engine, wechat_cookies)`
* `validate_url(url)` (returns the detected `SourceRef.kind`)

Start the server with `python -m video2text.mcp_server` and connect
your MCP client to the stdio transport.

### 5. Web UI

```bash
pip install "video2text[web]"
python -m video2text.web.app --port 8000
```

Open `http://127.0.0.1:8000` in a browser. The bundled browser
extension (`extension/`) ships a popup that POSTs the current tab
URL to this Web UI.

### 6. Performance benchmark (sandbox-safe)

```bash
python scripts/benchmark_transcribers.py
```

Returns a Markdown table comparing the three engines on cold start,
mean latency, throughput, and peak memory.  Mocked by default so it
runs in CI without GPU.

### 7. Real E2E smoke test (needs network)

```bash
VIDEO2TEXT_E2E=1 python -m pytest douyin_batch/tests/test_e2e_real_urls.py
python scripts/run_real_e2e.py --url-only            # detector only
python scripts/run_real_e2e.py --platform wechat_mp  # full pipeline
python scripts/run_wechat_mp_e2e.py --from-fixture   # WeChat-only
```

## Supported platforms

| Platform | Auto-detect | Notes |
|----------|-------------|-------|
| Bilibili | `bilibili.com`, `b23.tv` | BV-id and short links |
| Douyin | `douyin.com`, `v.douyin.com` | Douyin vod fallback to yt-dlp |
| YouTube | `youtube.com`, `youtu.be` | 4-client player rotation |
| Xiaohongshu | `xiaohongshu.com`, `xhslink.com` | Needs Playwright + cookies |
| WeChat MP | `mp.weixin.qq.com/s/...` | Text + OCR; cookies optional |
| TikTok | `tiktok.com` | yt-dlp generic |

## Engines

| Engine | Throughput | Accuracy | Optional? |
|--------|------------|----------|-----------|
| `whisper` | 1x baseline | good | hard dep |
| `faster-whisper` | ~3.5x | good | optional |
| `whisperx` | ~5x | best, with alignment | optional |

The factory falls back through this chain in order, so the tool
always works as long as the hard `whisper` dependency is installed.

## Output structure

```
output/
├── downloads/    # raw video files
├── audio/        # extracted audio (mp3/wav)
├── transcripts/  # *.md with the title + body
└── metadata/     # *.json with engine, segments, language, ...
```

Markdown files follow this template:

```markdown
# <title>
> Source: <url>
> Engine: whisper / Model: small / Language: en
> Segments: 42 / Duration: 03:15

[intro music]
Hello everyone, today we're going to...

[segment break at 00:42]
...
```

## Error handling

The CLI exits 0 if at least one URL succeeded, 1 if all failed.
Each Markdown is paired with a `.json` metadata file that records
`ok: true|false` plus a list of recoverable errors.  OCR failures
do NOT mark the article as failed — the article is saved with
`wechat_mp_status: partial`.

## Related files in this repo

- `video2text/pipeline.py` — main entry point
- `video2text/transcribers/factory.py` — engine selection
- `video2text/downloaders/` — one module per platform
- `docs/cli-screenshots/` — example outputs
- `docs/e2e-results.md` — how to run real-URL E2E tests
