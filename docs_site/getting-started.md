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
python -m mediascribe --url "https://www.youtube.com/watch?v=..." \
  --model small --lang en
```

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
python -m mediascribe --url "https://mp.weixin.qq.com/s/abc?__biz=..." \
  --wechat-cookies "wxuin=abc123; pass_ticket=xyz" \
  --ocr-engine easyocr --save-images
```

Images land in `output/downloads/wechat_mp_<id>/`; the markdown
gets an `![alt](file://...)` reference for each.  See
[WeChat MP](wechat-mp.md) for the full guide.

## 4. Batch from a creator's user page

```bash
python douyin_batch_v3.py --user "https://www.bilibili.com/..." \
  --max 10 --platform bilibili --lang zh --json
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
python -c "import mediascribe; print(mediascribe.__version__)"
python -m pytest douyin_batch/tests/ -q
```

You should see `3.0.0` and `240 passed` (or thereabouts).
