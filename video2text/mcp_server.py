"""
MCP (Model Context Protocol) server for Video2Text.

Exposes Video2Text's transcribe / batch / inspect capabilities as MCP tools
that AI agents (Claude Code, Cursor, Cline, Windsurf, Continue, etc.) can call
natively.

Two transports are supported:
- **stdio** (default) — for local agents (Claude Code, Cursor, Cline)
- **HTTP** (--transport http) — for remote agents

Usage:
    # Install MCP SDK first:  pip install mcp
    # Then register in your agent's MCP config:
    #   { "mcpServers": { "video2text": { "command": "python",
    #     "args": ["-m", "video2text.mcp_server"] } } }
    #
    # Or run directly to test:
    #   python -m video2text.mcp_server
    #
    # HTTP transport:
    #   python -m video2text.mcp_server --transport http --port 8765

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

SCHEMA_VERSION = "video2text.mcp/v1"

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
        VIDEO2TEXT_TRANSCRIPT_ROOT — directory under which transcripts
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
        os.environ.get("VIDEO2TEXT_TRANSCRIPT_ROOT", "output/transcripts")
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
                "set VIDEO2TEXT_TRANSCRIPT_ROOT to allow a different directory"
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
    from video2text.config import Settings
    from video2text.pipeline import Pipeline

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
        可通过环境变量 ``VIDEO2TEXT_BATCH_TIMEOUT`` 覆盖），
        防止批量 CLI 长时间阻塞 MCP 服务进程。
      * 捕获 ``subprocess.TimeoutExpired`` 异常，返回结构化错误而非崩溃
        MCP 工具调用链。
    """
    import subprocess
    env_val = os.environ.get("VIDEO2TEXT_BATCH_TIMEOUT", "").strip()
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
    cmd.extend(["-n", str(args.get("max_videos", 10))])
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
    from video2text.config import Settings
    from video2text.inputs import parse_source
    from video2text.pipeline import Pipeline

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
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params", {}) or {}

    if method == "initialize":
        return _make_response(req_id, {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "video2text", "version": "2.1.0"},
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


def _run_stdio() -> None:
    """Read JSON-RPC messages from stdin, write responses to stdout."""
    sys.stderr.write("[video2text MCP] stdio server ready\n")
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
        resp = _handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def _run_http(host: str, port: int) -> None:
    """Tiny HTTP server (no extra deps). For production use a real ASGI server."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            try:
                req = json.loads(body)
                resp = _handle_request(req)
                if resp is None:
                    resp = {"jsonrpc": "2.0", "id": req.get("id"), "result": {}}
            except json.JSONDecodeError as exc:
                resp = _make_error(None, -32700, f"Parse error: {exc}")
            data = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass  # suppress noisy access log

    server = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write(f"[video2text MCP] http server listening on {host}:{port}\n")
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Video2Text MCP server — expose transcription as MCP tools"
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
