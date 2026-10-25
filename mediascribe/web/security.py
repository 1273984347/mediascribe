"""鉴权与请求头校验(API token / CSRF / 客户端来源信任)。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
fastapi 是可选依赖 —— 缺失时本模块仍可导入, 只是鉴权函数不可用
(``create_app`` 会先抛 RuntimeError, 根本到不了这里)。
"""

from __future__ import annotations

import ipaddress
import os
from typing import Optional

try:
    from fastapi import Header, HTTPException, Request, status
except ImportError:  # pragma: no cover - optional
    # fastapi 缺失时的占位。``Header`` 出现在**默认参数**位置
    # (``authorization: Optional[str] = Header(default=None)``), 会在函数定义
    # 时求值 ⇒ 必须是个可调用对象, 不能是 None(否则模块自身 ImportError,
    # 兜底反而比不兜底更早炸)。``HTTPException`` / ``status`` 只在函数体里用,
    # 缺失时调用会 TypeError —— 但那时 create_app() 已先抛 RuntimeError。
    def Header(default=None):  # type: ignore[misc]
        """fastapi 缺失时的占位 Header。"""
        return default

    HTTPException = Request = status = None  # type: ignore[assignment,misc]


def _trusted_no_auth_client(host: Optional[str]) -> bool:
    """无 token 模式下该客户端来源是否可信(2026-10-03 审查加固)。

    威胁模型: "零配置 = 仅本机/内网"。公网来源一律拒绝; 两类合法的
    零配置场景放行 —— 直连 127.0.0.1 的本地开发, 以及 docker bridge
    网关(172.x/192.168.x, 宿主机经 127.0.0.1 端口映射进容器)。
    更细的 LAN 收敛交给端口绑定(compose 默认 127.0.0.1:8000)与
    ``MEDIASCRIBE_API_TOKEN``; 非 IP peer(测试客户端/unix socket)视为本地。
    """
    if not host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _require_api_token(
    request: "Request",
    authorization: Optional[str] = Header(default=None),
) -> None:
    """Enforce a single shared bearer token on every API route that depends
    on it.  Disabled (no-op for trusted clients) when ``MEDIASCRIBE_API_TOKEN``
    is unset so local development keeps working without ceremony; a client
    from a *public* address is rejected even in no-token mode (see
    :func:`_trusted_no_auth_client`).  Comparison is constant-time
    to prevent timing oracles against the secret.
    """
    import hmac

    expected = os.environ.get("MEDIASCRIBE_API_TOKEN", "").strip()
    if not expected:
        client_host = request.client.host if request.client else None
        if _trusted_no_auth_client(client_host):
            return  # auth disabled, trusted (local/private) client
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "API token 未配置, 已拒绝非本机/内网来源的请求。请设置 MEDIASCRIBE_API_TOKEN。"
            ),
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    presented = authorization.split(" ", 1)[1].strip()
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid API token",
        )


def _verify_api_token(presented: Optional[str], client_host: Optional[str] = None) -> bool:
    """Constant-time token check shared by HTTP and WebSocket auth (P1-1).

    Same server-side token source as :func:`_require_api_token`
    (``MEDIASCRIBE_API_TOKEN``).  When the env var is unset auth is
    disabled and only *trusted* (loopback / private-network) clients are
    allowed — mirroring the HTTP behaviour (2026-10-03 审查加固).
    """
    expected = os.environ.get("MEDIASCRIBE_API_TOKEN", "").strip()
    if not expected:
        return _trusted_no_auth_client(client_host)  # auth disabled
    if not presented or not presented.strip():
        return False
    import hmac

    return hmac.compare_digest(presented.strip(), expected)


def _env_float(name: str, default: float) -> float:
    """Read a float env var; unparsable/empty falls back to ``default``."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _require_json_content_type(request: Request) -> None:
    """P2-3: CSRF 防护 — ``text/plain`` 表单 "简单请求" 可携带任意 body
    却无需 CORS 预检, 不能让它驱动 JSON API。要求带 JSON body 的 POST
    端点显式声明 ``Content-Type: application/json``, 浏览器表单无法伪造
    该头。仅用于 **有 JSON body** 的端点; 无 body 的端点(如
    ``/api/jobs/{id}/cancel``)不适用。
    """
    ctype = (request.headers.get("content-type") or "").strip().lower()
    if not ctype.startswith("application/json"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be application/json",
        )
