"""
MCP 传输层 — stdio 与 HTTP 两种承载 (自 mcp_server.py 拆出, 2026-11-03)。

HTTP 加固历史 (P1-4 + 2026-11-02 SEC-003): token / Host 白名单 /
body 上限 / Content-Type 强制 / Origin 白名单 — 细节见各函数。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .protocol import _make_error, _safe_handle_request


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
    get = headers.get
    presented = (get("X-MCP-Token") or "").strip()
    if not presented:
        auth = (get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            presented = auth.split(" ", 1)[1].strip()
    if not presented:
        return False
    import hmac

    return hmac.compare_digest(presented, expected)


def _origin_allowed(origin: str) -> bool:
    """Origin 白名单: 本机回环地址与浏览器扩展。

    MCP HTTP 传输只服务本机客户端; 任何指向外部站点的 Origin 都意味着
    请求来自某个网页 (drive-by), 一律拒绝。
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    if parsed.scheme == "chrome-extension":
        return True
    host = (parsed.hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


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
            # SEC-003 (2026-11-02 审计): 浏览器 drive-by 防护。
            # text/plain 是 CORS 简单请求、无需预检, 任意网页可盲打本端口 —
            # 强制 application/json (与 Web 层 _require_json_content_type 同闸);
            # 携带 Origin 的跨源请求一律拒绝 (本服务只服务本机 CLI/桌面客户端)。
            ctype = (self.headers.get("Content-Type") or "").strip().lower()
            if not ctype.startswith("application/json"):
                self._reply_plain(415, "Content-Type must be application/json")
                self._drain(length)
                return
            origin = (self.headers.get("Origin") or "").strip()
            if origin and not _origin_allowed(origin):
                self._reply_plain(403, "cross-origin requests are not allowed")
                self._drain(length)
                return
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            bound_port = self.server.server_address[1]
            if not _mcp_host_ok(self.headers.get("Host", ""), bound_port):
                self._reply_plain(403, "forbidden host")
                return
            if not _mcp_token_ok(self.headers):
                self._reply_plain(
                    401,
                    "missing or invalid MCP token",
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
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
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
