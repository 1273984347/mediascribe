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

This module uses the official `mcp` Python SDK if available; if not, it
falls back to a minimal stdio-JSON-RPC implementation that still works
with the most common MCP clients.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .models import TranscriptResult  # noqa: F401  (re-exported for type hints)

SCHEMA_VERSION = "mediascribe.mcp/v1"

TOOL_LIST = [
    {
        "name": "transcribe_video",
        "description": (
            "Download a video from Bilibili / Douyin / YouTube / Xiaohongshu / "
            "local file and transcribe its audio to Markdown. Returns the path "
            "to the transcript file plus metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "Video URL (Bilibili / Douyin / YouTube / Xiaohongshu) "
                        "or local file path"
                    ),
                },
                "language": {
                    "type": "string",
                    "enum": ["auto", "zh", "en", "ja"],
                    "default": "auto",
                },
                "whisper_model": {
                    "type": "string",
                    "enum": ["tiny", "base", "small", "medium", "large"],
                    "default": "small",
                },
                "output_dir": {
                    "type": "string",
                    "default": "output",
                },
            },
            "required": ["source"],
        },
    },
    {
        "name": "batch_transcribe_creator",
        "description": (
            "Discover all videos from a Douyin/Bilibili creator and transcribe "
            "them in batch. Supports resume / dedup via the local cache."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_url": {
                    "type": "string",
                    "description": "Creator profile URL",
                },
                "from_video": {
                    "type": "string",
                    "description": "Or: discover the creator from one of their videos",
                },
                "max_videos": {
                    "type": "integer",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 200,
                },
                "language": {
                    "type": "string",
                    "default": "zh",
                },
                "output_dir": {
                    "type": "string",
                    "default": "output",
                },
            },
            "anyOf": [{"required": ["user_url"]}, {"required": ["from_video"]}],
        },
    },
    {
        "name": "get_cache_stats",
        "description": "Return cache statistics: total / success / failed / skipped video counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "validate_url",
        "description": (
            "Check whether a URL is safe to fetch (HTTPS only, trusted domain, "
            "no embedded scripts). Use before passing user-supplied URLs into "
            "downloaders."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "sanitize_filename",
        "description": (
            "Convert an arbitrary string into a safe filename (Windows + Linux). "
            "Strips reserved names, control characters, and path separators."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "detect_platform",
        "description": (
            "Identify which platform a URL belongs to (bilibili / douyin / "
            "youtube / xiaohongshu / tiktok / wechat_mp / unknown)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "get_transcript",
        "description": (
            "Read an existing transcript file (.md) and return its content plus "
            "metadata header. If the file does not exist, returns an error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "transcribe_wechat_mp",
        "description": (
            "Extract a WeChat MP article (mp.weixin.qq.com/s?...) into a "
            "markdown transcript. For text articles the body is saved "
            "directly (no ASR). For video messages the embedded mp4 is "
            "downloaded and transcribed. For image articles, OCR is run "
            "with the requested engine. Cookies can be supplied to bypass "
            "login walls."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "WeChat MP article URL",
                },
                "cookies": {
                    "type": "object",
                    "description": (
                        "Optional cookies dict (name → value) for login-walled "
                        "articles. Highest priority."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "cookies_file": {
                    "type": "string",
                    "description": (
                        "Optional path to a cookie file. Accepts Netscape "
                        "or JSON formats."
                    ),
                },
                "whisper_model": {
                    "type": "string",
                    "enum": ["tiny", "base", "small", "medium", "large"],
                    "default": "small",
                },
                "output_dir": {
                    "type": "string",
                    "default": "output",
                },
                "ocr_engine": {
                    "type": "string",
                    "enum": ["auto", "paddleocr", "pytesseract", "easyocr"],
                    "default": "auto",
                    "description": (
                        "OCR engine for embedded images. ``auto`` (default) "
                        "tries paddleocr → pytesseract → easyocr in order. "
                        "Any unavailable engine is silently skipped."
                    ),
                },
                "ocr_lang": {
                    "type": "string",
                    "default": "chi_sim+eng",
                    "description": (
                        "OCR language code (pytesseract / easyocr style). "
                        "Examples: ``chi_sim+eng``, ``eng``, ``chi_tra+eng``."
                    ),
                },
                "save_images": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "If true, download and persist every image URL in the "
                        "article next to the markdown transcript (in addition "
                        "to running OCR)."
                    ),
                },
            },
            "required": ["url"],
        },
    },
]


def _tool_validate_url(args: Dict[str, Any]) -> Dict[str, Any]:
    from douyin_batch.security import is_safe_url
    url = args.get("url", "")
    return {"url": url, "safe": is_safe_url(url)}


def _tool_sanitize_filename(args: Dict[str, Any]) -> Dict[str, Any]:
    from douyin_batch.platform_compat import safe_filename
    return {"name": args.get("name", ""), "safe": safe_filename(args.get("name", ""))}


def _tool_detect_platform(args: Dict[str, Any]) -> Dict[str, Any]:
    url = (args.get("url", "") or "").lower()
    platform = "unknown"
    if not url:
        platform = "empty"
    elif "bilibili.com" in url or "b23.tv" in url:
        platform = "bilibili"
    elif "douyin.com" in url or "iesdouyin.com" in url:
        platform = "douyin"
    elif "youtube.com" in url or "youtu.be" in url or "youtube-nocookie.com" in url:
        platform = "youtube"
    elif "xiaohongshu.com" in url or "xhslink.com" in url:
        platform = "xiaohongshu"
    elif "tiktok.com" in url:
        platform = "tiktok"
    elif "mp.weixin.qq.com" in url:
        platform = "wechat_mp"
    elif url.startswith("http://") or url.startswith("https://"):
        platform = "other"
    return {"url": args.get("url", ""), "platform": platform}


def _tool_get_cache_stats(_: Dict[str, Any]) -> Dict[str, Any]:
    from douyin_batch.cache import ProcessCache
    cache = ProcessCache()
    return cache.get_stats()


def _tool_get_transcript(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return the contents of a transcript file, with safety constraints.

    The MCP server is meant to be hit by trusted local clients (a
    coding agent, a Web UI).  Even so, a naive ``Path(args['path'])``
    read is a remote-file-read primitive: any caller can ask for
    ``/etc/passwd`` or ``~/.ssh/id_rsa``.  This implementation pins the
    reachable root to a single directory and refuses anything else.

    Configuration:
        MEDIASCRIBE_TRANSCRIPT_ROOT — directory under which transcripts
        may be read (default: ``./output/transcripts``).  Relative paths
        are resolved against the current working directory.

    Limits:
        * File must exist and be a regular file (no directories, no
          device files, no sockets).
        * File must be a descendant of the configured root (after
          symlink resolution).
        * File must be at most 5 MiB; larger files return ``truncated``
          metadata without the full content.
    """
    max_bytes = 5 * 1024 * 1024  # 5 MiB
    root = Path(
        os.environ.get("MEDIASCRIBE_TRANSCRIPT_ROOT", "output/transcripts")
    ).expanduser().resolve()

    raw = str(args.get("path", "")).strip()
    if not raw:
        return {"path": raw, "exists": False, "error": "path is required"}

    try:
        candidate = Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        return {"path": raw, "exists": False, "error": f"cannot resolve path: {exc}"}

    # Containment check: the resolved candidate must live under ``root``.
    try:
        candidate.relative_to(root)
    except ValueError:
        return {
            "path": raw,
            "exists": candidate.exists(),
            "error": (
                f"path escapes the transcript root ({root}); "
                "set MEDIASCRIBE_TRANSCRIPT_ROOT to allow a different directory"
            ),
        }

    if not candidate.exists():
        return {"path": str(candidate), "exists": False, "error": "file not found"}
    if not candidate.is_file():
        return {
            "path": str(candidate),
            "exists": True,
            "error": "not a regular file",
        }

    size = candidate.stat().st_size
    if size > max_bytes:
        return {
            "path": str(candidate),
            "exists": True,
            "size_bytes": size,
            "truncated": True,
            "max_bytes": max_bytes,
            "error": f"file is larger than {max_bytes} bytes; refusing to inline",
        }

    try:
        text = candidate.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {
            "path": str(candidate),
            "exists": True,
            "size_bytes": size,
            "error": "file is not valid utf-8; refusing to inline",
        }

    return {
        "path": str(candidate),
        "exists": True,
        "size_bytes": size,
        "content": text,
    }


def _tool_transcribe_video(args: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronous transcription (small videos only — for large batch use the CLI).

    The actual ``Pipeline`` constructor signature is
    ``Pipeline(settings, downloader=None, transcriber=None)`` and the entry
    point is ``Pipeline.transcribe(source_input, **opts)`` returning a
    ``TranscriptResult``.  This wrapper maps the flat MCP argument shape to
    that API.
    """
    from mediascribe.config import Settings
    from mediascribe.pipeline import Pipeline

    source_input = args.get("source") or args.get("url") or args.get("path")
    if not source_input:
        raise ValueError("transcribe_video requires 'source' (URL or local path)")

    # The MCP tool is opinionated about defaults that may be different from
    # what the CLI uses.  Build a minimal Settings that honours the args
    # the caller passed through, falling back to safe defaults.
    model = args.get("whisper_model") or args.get("model") or "small"
    engine = args.get("engine") or "whisper"
    language = args.get("language")
    settings = Settings(
        workspace_root=Path(args.get("output_dir", "output")).resolve(),
        model=model,
        engine=engine,
        language=None if language in (None, "auto", "") else language,
    )
    pipeline = Pipeline(settings=settings)
    result: TranscriptResult = pipeline.transcribe(
        source_input=source_input,
        output=Path(args.get("output_dir", "output")).resolve(),
        language=None if language in (None, "auto", "") else language,
    )
    return {
        "source": source_input,
        "transcript": str(result.transcript_path),
        "audio": str(result.audio_path),
        "video": str(result.video_path) if result.video_path else None,
        "engine": result.engine,
        "model": result.model,
        "language": language or "auto",
    }


_BATCH_DEFAULT_TIMEOUT_SECONDS = 1800  # 30 min; 批量转录可能涉及多视频下载+ASR


def _tool_batch_transcribe_creator(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run the batch CLI as a subprocess and return the parsed JSON output.

    v3.2.0e+ 安全加固 (F-10):
      * 为 ``subprocess.run`` 添加 ``timeout`` 参数（默认 1800s = 30min,
        可通过环境变量 ``MEDIASCRIBE_BATCH_TIMEOUT`` 覆盖），
        防止批量 CLI 长时间阻塞 MCP 服务进程。
      * 捕获 ``subprocess.TimeoutExpired`` 异常，返回结构化错误而非崩溃
        MCP 工具调用链。
    """
    import subprocess
    env_val = os.environ.get("MEDIASCRIBE_BATCH_TIMEOUT", "").strip()
    if env_val:
        try:
            timeout = float(env_val)
        except ValueError:
            timeout = _BATCH_DEFAULT_TIMEOUT_SECONDS
    else:
        timeout = _BATCH_DEFAULT_TIMEOUT_SECONDS

    cmd = [
        sys.executable, "douyin_batch_v3.py",
        "--json",
        "--lang", "en",
    ]
    if args.get("user_url"):
        cmd.extend(["--user", args["user_url"]])
    elif args.get("from_video"):
        cmd.extend(["--from-video", args["from_video"]])
    # P1-4⑥: handler 层强制 clamp 到 schema 声明的 1-200 — 恶意/异常
    # 客户端可能绕过 schema 校验直接传 0 / 负数 / 超大值。
    try:
        max_videos = int(args.get("max_videos", 10))
    except (TypeError, ValueError):
        max_videos = 10
    max_videos = max(1, min(200, max_videos))
    cmd.extend(["-n", str(max_videos)])
    if args.get("language"):
        cmd.extend(["--language", args["language"]])
    if args.get("output_dir"):
        cmd.extend(["--output-dir", args["output_dir"]])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "error": f"batch transcribe timed out after {timeout:.0f}s",
            "cmd": cmd,
            "stdout": e.stdout or "",
            "stderr": e.stderr or "",
        }
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "non-JSON output", "stdout": proc.stdout, "stderr": proc.stderr}


def _tool_transcribe_wechat_mp(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract a WeChat MP article. Cookies may be supplied as either a dict
    (highest priority) or a file path. Returns a summary suitable for
    agents: {url, mode, transcript_path, audio_path, error}.
    """
    from mediascribe.config import Settings
    from mediascribe.inputs import parse_source
    from mediascribe.pipeline import Pipeline

    url = args.get("url", "").strip()
    if not url:
        return {"ok": False, "error": "url is required"}

    # Validate the URL is a WeChat MP link before invoking the pipeline
    src = parse_source(url)
    if src.kind != "wechat_mp":
        return {
            "ok": False,
            "error": f"url is not a WeChat MP article (detected kind={src.kind!r})",
            "url": url,
        }

    cookies = args.get("cookies") or None
    cookies_file = args.get("cookies_file") or None
    whisper_model = args.get("whisper_model", "small")
    output_dir = args.get("output_dir", "output")
    ocr_engine = args.get("ocr_engine", "auto")
    ocr_lang = args.get("ocr_lang", "chi_sim+eng")
    save_images = bool(args.get("save_images", False))

    settings = Settings(
        workspace_root=Path(output_dir).expanduser(),
        model=whisper_model,
        wechat_cookies=cookies,
        wechat_cookies_file=Path(cookies_file).expanduser() if cookies_file else None,
    )

    try:
        result = Pipeline(settings=settings).transcribe(
            url,
            ocr_engine=ocr_engine,
            ocr_lang=ocr_lang,
            save_images=save_images,
        )
        return {
            "ok": True,
            "url": url,
            "mode": (
                "video" if result.engine != "wechat_mp_text" else "text"
            ),
            "transcript_path": str(result.transcript_path),
            "audio_path": str(result.audio_path) if result.audio_path else None,
            "engine": result.engine,
            "language": result.language,
            "ocr_engine": ocr_engine if result.engine == "wechat_mp_text" else None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
        }


TOOL_HANDLERS = {
    "transcribe_video": _tool_transcribe_video,
    "batch_transcribe_creator": _tool_batch_transcribe_creator,
    "transcribe_wechat_mp": _tool_transcribe_wechat_mp,
    "get_cache_stats": _tool_get_cache_stats,
    "validate_url": _tool_validate_url,
    "sanitize_filename": _tool_sanitize_filename,
    "detect_platform": _tool_detect_platform,
    "get_transcript": _tool_get_transcript,
}


# ---------------------------------------------------------------------------
# Minimal stdio JSON-RPC server (works without the official `mcp` SDK)
# ---------------------------------------------------------------------------


def _make_response(req_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _handle_request(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # P1-4⑤: 畸形请求(params 非 dict / req 非 dict)不再抛 AttributeError,
    # 而是返回结构化 JSON-RPC error。
    if not isinstance(req, dict):
        return _make_error(None, -32600, "Invalid Request: body must be an object")
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params", {}) or {}
    if not isinstance(params, dict):
        return _make_error(req_id, -32602, "Invalid params: params must be an object")

    if method == "initialize":
        return _make_response(req_id, {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "mediascribe", "version": "2.1.0"},
            "capabilities": {"tools": {}},
        })

    if method == "notifications/initialized":
        return None  # notification, no response

    if method == "tools/list":
        return _make_response(req_id, {"tools": TOOL_LIST})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            # Per MCP spec, tool-not-found is reported as a result with isError=true
            return _make_response(req_id, {
                "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                "isError": True,
            })
        try:
            result = handler(args)
        except Exception as exc:
            return _make_response(req_id, {
                "content": [{"type": "text", "text": f"Error: {type(exc).__name__}: {exc}"}],
                "isError": True,
            })
        return _make_response(req_id, {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
            "isError": False,
        })

    if method == "ping":
        return _make_response(req_id, {})

    return _make_error(req_id, -32601, f"Method not found: {method}")


def _safe_handle_request(req: Any) -> Optional[Dict[str, Any]]:
    """``_handle_request`` 的防崩包装 (P1-4④)。

    任何畸形请求都返回 JSON-RPC error 对象(而不是抛异常), stdio 主
    循环 / HTTP handler 不会因一条坏消息退出。
    """
    try:
        return _handle_request(req)
    except Exception as exc:
        req_id = req.get("id") if isinstance(req, dict) else None
        return _make_error(req_id, -32603, f"Internal error: {type(exc).__name__}")


def _run_stdio() -> None:
    """Read JSON-RPC messages from stdin, write responses to stdout."""
    sys.stderr.write("[mediascribe MCP] stdio server ready\n")
    sys.stderr.flush()
    for raw in sys.stdin:
        line = raw.strip()
        # Strip optional UTF-8 BOM (some clients add it)
        if line.startswith("\ufeff"):
            line = line[1:]
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps(_make_error(None, -32700, f"Parse error: {exc}")) + "\n")
            sys.stdout.flush()
            continue
        resp = _safe_handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


# ---------------------------------------------------------------------------
# HTTP transport hardening (P1-4)
# ---------------------------------------------------------------------------
_MAX_REQUEST_BODY_BYTES = 1024 * 1024  # 1 MiB — 请求体上限 (P1-4③)


def _mcp_expected_token() -> str:
    return os.environ.get("MEDIASCRIBE_MCP_TOKEN", "").strip()


def _mcp_token_ok(headers: Any) -> bool:
    """P1-4①: 可选 token 鉴权。

    设置了 ``MEDIASCRIBE_MCP_TOKEN`` 时, 请求必须携带 ``X-MCP-Token``
    头或 ``Authorization: Bearer <token>``; 常数时间比较。未设置时不
    鉴权 — 默认本地 stdio 用法与既有部署行为完全不变。
    """
    expected = _mcp_expected_token()
    if not expected:
        return True
    get = getattr(headers, "get")
    presented = (get("X-MCP-Token") or "").strip()
    if not presented:
        auth = (get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            presented = auth.split(" ", 1)[1].strip()
    if not presented:
        return False
    import hmac
    return hmac.compare_digest(presented, expected)


def _mcp_host_ok(host_header: str, port: int) -> bool:
    """P1-4②: Host 头白名单, 防 DNS rebinding。

    仅允许 ``127.0.0.1:<port>`` / ``localhost:<port>`` 以及
    ``MEDIASCRIBE_MCP_ALLOWED_HOSTS``(逗号分隔)配置的额外主机; 额外
    主机条目可带端口(须精确匹配)或不带端口(匹配任意端口)。
    """
    host = (host_header or "").strip().lower()
    if not host:
        return False
    if host in (f"127.0.0.1:{port}", f"localhost:{port}"):
        return True
    for extra in os.environ.get("MEDIASCRIBE_MCP_ALLOWED_HOSTS", "").split(","):
        extra = extra.strip().lower()
        if not extra:
            continue
        if extra == host:
            return True
        if ":" not in extra and host.rsplit(":", 1)[0] == extra:
            return True
    return False


def _make_http_server(host: str, port: int) -> Any:
    """构建加固过的 HTTP server (不引入额外依赖)。

    P1-4: 请求处理顺序为 请求体上限(413) → Host 白名单(403) →
    token(401) → JSON-RPC; body 先于校验读取, 保证错误响应发出时
    连接可以干净关闭; 解析/处理任何异常都回 JSON-RPC error 对象。
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _reply_plain(self, code: int, message: str, **extra_headers: str) -> None:
            data = json.dumps({"error": message}).encode("utf-8")
            self.send_response(code)
            for key, value in extra_headers.items():
                self.send_header(key.replace("_", "-"), value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802
            # 先读 body(带 1 MiB 上限)再做鉴权/Host 校验 — 这样任何错误
            # 响应发出时请求体都已被消费, 连接可以干净关闭(否则 Windows
            # 下客户端会在读到响应前收到 RST)。
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > _MAX_REQUEST_BODY_BYTES:
                # P1-4③: 超限直接 413, 不缓冲 body。丢弃已声明的 body
                # (设硬上限防止恶意超大声明拖住服务线程)。
                self._reply_plain(413, "request body too large")
                self._drain(length)
                return
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            bound_port = self.server.server_address[1]
            if not _mcp_host_ok(self.headers.get("Host", ""), bound_port):
                self._reply_plain(403, "forbidden host")
                return
            if not _mcp_token_ok(self.headers):
                self._reply_plain(
                    401, "missing or invalid MCP token",
                    WWW_Authenticate="Bearer",
                )
                return
            try:
                req = json.loads(body)
                resp = _safe_handle_request(req)
                if resp is None:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req.get("id") if isinstance(req, dict) else None,
                        "result": {},
                    }
            except json.JSONDecodeError as exc:
                resp = _make_error(None, -32700, f"Parse error: {exc}")
            data = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _drain(self, declared_length: int) -> None:
            """丢弃请求体(硬上限 8 MiB), 防止未读数据触发 RST。"""
            remaining = min(declared_length, 8 * 1024 * 1024)
            try:
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except OSError:  # pragma: no cover - client vanished
                pass

        def log_message(self, *_):
            pass  # suppress noisy access log

    return ThreadingHTTPServer((host, port), Handler)


def _run_http(host: str, port: int) -> None:
    """Tiny HTTP server (no extra deps). For production use a real ASGI server.

    P1-4 安全加固:
      * ``MEDIASCRIBE_MCP_TOKEN`` 设置时要求 ``X-MCP-Token`` /
        ``Authorization: Bearer``(否则 401); 未设置时行为不变。
      * Host 头白名单(否则 403), 防 DNS rebinding。
      * 请求体 > 1 MiB 直接 413。
    """
    server = _make_http_server(host, port)
    sys.stderr.write(f"[mediascribe MCP] http server listening on {host}:{port}\n")
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MediaScribe MCP server — expose transcription as MCP tools"
    )
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.transport == "stdio":
        _run_stdio()
    else:
        _run_http(args.host, args.port)


if __name__ == "__main__":
    main()
