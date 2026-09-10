# MCP server

MediaScribe ships an MCP server so LLM agents can transcribe
videos by calling tools.

## Quick start

```bash
pip install "mediascribe[mcp]"
python -m mediascribe.mcp_server
```

Connect your MCP client (Claude Desktop, Cursor, Cline, etc.)
to the stdio transport.

## Tools

### `validate_url`

```json
{ "url": "https://www.youtube.com/watch?v=..." }
```

Returns the detected `SourceRef.kind`.  Use it to gate a
`transcribe_url` call: if `validate_url` returns `unknown`,
the URL is not supported.

### `transcribe_url`

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "engine": "whisper",
  "model": "small",
  "language": "en",
  "ocr_engine": "auto",
  "wechat_cookies": null
}
```

Returns:

```json
{
  "ok": true,
  "url": "...",
  "engine": "whisper",
  "title": "...",
  "markdown": "# Title\n\n...",
  "metadata": { ... }
}
```

## Bilingual output

Set `bilingual: true` to get both the original and the
translated (or second-language) subtitles side-by-side.

## Failure modes

| Tool returns | Meaning |
|--------------|---------|
| `ok: false, error: "..."` | the download / OCR / transcription step failed |
| `ok: true, wechat_mp_status: "partial"` | OCR partial failure but the article text was saved |

The MCP server never throws on download errors; the agent
should always check the `ok` field.
