# CLI Screenshots

Auto-generated text snapshots of CLI help output. Useful for
documentation sites and for verifying that flags haven't drifted
between releases.

| File | Bytes | Description |
|------|------:|-------------|
| `mediascribe-help.txt` | 3259 | Main mediascribe CLI help (English) |
| `mediascribe-mcp-help.txt` | 343 | MCP server CLI options |
| `archive-help.txt` | 2138 | batch CLI / mediascribe archive (auto-detect language) |
| `archive-help-zh.txt` | 2138 | batch CLI forced to Chinese |
| `archive-help-en.txt` | 2838 | batch CLI forced to English |

## How to regenerate

```bash
python scripts/e2e/capture_cli_help.py
```
