"""用户提交来源的 SSRF / 本地路径校验。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import List
from urllib.parse import urlsplit

from ..i18n import t


def _validate_public_url(url: str) -> str:
    """Validate a user-supplied http(s) URL against SSRF targets (P1-2).

    Rejects:

    1. non-http(s) schemes (defence in depth — the pydantic validator
       already blocks the obvious ones);
    2. URLs carrying userinfo (``https://user@host/…``);
    3. hostnames that resolve to private / loopback / link-local /
       reserved / multicast addresses — checked against *every* address
       ``socket.getaddrinfo`` returns, so DNS rebinding to an internal
       IP is caught too;
    4. hostnames that fail to resolve (fail closed).

    Opt-out for local development: set ``MEDIASCRIBE_ALLOWED_HOSTS`` to a
    comma-separated list of hostnames that skip the private-address
    check (they still must be http(s) and userinfo-free).

    Note: this deliberately does NOT reuse
    ``mediascribe.security.check_url_safety`` — that helper is a
    Douyin-domain allowlist and would reject YouTube / Bilibili URLs.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise ValueError(t("WEB_URL_UNPARSEABLE", url=url[:80])) from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError(t("WEB_URL_SCHEME", url=url[:80]))
    if parts.username or parts.password:
        raise ValueError(t("WEB_URL_USERINFO", url=url[:80]))
    hostname = (parts.hostname or "").lower()
    if not hostname:
        raise ValueError(t("WEB_URL_NO_HOST", url=url[:80]))
    allowed = {
        h.strip().lower()
        for h in os.environ.get("MEDIASCRIBE_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    }
    if hostname in allowed:
        # 显式白名单: 跳过私网解析检查(本地联调用), 其余规则仍生效。
        return url
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, OSError, UnicodeError) as exc:
        # 解析失败一律拒绝(fail closed), 不给内网探测留口子。
        raise ValueError(t("WEB_URL_HOST_FAIL", host=hostname)) from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(t("WEB_URL_PRIVATE", host=hostname))
    return url


def _is_local_path_source(entry: str) -> bool:
    """True 当提交项应按本地文件路径处理(而非平台分享文本)。

    分享文本(无 ``://`` 且磁盘上不存在同名文件)原样透传给 pipeline;
    绝对路径(PurePosixPath / PureWindowsPath 语义均检查, 跨平台)或
    当前工作目录下真实存在的相对路径都按本地路径校验。
    """
    p = entry.strip()
    if not p:
        return False
    try:
        if PurePosixPath(p).is_absolute() or PureWindowsPath(p).is_absolute():
            return True
        return Path(p).exists()
    except (OSError, ValueError):  # pragma: no cover - defensive
        return False


def _validate_local_source(entry: str, workspace: Path) -> None:
    """Reject local-file sources outside the workspace (P1-3).

    ``inputs.parse_source`` treats any existing local path as a
    transcription source, which turns the submit endpoints into an
    arbitrary-local-file-read primitive on a shared server.  The web
    layer therefore only accepts local paths that resolve *inside*
    the workspace directory; everything else must be an http(s) URL.
    """
    resolved = Path(entry).expanduser().resolve()
    try:
        ws_root = Path(workspace).resolve()
        contained = resolved == ws_root or resolved.is_relative_to(ws_root)
    except OSError:  # pragma: no cover - defensive
        ws_root = None  # type: ignore[assignment]
        contained = False
    if not contained:
        raise ValueError(
            t(
                "WEB_LOCAL_PATH",
                workspace=ws_root,
                entry=entry[:80],
            )
        )


def _validate_submitted_urls(urls: List[str], workspace: Path) -> None:
    """对提交入口的每一条 source 做 SSRF / 本地路径校验 (P1-2 + P1-3)。

    校验失败抛 :class:`ValueError`, 由调用方转成 HTTP 400。
    """
    for entry in urls:
        stripped = (entry or "").strip()
        if not stripped:
            raise ValueError(t("WEB_EMPTY_ENTRY"))
        if stripped.lower().startswith(("http://", "https://")):
            _validate_public_url(stripped)
            _revalidate_short_link_target(stripped)
        elif _is_local_path_source(stripped):
            _validate_local_source(stripped, workspace)


def _revalidate_short_link_target(url: str) -> None:
    """短链(b23.tv / v.douyin.com)的最终重定向目标也要过 SSRF 校验。

    提交时只查原始 hostname; 短链服务若被利用做开放重定向(或被接管),
    重定向目标可以指向 169.254.169.254 等元数据端点, 绕过首次校验
    (2026-10-03 审查加固)。解析失败不拦截(交给下载阶段自然失败,
    也避免离线 CI 被 网络 round-trip 卡死); 只有解析出明确目标且
    目标违规时拒绝。
    """
    from mediascribe.url_utils import is_short_link, resolve_short_url

    if not is_short_link(url):
        return
    final = resolve_short_url(url)
    if final and final != url:
        _validate_public_url(final)
