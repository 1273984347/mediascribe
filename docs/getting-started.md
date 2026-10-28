# Getting started

This page walks through the most common workflows.

## 1. Install

```bash
pip install "mediascribe[all]"
```

The `[all]` extra pulls every optional dependency (Whisper
engines, OCR, MCP, Web UI).  If you only need a subset:

```bash
pip install "mediascribe[whisperx]"        # specific engine
pip install "mediascribe[ocr]"             # OCR support
pip install "mediascribe[web]"             # Web UI
pip install "mediascribe[mcp]"             # MCP server
pip install "mediascribe[dev]"             # tests + lint
```

System dependencies:

* `ffmpeg` (and `ffprobe`) on `$PATH` for audio extraction
  and the chunked transcriber.
* A Whisper model cache directory writable by the user
  (default: `~/.cache/whisper`).

## 2. Transcribe a single video

```bash
python -m mediascribe transcribe "https://www.youtube.com/watch?v=..." \
  --model small --language en
```

Useful flags (see `python -m mediascribe transcribe --help` for
the full list): `--engine faster-whisper`, `--timestamps`,
`--audio-only`, `--cross-check small`, `--output`.

What you get:

```
output/
├── downloads/<title>.mp4
├── transcripts/<title>.md      ← the transcript
└── metadata/<title>.json       ← engine, segments, errors
```

Open the `.md` in any editor.  Each segment is on its own line
with a leading timestamp.

## 3. Transcribe a WeChat MP article

```bash
python -m mediascribe transcribe "https://mp.weixin.qq.com/s/abc?__biz=..." \
  --wechat-cookies "wxuin=abc123; pass_ticket=xyz"
```

OCR of embedded images is configured per request in the **Web UI**
or the **MCP server** (`ocr_engine`, `save_images`) — the CLI has no
OCR flags.  See [WeChat MP](wechat-mp.md) for the full guide.

## 4. Batch from a creator's user page

```bash
python -m mediascribe archive --user "https://www.bilibili.com/..." \
  -n 10 --platform bilibili --lang zh --json
```

## 5. Web UI

```bash
python -m mediascribe.web.app --port 8000
```

Open `http://127.0.0.1:8000` and paste up to 20 URLs at once.

## 6. MCP server (for LLM agents)

```bash
python -m mediascribe.mcp_server
```

Connect your MCP client (Claude Desktop, Cursor, Cline, etc.) to
the stdio transport.  See [MCP server](mcp.md) for the tool list.

## 7. Verify the install

```bash
python -m mediascribe doctor
python scripts/run_tests.py
```

`doctor` checks ffmpeg / GPU / engines / model caches in one pass;
`scripts/run_tests.py` forwards to pytest with the CI-matched flags.
