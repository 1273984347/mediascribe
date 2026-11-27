# MCP server

MediaScribe ships an MCP server so LLM agents can transcribe videos, run
creator batches, validate inputs, and read local transcripts / wiki notes.
The registry in `mediascribe/mcp_server/tools.py::TOOL_LIST` is the source of
truth for tool names and input schemas.

## Quick start

```bash
pip install "mediascribe[mcp,faster-whisper]"
python -m mediascribe.mcp_server
```

Connect an MCP client to the stdio transport, for example:

```json
{
  "mcpServers": {
    "mediascribe": {
      "command": "python",
      "args": ["-m", "mediascribe.mcp_server"]
    }
  }
}
```

The server also supports `--transport http --port 8765`.  HTTP mode enforces
a Host allowlist and a 1 MiB body limit; set `MEDIASCRIBE_MCP_TOKEN` to require
`Authorization: Bearer <token>` or `X-MCP-Token`.

!!! warning "stdio transport is serial"
    The stdio loop handles one request at a time.  A long-running
    `transcribe_video` call blocks later requests, including `ping` and
    `tools/list`, and there is no protocol-level cancellation.  For batch or
    long-video work, prefer `batch_transcribe_creator` (a subprocess with a
    timeout) or the CLI / Web UI directly.

### Output paths

Pass an absolute `output_dir` when the caller needs a deterministic location.
For `transcribe_video` and `transcribe_wechat_mp`, relative paths are
resolved from the MCP server's working directory.  The batch tool runs the
`archive` CLI as a child process; when `MEDIASCRIBE_WORKSPACE` is set, that
child uses it as its working directory, so a relative `output_dir` is rooted
there.  Without it, the child inherits the server's working directory.

### Automatic selection and model authorization

When `engine` is omitted, all transcription tools use the shared automatic
recommendation: `faster-whisper` when installed, otherwise `whisper`.
`whisperx` remains opt-in.  For schemas that expose `whisper_model`, omission
uses `small`, subject to the resource guard.  Missing model weights are not
downloaded unless `allow_model_download: true` is explicitly supplied
(default `false`).

## Tools

### `transcribe_video`

Download a supported URL or read a local media file, then transcribe it to
Markdown.  This is synchronous and intended for one small / medium item.

```json
{
  "source": "https://www.youtube.com/watch?v=...",
  "language": "en",
  "whisper_model": "small",
  "engine": "faster-whisper",
  "allow_model_download": false,
  "output_dir": "output"
}
```

| Parameter | Required | Default | Values / meaning |
|---|---|---|---|
| `source` | yes | — | Supported video URL or local file path |
| `language` | no | `auto` | `auto`, `zh`, `en`, `ja` |
| `whisper_model` | no | `small` | `tiny`, `base`, `small`, `medium`, `large` |
| `engine` | no | automatic | `whisper`, `faster-whisper`, `whisperx`, `whisper-cpp` |
| `allow_model_download` | no | `false` | Authorize missing-weight downloads |
| `output_dir` | no | `output` | Transcript / workspace directory |

A successful tool result contains `source`, `transcript`, `audio`, `video`,
`engine`, `model`, `language`, and resource `notices`.  It returns paths, not
the full Markdown body; use `get_transcript` when the file is under the
configured transcript root.

### `batch_transcribe_creator`

Discover videos from a Douyin / Bilibili creator and transcribe them in a
bounded subprocess.  Supply exactly one of `user_url` or `from_video`.

```json
{
  "user_url": "https://space.bilibili.com/...",
  "max_videos": 10,
  "language": "zh",
  "engine": "faster-whisper",
  "allow_model_download": false,
  "output_dir": "output"
}
```

| Parameter | Required | Default | Values / meaning |
|---|---|---|---|
| `user_url` | one of two | — | Creator profile URL |
| `from_video` | one of two | — | Discover the creator from a video URL |
| `max_videos` | no | `10` | Integer 1–200; handler clamps out-of-range values |
| `language` | no | `zh` | Transcription language passed through the batch environment |
| `engine` | no | automatic | Same four engine names as above |
| `allow_model_download` | no | `false` | Authorize missing-weight downloads |
| `output_dir` | no | `output` | Batch output directory |

The child timeout defaults to 1800 s and can be changed with
`MEDIASCRIBE_BATCH_TIMEOUT`.  The tool returns the batch CLI's JSON result;
protocol-level failures are normalized as described below.

### `transcribe_wechat_mp`

Extract a WeChat MP article into Markdown.  Text articles skip ASR; video
messages are transcribed; image articles can run OCR.

```json
{
  "url": "https://mp.weixin.qq.com/s/...",
  "cookies_file": "cookies.txt",
  "whisper_model": "small",
  "engine": "faster-whisper",
  "allow_model_download": false,
  "output_dir": "output",
  "ocr_engine": "auto",
  "ocr_lang": "chi_sim+eng",
  "save_images": false
}
```

| Parameter | Required | Default | Values / meaning |
|---|---|---|---|
| `url` | yes | — | WeChat MP article URL |
| `cookies` | no | `null` | Cookie name → value object; highest priority |
| `cookies_file` | no | `null` | Netscape or JSON cookie file |
| `whisper_model` | no | `small` | `tiny`, `base`, `small`, `medium`, `large` |
| `engine` | no | automatic | Same four engine names as above |
| `allow_model_download` | no | `false` | Authorize missing-weight downloads |
| `output_dir` | no | `output` | Output directory |
| `ocr_engine` | no | `auto` | `auto`, `paddleocr`, `pytesseract`, `easyocr`, `none` |
| `ocr_lang` | no | `chi_sim+eng` | OCR language code |
| `save_images` | no | `false` | Persist article images next to the transcript |

Success returns `ok: true`, `url`, `mode` (`text` or `video`),
`transcript_path`, `audio_path`, `engine`, `language`, and `ocr_engine` (for
text mode).  The current transcription tool schemas do **not** expose a
`bilingual` option.

### `validate_url`

```json
{"url": "https://www.youtube.com/watch?v=..."}
```

Returns `url`, `safe`, and `reason`.  This uses the trusted-domain security
check, not merely URL syntax validation.  Do not pass a URL to a downloader
when `safe` is false.

### `sanitize_filename`

```json
{"name": "a/name with unsafe characters"}
```

Returns the original `name` and a Windows / Linux-safe `safe` filename.

### `detect_platform`

```json
{"url": "https://www.youtube.com/watch?v=..."}
```

Returns `url` and `platform`.  Depending on the input, the platform can be a
known source kind, `other` (an unrecognized HTTP(S) URL), `empty`, or
`unknown`.

### `get_cache_stats`

Takes no arguments.  Returns total / success / failed / skipped counts from
the batch `ProcessCache`.

### `get_transcript`

```json
{"path": "output/transcripts/example.md"}
```

Reads a UTF-8 Markdown file and returns `path`, `exists`, `size_bytes`, and
`content`.  Reads are sandboxed to `MEDIASCRIBE_TRANSCRIPT_ROOT` (default
`output/transcripts`) after symlink resolution and capped at 5 MiB.  Missing,
unsafe, non-file, oversized, or non-UTF-8 inputs return an error object
rather than the file body.

### `wiki_list`

Takes no arguments.  Lists raw transcript notes, aggregated wiki notes, and
the HOME index.  The vault root is `MEDIASCRIBE_VAULT_DIR`, otherwise
`<MEDIASCRIBE_WORKSPACE or output>/vault`.  `MEDIASCRIBE_WIKI=0` returns
`enabled: false`.

### `wiki_read_note`

```json
{"name": "wiki/概念 - Example"}
```

Reads one vault note by relative name, with or without `.md`.  Paths are
root-sandboxed, traversal / absolute paths are rejected, and reads are
capped at 5 MiB.  Success returns `name`, `exists`, `size_bytes`, and
`content`.

## Failure contract

A normal tool result is returned as JSON text in `content` with
`isError: false`.  Clients must not assume every tool success contains an
`ok` field; several utility tools return their own result shape.

The protocol layer normalizes handler failures in either of these forms:

* an exception raised by the handler;
* a returned object with `ok: false` or a non-empty `error_code`.

Both become an MCP tool error with `isError: true` and structured recovery
content:

```json
{
  "content": [
    {
      "type": "text",
      "text": "{\"error_code\": \"...\", \"error\": \"...\", \"recovery\": {...}}"
    }
  ],
  "structuredContent": {
    "error_code": "DOWNLOAD_FAILED",
    "error": "<safe localized explanation>",
    "recovery": {
      "code": "DOWNLOAD_FAILED",
      "category": "DOWNLOAD_FAILED",
      "scope": "download",
      "retryable": true,
      "actions": [{"id": "retry", "label": "<localized action>"}]
    }
  },
  "isError": true
}
```

`structuredContent.error_code` is the stable machine-readable code.
`structuredContent.recovery` adds scope, retryability, and next actions.
Unknown codes normalize to `UNKNOWN`.  The normalization path uses the safe
recovery catalog instead of forwarding the handler's raw error string or the
original exception type / message, so private paths and diagnostics are not
exposed to the MCP client.  Server-side logs may retain diagnostic detail.

A returned object that only has an `error` field (without `ok: false` or
`error_code`) is not part of this normalization rule; inspect the individual
tool contract above.  Unknown tool names are also returned with
`isError: true`.
