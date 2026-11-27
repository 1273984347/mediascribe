"""
MCP JSON-RPC 协议层 — 请求分发与响应构造 (自 mcp_server.py 拆出, 2026-11-03)。

只关心协议: initialize / tools/* / resources/* 方法路由与 JSON-RPC 2.0
信封; 工具实现见 tools.py, 传输见 transport.py。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .._version import __version__
from .tools import TOOL_HANDLERS, TOOL_LIST

_TRANSCRIPTION_TOOLS = frozenset({"transcribe_video", "transcribe_wechat_mp"})


def _record_mcp_input(name: str, args: Dict[str, Any]) -> None:
    if name not in _TRANSCRIPTION_TOOLS:
        return
    source = args.get("source") or args.get("url") or args.get("path")
    if not source:
        return
    try:
        from ..events import EventRecorder

        EventRecorder(surface="mcp").record_input(
            input_value=source,
            selection_mode="explicit" if args.get("engine") or args.get("model") else "auto",
            engine=args.get("engine"),
            model=args.get("model"),
            device=args.get("device"),
        )
    except Exception:  # pragma: no cover - telemetry must not affect MCP
        import logging

        logging.getLogger(__name__).debug("MCP input event recording failed", exc_info=True)


def _record_mcp_result(name: str, error_code: Optional[str] = None) -> None:
    if name not in _TRANSCRIPTION_TOOLS:
        return
    result = "no_speech" if error_code == "NO_SPEECH" else ("failed" if error_code else "success")
    stage = "transcribe" if error_code in {"NO_SPEECH", "TRANSCRIPTION_EMPTY"} else None
    try:
        from ..events import EventRecorder

        EventRecorder(surface="mcp").record_finished(
            result=result,
            error_code=error_code,
            stage=stage,
        )
    except Exception:  # pragma: no cover - telemetry must not affect MCP
        import logging

        logging.getLogger(__name__).debug("MCP terminal event recording failed", exc_info=True)


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
        return _make_response(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                # R-13(2026-11-21 架构评审): 版本接单一真源,不再硬编码。
                "serverInfo": {"name": "mediascribe", "version": __version__},
                "capabilities": {"tools": {}},
            },
        )

    if method == "notifications/initialized":
        return None  # notification, no response

    if method == "tools/list":
        return _make_response(req_id, {"tools": TOOL_LIST})

    if method == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments", {}) or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            # Per MCP spec, tool-not-found is reported as a result with isError=true
            return _make_response(
                req_id,
                {
                    "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                    "isError": True,
                },
            )
        _record_mcp_input(name, args)
        try:
            result = handler(args)
        except Exception as exc:
            from ..recovery import recovery_for_exception

            recovery = recovery_for_exception(exc)
            payload = {
                "error_code": recovery["code"],
                "error": recovery["reason"],
                "recovery": recovery,
            }
            _record_mcp_result(name, recovery["code"])
            return _make_response(
                req_id,
                {
                    "content": [
                        {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}
                    ],
                    "structuredContent": payload,
                    "isError": True,
                },
            )
        if isinstance(result, dict) and (result.get("ok") is False or result.get("error_code")):
            from ..recovery import recovery_for_code

            error_code = str(result.get("error_code") or "UNKNOWN")
            recovery = recovery_for_code(error_code)
            payload = {
                "error_code": recovery["code"],
                "error": recovery["reason"],
                "recovery": recovery,
            }
            _record_mcp_result(name, recovery["code"])
            return _make_response(
                req_id,
                {
                    "content": [
                        {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}
                    ],
                    "structuredContent": payload,
                    "isError": True,
                },
            )
        _record_mcp_result(name)
        return _make_response(
            req_id,
            {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                ],
                "isError": False,
            },
        )

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
