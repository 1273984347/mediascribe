"""
MCP (Model Context Protocol) server for MediaScribe.

Exposes MediaScribe's transcribe / batch / inspect capabilities as MCP tools
that AI agents (Claude Code, Cursor, Cline, Windsurf, Continue, etc.) can call
natively.

Two transports are supported:
- **stdio** (default) — for local agents (Claude Code, Cursor, Cline)
- **HTTP** (--transport http) — for remote agents

HTTP transport hardening (P1-4):
- ``MEDIASCRIBE_MCP_TOKEN`` — when set, every HTTP request must carry
  ``X-MCP-Token: <token>`` or ``Authorization: Bearer <token>``
  (401 otherwise).  Unset ⇒ auth disabled (stdio unaffected).
- Host header allowlist: only ``127.0.0.1:<port>``,
  ``localhost:<port>`` and extra hosts from
  ``MEDIASCRIBE_MCP_ALLOWED_HOSTS`` are accepted (403 otherwise) to
  prevent DNS rebinding.
- Request bodies larger than 1 MiB are rejected with 413.

Usage:
    # Install MCP SDK first:  pip install mcp
    # Then register in your agent's MCP config:
    #   { "mcpServers": { "mediascribe": { "command": "python",
    #     "args": ["-m", "mediascribe.mcp_server"] } } }
    #
    # Or run directly to test:
    #   python -m mediascribe.mcp_server
    #
    # HTTP transport:
    #   python -m mediascribe.mcp_server --transport http --port 8765

2026-11-03 拆分: tools.py(工具实现) / protocol.py(JSON-RPC 分发) /
transport.py(stdio+http 承载) — 本包 __init__ 全量 re-export 保持
``from mediascribe.mcp_server import X`` 的既有路径不变。
"""

from .protocol import _handle_request, _make_error, _make_response, _safe_handle_request
from .tools import (
    SCHEMA_VERSION,
    TOOL_HANDLERS,
    TOOL_LIST,
    _mcp_vault,
    _read_text_capped,
    _tool_batch_transcribe_creator,
    _tool_detect_platform,
    _tool_get_cache_stats,
    _tool_get_transcript,
    _tool_sanitize_filename,
    _tool_transcribe_video,
    _tool_transcribe_wechat_mp,
    _tool_validate_url,
    _tool_wiki_list,
    _tool_wiki_read_note,
)
from .transport import (
    _make_http_server,
    _mcp_expected_token,
    _mcp_host_ok,
    _mcp_token_ok,
    _origin_allowed,
    _run_http,
    _run_stdio,
    main,
)

__all__ = [
    "SCHEMA_VERSION",
    "TOOL_HANDLERS",
    "TOOL_LIST",
    "main",
    "_handle_request",
    "_safe_handle_request",
    "_make_http_server",
]
