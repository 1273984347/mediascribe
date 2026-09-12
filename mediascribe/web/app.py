"""
Web UI for MediaScribe (FastAPI + vanilla HTML).

A minimal browser interface that lets a non-technical user paste
one or more URLs and get back Markdown transcriptions.  The page
calls ``/api/transcribe`` for each URL; the server runs the same
``Pipeline`` that the CLI uses and returns the resulting markdown
inline.  No persistent storage on the server — the markdown is
shown in a copyable textbox and can be downloaded with one click.

This module is *optional*: it depends on ``fastapi`` and ``uvicorn``,
which are NOT hard dependencies.  Install them via
``pip install mediascribe[web]``.

Usage:
    uvicorn mediascribe.web.app:app --reload --port 8000
    # or
    python -m mediascribe.web.app --port 8000

Endpoints:
    GET  /            → HTML page
    GET  /api/health  → JSON liveness probe (always public)
    POST /api/transcribe
         body: {"urls": ["..."], "engine": "whisper", "model": "small",
                "language": "zh", "ocr_engine": "auto",
                "wechat_cookies": "..."}
         returns: {"results": [{"url": "...", "markdown": "...",
                                  "engine": "...", "ok": true}, ...]}

Security:
    * CORS: extension origins and the local dashboard are pre-allowed
      by default.  Set ``MEDIASCRIBE_CORS_ORIGINS`` to a comma-separated
      list to override.  ``*`` is also accepted.
    * Auth: if ``MEDIASCRIBE_API_TOKEN`` is set, every API request
      (except ``/api/health`` and ``/``) must carry
      ``Authorization: Bearer <token>``.  The same token is required on
      the ``/ws/progress/{job_id}`` WebSocket handshake via
      ``?token=`` (or the first ``Sec-WebSocket-Protocol`` entry).
      The token is loaded once at startup; rotate by restarting the
      server.
    * SSRF: user-submitted http(s) URLs are resolved and rejected when
      they point at private / loopback / link-local / reserved
      addresses (``MEDIASCRIBE_ALLOWED_HOSTS`` opts hostnames out for
      local development).  Local file paths are only accepted inside
      the workspace directory.
    * CSRF: JSON-body POST endpoints require ``Content-Type:
      application/json`` (else 415), so cross-site ``text/plain`` form
      posts cannot drive them.
    * Misc env knobs: ``MEDIASCRIBE_PUBLIC_BASE_URL`` (origin rendered
      into INSTALL.md), ``MEDIASCRIBE_WORKSPACE`` (data root),
      ``MEDIASCRIBE_LOG_LEVEL`` (root log level),
      ``MEDIASCRIBE_WIKI`` (set ``0`` to disable the knowledge vault —
      ``mediascribe.wiki`` archives every finished transcript into
      ``<workspace>/vault/``, an Obsidian-compatible wiki),
      ``MEDIASCRIBE_WS_MAX_SESSION_SECONDS`` /
      ``MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS`` (WebSocket lifetime caps).
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import ipaddress
import logging
import os
import queue
import socket
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Deque, Dict, List, Optional
from urllib.parse import urlsplit

# FastAPI / Pydantic are optional dependencies.  The CLI may
# still want to print the help without them, so we keep imports
# inside the route handlers.
try:
    from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, status
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel, Field, field_validator

    _FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - optional
    _FASTAPI_AVAILABLE = False

logger = logging.getLogger(__name__)


def _configure_logging_from_env() -> None:
    """把 ``MEDIASCRIBE_LOG_LEVEL`` 应用到 root logger(幂等)。

    docker-compose / Dockerfile 已经导出 ``MEDIASCRIBE_LOG_LEVEL=info``,
    但此前代码从不读取 — 死配置。无法识别的取值直接忽略, 保持默认级别。
    """
    raw = os.environ.get("MEDIASCRIBE_LOG_LEVEL", "").strip().upper()
    if not raw:
        return
    level = getattr(logging, raw, None)
    if isinstance(level, int):
        logging.getLogger().setLevel(level)


ROOT = Path(__file__).parent.parent.parent
STATIC_DIR = Path(__file__).parent / "static"
# Source tree of the browser extension.  Resolved once at import time
# so the download endpoint can stream a freshly-built ZIP without
# having to walk the directory tree on every request.
_EXTENSION_ROOT = ROOT / "extension"


def _read_extension_manifest_version() -> str:
    """Return the ``version`` field from ``extension/manifest.json``.

    We tolerate any failure (file missing, JSON invalid) and fall
    back to ``"0.0.0"`` — the install page must always render, even
    on a partial install.
    """
    manifest_path = _EXTENSION_ROOT / "manifest.json"
    if not manifest_path.is_file():
        return "0.0.0"
    try:
        import json as _json

        data = _json.loads(manifest_path.read_text(encoding="utf-8"))
        v = str(data.get("version", "0.0.0")).strip()
        return v or "0.0.0"
    except Exception:  # pragma: no cover - defensive
        return "0.0.0"


_EXTENSION_MANIFEST_VERSION = _read_extension_manifest_version()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
if _FASTAPI_AVAILABLE:

    class TranscribeRequest(BaseModel):
        urls: List[str] = Field(..., min_length=1, max_length=20)
        engine: str = Field("whisper", pattern="^(whisper|faster-whisper|whisperx)$")
        model: str = Field(
            "small",
            pattern="^(tiny|base|small|medium|large|large-v1|large-v2|large-v3|distil-large-v2|distil-large-v3)$",
        )
        language: Optional[str] = None
        ocr_engine: str = Field("auto", pattern="^(auto|paddleocr|pytesseract|easyocr|none)$")
        wechat_cookies: Optional[str] = None
        save_images: bool = False
        bilingual: bool = False

        @field_validator("urls")
        @classmethod
        def _reject_non_http_urls(cls, v: List[str]) -> List[str]:
            # SSRF 防护: 拒绝 file:// / ftp:// / data: / gopher:// 等非 http(s) 协议。
            # 允许: http(s) URL / 本地文件路径 / 抖音短链接文本(无 "://")
            DANGEROUS = (
                # 网络协议
                "file://",
                "ftp://",
                "ftps://",
                "data:",
                "gopher://",
                "dict://",
                "ldap://",
                "ldaps://",
                "jar://",
                "netdoc://",
                # DRL R2 补全 (F-1): 浏览器/脚本 scheme (XSS 反射 + 浏览器协议误用)
                "javascript:",
                "vbscript:",
                "blob:",
                "view-source:",
                "about:",
                "chrome:",
                "chrome-extension:",
                "moz-extension:",
            )
            for url in v:
                low = url.strip().lower()
                if low.startswith(DANGEROUS):
                    raise ValueError(
                        f"URL 协议不被允许: {url[:60]}... (仅支持 http/https, "
                        f"本地文件路径, 或平台短链接文本)"
                    )
            return v

    class TranscribeItem(BaseModel):
        url: str
        ok: bool
        engine: Optional[str] = None
        markdown: Optional[str] = None
        title: Optional[str] = None
        error: Optional[str] = None
        wechat_mp_status: Optional[str] = None
        ocr_success: int = 0
        ocr_total: int = 0

    class TranscribeResponse(BaseModel):
        results: List[TranscribeItem]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def _build_pipeline(req: "TranscribeRequest", workspace: Path):
    """构造或复用 Pipeline。

    v3.2.0e 优化: 按 ``(engine, model, device, language)`` 缓存 Pipeline 实例，
    避免每个 Web 请求重新加载 Whisper 模型（5s+ 加载）。``wechat_cookies`` 不参与
    缓存 key（cookies 不影响 transcriber 状态），按需从 Settings 重新读取。

    缓存容量 8（覆盖常见 engine × model 组合），**FIFO** 淘汰（最早插入者先淘汰；
    非 LRU,因为读路径 ``OrderedDict.get()`` 不调 ``move_to_end`` — 8 容量下
    FIFO 与 LRU 实际差异可忽略，避免读路径额外开销）。

    v3.2.0e+ 线程安全修复:
      * ``_PIPELINE_CACHE`` 的 FIFO 淘汰 + 写入由 ``_PIPELINE_CACHE_LOCK`` 保护，
        防止并发请求同时触发淘汰导致多写。
      * 命中缓存时**不修改** cached 实例的 ``settings``，而是构造一个新的
        ``Pipeline`` 共享 cached 的 ``transcriber`` / ``downloader``（重资源 5s+
        模型加载），保证每请求独立的 ``settings`` 不被并发污染。
    """
    from mediascribe.config import Settings
    from mediascribe.pipeline import Pipeline, resolve_device

    cookies: Optional[Dict[str, str]] = None
    if req.wechat_cookies:
        cookies = {}
        for part in req.wechat_cookies.split(";"):
            if "=" in part:
                k, _, v = part.partition("=")
                cookies[k.strip()] = v.strip()

    device = resolve_device(os.environ.get("MEDIASCRIBE_DEVICE", "auto"))
    # 缓存 key 不含 cookies（不影响 transcriber），不含 workspace（每次请求不同）
    cache_key = (
        req.engine if req.engine != "whisper" else "faster-whisper",
        req.model,
        device,
        req.language or "",
    )
    cached = _PIPELINE_CACHE.get(cache_key)
    if cached is not None:
        # 复用 transcriber（5s+ 加载的重资源），但用本次请求的独立 settings
        # 构造新 Pipeline，避免修改 cached 实例的状态导致并发污染。
        settings = Settings(
            workspace_root=workspace,
            model=req.model,
            engine=cache_key[0],
            language=req.language,
            wechat_cookies=cookies,
        )
        settings.device = device
        return Pipeline(
            settings,
            transcriber=cached.transcriber,
            downloader=cached.downloader,
        )

    settings = Settings(
        workspace_root=workspace,
        model=req.model,
        engine=req.engine if req.engine != "whisper" else "faster-whisper",
        language=req.language,
        wechat_cookies=cookies,
    )
    settings.device = device
    pipeline = Pipeline(settings)
    # FIFO: 容量 8，超出淘汰最旧。持锁避免并发写入 race。
    with _PIPELINE_CACHE_LOCK:
        # Double-checked: 另一线程可能在我们构造 Pipeline 时已写入相同 key。
        # 此时丢弃本次构造的 pipeline（其 transcriber 已加载但未缓存，可被 GC），
        # 复用 cache 中的 transcriber/downloader。
        existing = _PIPELINE_CACHE.get(cache_key)
        if existing is not None:
            return Pipeline(
                settings,
                transcriber=existing.transcriber,
                downloader=existing.downloader,
            )
        if len(_PIPELINE_CACHE) >= 8:
            _PIPELINE_CACHE.pop(next(iter(_PIPELINE_CACHE)))
        _PIPELINE_CACHE[cache_key] = pipeline
    return pipeline


# v3.2.0e: Pipeline 实例缓存，按 (engine, model, device, language) 复用。
# 避免每个 Web 请求重新加载 Whisper 模型（5s+ → 0ms）。
_PIPELINE_CACHE: "OrderedDict[tuple, Any]" = OrderedDict()
# v3.2.0e+: 保护 _PIPELINE_CACHE 的 LRU 淘汰与写入，防止并发 race。
_PIPELINE_CACHE_LOCK = threading.Lock()


def _run_one(pipeline, url: str, out_dir: Path, req: "TranscribeRequest") -> "TranscribeItem":
    # ``hash()`` collisions are fine here (we use 128 bits of entropy from
    # ``uuid4``) but historically we used ``abs(hash(url)) % 10**8``, which
    # is deterministic across processes only on Python builds that disable
    # hash randomisation.  ``uuid4().hex[:8]`` is portable and has 32 bits
    # of entropy — enough to make accidental collisions negligible.
    out_path = out_dir / f"{int(time.time())}_{uuid.uuid4().hex[:8]}.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = pipeline.transcribe(
            url,
            output=out_path,
            ocr_engine=req.ocr_engine,
            save_images=req.save_images,
            bilingual=req.bilingual,
        )
        if not out_path.exists():
            return TranscribeItem(
                url=url,
                ok=False,
                error="Pipeline returned but no .md file produced",
            )
        md = out_path.read_text(encoding="utf-8", errors="replace")
        meta = result.metadata or {}
        return TranscribeItem(
            url=url,
            ok=True,
            engine=result.engine,
            markdown=md,
            title=meta.get("title"),
            wechat_mp_status=meta.get("wechat_mp_status"),
            ocr_success=int(meta.get("ocr_success", 0)),
            ocr_total=int(meta.get("ocr_total", 0)),
        )
    except Exception as exc:  # pragma: no cover - defensive
        # P2-8: 下载/解析类用户输入错误(如视频不存在)原样返回给提交者,
        # 但完整 traceback 要落服务端日志。
        logger.exception("transcribe failed (url=%s)", url[:200])
        return TranscribeItem(url=url, ok=False, error=f"{exc.__class__.__name__}: {exc}")


def _default_cors_origins() -> List[str]:
    """Browser extension + common local dashboard origins.

    Wildcard (``*``) is intentionally *not* the default — the Web UI is
    a JSON API and we want explicit, auditable origins in production.

    These are the *exact* origins that the CORS middleware will match
    on.  For pattern matching (e.g. ``http://localhost:*`` and
    ``chrome-extension://*``) we use the regex returned by
    :func:`_default_cors_origin_regex`.
    """
    return [
        # Local development dashboards with no explicit port.
        "http://localhost",
        "http://127.0.0.1",
        # host.docker.internal — the docker-compose workflow (no port).
        "http://host.docker.internal",
    ]


# Pre-compiled at import time so the middleware doesn't re-parse on
# every request.  ``chrome-extension://<id>`` and ``moz-extension://<id>``
# always have a fixed id (one per install), so we match any extension id
# without forcing users to pre-declare it.
_DEFAULT_CORS_ORIGIN_REGEX = (
    r"^https?://(localhost|127\.0\.0\.1|host\.docker\.internal)(:\d+)?$"
    r"|^chrome-extension://[a-z]+$"
    r"|^moz-extension://[a-f0-9-]{36,}$"
    # P2-2: ``file://`` 分支已删除 — 任意本地页面都能带着用户凭据场景
    # 打跨站请求。确有需要时经 ``MEDIASCRIBE_CORS_ORIGINS`` 显式配置。
)


def _resolve_cors_origins() -> tuple[List[str], Optional[str]]:
    """Return ``(origins, regex)`` honouring the env override."""
    override = os.environ.get("MEDIASCRIBE_CORS_ORIGINS", "").strip()
    if not override:
        return _default_cors_origins(), _DEFAULT_CORS_ORIGIN_REGEX
    origins = [o.strip() for o in override.split(",") if o.strip()]
    # When the user opts in via env, drop the regex to keep behaviour
    # explicit — they can always add their own regex patterns.
    return origins, None


def _require_api_token(authorization: Optional[str] = Header(default=None)) -> None:
    """Enforce a single shared bearer token on every API route that depends
    on it.  Disabled (no-op) when ``MEDIASCRIBE_API_TOKEN`` is unset so local
    development keeps working without ceremony.  Comparison is constant-time
    to prevent timing oracles against the secret.
    """
    import hmac

    expected = os.environ.get("MEDIASCRIBE_API_TOKEN", "").strip()
    if not expected:
        return  # auth disabled
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


def _verify_api_token(presented: Optional[str]) -> bool:
    """Constant-time token check shared by HTTP and WebSocket auth (P1-1).

    Same server-side token source as :func:`_require_api_token`
    (``MEDIASCRIBE_API_TOKEN``).  When the env var is unset auth is
    disabled and every caller is allowed — mirroring the HTTP behaviour.
    """
    expected = os.environ.get("MEDIASCRIBE_API_TOKEN", "").strip()
    if not expected:
        return True  # auth disabled — same as the HTTP side
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


# ---------------------------------------------------------------------------
# SSRF / local-path hardening for user-submitted sources (P1-2 / P1-3)
# ---------------------------------------------------------------------------
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
    ``douyin_batch.security.check_url_safety`` — that helper is a
    Douyin-domain allowlist and would reject YouTube / Bilibili URLs.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise ValueError(f"URL 无法解析: {url[:80]}")
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError(f"仅支持 http/https URL: {url[:80]}")
    if parts.username or parts.password:
        raise ValueError(f"URL 不允许包含 userinfo (user@host): {url[:80]}")
    hostname = (parts.hostname or "").lower()
    if not hostname:
        raise ValueError(f"URL 缺少 hostname: {url[:80]}")
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
    except (socket.gaierror, OSError, UnicodeError):
        # 解析失败一律拒绝(fail closed), 不给内网探测留口子。
        raise ValueError(f"URL hostname 无法解析: {hostname}")
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
            raise ValueError(f"URL 指向私有/环回/链路本地/保留地址, 已拒绝 (SSRF 防护): {hostname}")
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
            "本地路径仅允许 workspace 目录内的文件, 远端请提交 http(s) URL"
            f" (workspace: {ws_root}): {entry[:80]}"
        )


def _validate_submitted_urls(urls: List[str], workspace: Path) -> None:
    """对提交入口的每一条 source 做 SSRF / 本地路径校验 (P1-2 + P1-3)。

    校验失败抛 :class:`ValueError`, 由调用方转成 HTTP 400。
    """
    for entry in urls:
        stripped = (entry or "").strip()
        if not stripped:
            raise ValueError("提交项不能为空")
        if stripped.lower().startswith(("http://", "https://")):
            _validate_public_url(stripped)
        elif _is_local_path_source(stripped):
            _validate_local_source(stripped, workspace)
        # 其余: 平台分享文本(无 "://" 且无对应本地文件) — 原样放行。


def _public_base_url(request: "Request") -> str:
    """决定 INSTALL.md 中展示的服务端 origin (P2-10)。

    优先 ``MEDIASCRIBE_PUBLIC_BASE_URL``; 否则取 ``request.base_url`` 但
    只保留 scheme + host(:port) — Host 头可被客户端伪造, 不能把
    userinfo / 任意字符原样反射进文档。
    """
    env_base = os.environ.get("MEDIASCRIBE_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if env_base:
        return env_base
    try:
        parts = urlsplit(str(request.base_url))
        scheme = parts.scheme.lower() if parts.scheme.lower() in ("http", "https") else "http"
        host = parts.hostname or "127.0.0.1"
        # 只放行 hostname 字符集, 防止 Host 头把引号/尖括号等注入文档。
        if not all(c.isalnum() or c in "._-[]" for c in host):
            host = "127.0.0.1"
        try:
            port = parts.port
        except ValueError:
            port = None
        netloc = f"{host}:{port}" if port else host
        return f"{scheme}://{netloc}"
    except Exception:  # pragma: no cover - defensive
        return "http://127.0.0.1:8000"


# ---------------------------------------------------------------------------
# Rate limiting (lightweight in-process sliding window)
# ---------------------------------------------------------------------------
# The web API is *expensive* — each ``/api/transcribe`` call kicks off a
# network download + an ASR model run.  A 1 rps brute-force loop from
# the LAN can saturate disk + GPU.  We protect against that with a
# simple per-client sliding-window counter (1-minute window, configurable
# max requests).  The state lives in process memory, so this is
# advisory rather than authoritative across replicas — for a real
# cluster you would back this with Redis.  For our single-process web
# UI it is more than enough.
@dataclass
class _RateLimiter:
    """Sliding-window rate limiter keyed by client identity.

    Thread-safe (a lock guards the underlying map).  Each check prunes
    the calling client's stale timestamps; **expired buckets for absent
    clients are swept periodically** — at most once per
    ``sweep_interval`` seconds (see :meth:`_RateLimiter.sweep`) — so
    memory stays bounded by "clients active within the last window +
    sweep interval".

    Client identity is the direct peer address (``request.client.host``).
    When running behind a reverse proxy that terminates the connection
    (uvicorn ``--proxy-headers`` / nginx), every client shares the
    proxy's single bucket, so the limiter degrades to a *global* limit —
    deployers behind a proxy should raise ``MEDIASCRIBE_RATE_LIMIT``
    accordingly or move limiting into the proxy.
    """

    max_requests: int = 10
    window_seconds: float = 60.0
    enabled: bool = True
    sweep_interval: float = 60.0
    _buckets: Dict[str, Deque[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_sweep: float = 0.0

    def _sweep_locked(self, cutoff: float) -> int:
        """Drop every bucket whose newest timestamp left the window.

        Caller must hold ``_lock``.
        """
        dead = [k for k, b in self._buckets.items() if not b or b[-1] <= cutoff]
        for k in dead:
            self._buckets.pop(k, None)
        return len(dead)

    def sweep(self) -> int:
        """Periodic full-map cleanup of expired buckets (P2-1).

        Called automatically from :meth:`check` at most once per
        ``sweep_interval`` seconds; exposed separately for tests and
        ops tooling.  Returns the number of buckets removed.
        """
        now = time.monotonic()
        with self._lock:
            self._last_sweep = now
            return self._sweep_locked(now - self.window_seconds)

    def check(self, client_id: str) -> tuple[bool, int, float]:
        """Return ``(allowed, remaining, retry_after_seconds)``.

        ``retry_after_seconds`` is only meaningful when ``allowed`` is
        ``False``; otherwise it is ``0``.
        """
        if not self.enabled or self.max_requests <= 0:
            return True, self.max_requests, 0.0
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            # P2-1: 周期性全表清理 — 只 prune 当前 client 的时间戳不够,
            # 离席 client 的空 bucket 永不回收, 长跑进程内存无界增长。
            if now - self._last_sweep >= self.sweep_interval:
                self._last_sweep = now
                self._sweep_locked(cutoff)
            bucket = self._buckets.get(client_id)
            if bucket is None:
                bucket = deque()
                self._buckets[client_id] = bucket
            # Prune timestamps that fell out of the window.
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                retry_after = max(0.0, bucket[0] + self.window_seconds - now)
                return False, 0, retry_after
            bucket.append(now)
            return True, self.max_requests - len(bucket), 0.0

    def reset(self) -> None:
        """Clear all state — exposed for tests."""
        with self._lock:
            self._buckets.clear()


def _build_rate_limiter() -> _RateLimiter:
    """Build a rate limiter from the environment.

    * ``MEDIASCRIBE_RATE_LIMIT`` — integer max requests per minute
      (default ``10``).  ``0`` disables the limiter entirely.
    * ``MEDIASCRIBE_RATE_LIMIT_WINDOW`` — float window in seconds
      (default ``60``).  This is a free-form knob in case users want
      "5 requests per 10 seconds" style limits.
    """
    try:
        max_req = int(os.environ.get("MEDIASCRIBE_RATE_LIMIT", "10"))
    except ValueError:
        max_req = 10
    try:
        window = float(os.environ.get("MEDIASCRIBE_RATE_LIMIT_WINDOW", "60"))
    except ValueError:
        window = 60.0
    return _RateLimiter(
        max_requests=max_req,
        window_seconds=window,
        enabled=max_req > 0,
    )


def _enforce_rate_limit(
    limiter: _RateLimiter,
    request,  # ``fastapi.Request`` is optional — avoid hard import.
) -> None:
    """Raise ``429`` when the client exceeds its quota."""
    client_ip = "unknown"
    try:
        client_ip = request.client.host if request.client else "unknown"
    except Exception:  # pragma: no cover - defensive
        client_ip = "unknown"
    allowed, remaining, retry_after = limiter.check(client_ip)
    if not allowed:
        # ``Retry-After`` per RFC 6585 must be an integer number of seconds.
        retry_int = max(1, int(round(retry_after)))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"rate limit exceeded ({limiter.max_requests} req / "
                f"{limiter.window_seconds:g}s); retry in {retry_int}s"
            ),
            headers={
                "Retry-After": str(retry_int),
                "X-RateLimit-Limit": str(limiter.max_requests),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Window": str(int(limiter.window_seconds)),
            },
        )


# ---------------------------------------------------------------------------
# GPU health probe with 1-second cache (v3.2.0c Tier 2)
# ---------------------------------------------------------------------------
# ``nvidia-smi`` / ``torch.cuda.*`` probes cost ~30 ms per call; the
# dashboard polls ``/api/health`` every few seconds, so we cache the
# result for 1 second.  The cache is module-level because GPU state is
# global to the process.
_gpu_health_cache: Dict[str, Any] = {"ts": 0.0, "value": None}
_gpu_health_lock = threading.Lock()


def _cached_gpu_health(ttl_seconds: float = 1.0) -> Dict[str, Any]:
    """Return ``gpu_health()`` cached for ``ttl_seconds``.

    The actual probe runs outside the lock so a slow probe does not
    block other readers; we re-acquire the lock only to publish the
    fresh value.
    """
    now = time.monotonic()
    with _gpu_health_lock:
        cached = _gpu_health_cache["value"]
        ts = _gpu_health_cache["ts"]
    if cached is not None and (now - ts) < ttl_seconds:
        return cached
    # Local import — keeps the module importable without torch.
    from mediascribe.pipeline import gpu_health

    fresh = gpu_health()
    with _gpu_health_lock:
        _gpu_health_cache["ts"] = time.monotonic()
        _gpu_health_cache["value"] = fresh
    return fresh


def _reset_gpu_health_cache() -> None:
    """Test hook — clear the cached GPU health value."""
    with _gpu_health_lock:
        _gpu_health_cache["ts"] = 0.0
        _gpu_health_cache["value"] = None


# ---------------------------------------------------------------------------
# Background job runner (v3.2.0c Tier 1 — cancel WS bridge)
# ---------------------------------------------------------------------------
def _store_job_result(
    runner: Callable[[], Any],
    job_id: str,
    out_path: Path,
    url: str,
    results_store: Dict[str, Dict[str, Any]],
    results_lock: "threading.Lock",
    registry: Any,
    vault: Any = None,
) -> None:
    """执行 ``runner()`` 并把结果安全写入 ``results_store``。

    ``runner`` 由调用方构造(可包含 :func:`mediascribe.progress.with_progress`
    进度包装)。所有写操作都在 ``results_lock`` 内,且先校验 job 仍在
    ``registry`` 中(关闭 TOCTOU 窗口)。

    v3.2.0c-fix (P3-1): If the job has been purged from ``registry``
    between ``runner()`` finishing and the result-store write (e.g. by
    a concurrent ``/api/health`` purge on a long-running job older
    than 1 hour), the write is skipped — otherwise we would create an
    orphan entry that ``/api/health`` has just cleaned.
    v3.2.0c-fix (P3-2): All writes to ``results_store`` are guarded by
    ``results_lock`` so the /api/health iteration in another thread
    cannot raise ``RuntimeError: dictionary changed size during
    iteration``.
    v3.2.0c-fix (R6): The ``registry.get(job_id)`` check is performed
    INSIDE ``results_lock`` (same lock ``/api/health`` acquires for
    purge + orphan cleanup), so a concurrent purge cannot mutate the
    registry between the check and the write — closing the TOCTOU
    window that P3-1 alone left open. Any code that mutates
    ``app.state.jobs`` (purge, future DELETE endpoints) MUST hold
    ``results_lock`` to preserve this invariant.
    """
    try:
        result = runner()
        md = ""
        if out_path.exists():
            md = out_path.read_text(encoding="utf-8", errors="replace")
        engine = getattr(result, "engine", None)
        meta = getattr(result, "metadata", None) or {}
        title = meta.get("title")
        # Karpathy 式知识库归档 (Phase 1) — 失败绝不影响任务结果,
        # 只记服务端日志并继续无 wiki 信息的结果写回。
        wiki_info = None
        if vault is not None and out_path.exists():
            try:
                wiki_info = vault.archive_transcript(out_path, meta)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("wiki 归档失败 (job=%s): %r", job_id, exc)
        # P3-1 + R6-fix: registry check is INSIDE the lock so a
        # concurrent /api/health purge (which also holds this lock,
        # see /api/health) cannot mutate the registry between the
        # check and the write — closing the TOCTOU window that would
        # otherwise let the worker resurrect an orphan entry.
        with results_lock:
            if registry.get(job_id) is None:
                # Job was purged while we were running; don't write.
                return
            results_store[job_id] = {
                "markdown": md,
                "engine": engine,
                "title": title,
                "url": url,
                "wiki": wiki_info,
            }
    except Exception as exc:  # pragma: no cover - defensive
        # ``with_progress`` already marks the job as failed via
        # ``job.fail(...)``; we just record the failure here so the
        # REST ``/api/jobs/{id}/result`` endpoint can surface it.
        # P3-1 + R6-fix: same TOCTOU-closing pattern on exception path.
        # P2-8: per-URL 错误信息保留给提交者(下载失败等用户输入错误),
        # 完整异常落服务端日志。
        logger.exception("job %s failed (url=%s)", job_id, url[:200])
        with results_lock:
            if registry.get(job_id) is None:
                return
            results_store[job_id] = {
                "markdown": "",
                "engine": None,
                "title": None,
                "url": url,
                "error": f"{exc.__class__.__name__}: {exc}",
            }


def _run_job_safely(
    runner: Callable[[], Any],
    job_id: str,
    out_path: Path,
    url: str,
    results_store: Dict[str, Dict[str, Any]],
    results_lock: "threading.Lock",
    registry: Any,
    vault: Any = None,
) -> None:
    """遗留单任务提交路径的薄封装 — 委托 :func:`_store_job_result`。

    v3.2.0e:批量提交已改走 :func:`_run_batch_job`(``AsyncPipeline.run_batch``),
    此处保留以兼容单 URL 直接 ``ThreadPoolExecutor.submit`` 的调用方。
    """
    _store_job_result(
        runner, job_id, out_path, url, results_store, results_lock, registry, vault=vault
    )


def _run_batch_job(
    ap: Any,
    urls: List[str],
    runners: Dict[str, Callable[[], Any]],
    job_ids: List[str],
    async_pipelines: Dict[str, Any],
) -> None:
    """整批提交路径:在独立线程跑 ``AsyncPipeline.run_batch``。

    * GPU 感知并发 + 单视频失败隔离 + 可取消(见 ``cancel_job`` / ws_progress)。
    * 每个 URL 的 ``runners[url]`` 是 ``with_progress`` + ``_store_job_result``
      封装的带进度 thunk。
    * 批处理结束后清理 ``async_pipelines`` 中本批 job_id 注册(该字典即
      ``app.state.async_pipelines``,由 submit_jobs 传入),避免注册表泄漏。
    """
    try:
        asyncio.run(ap.run_batch(urls, runners=runners))
    except Exception:
        # P2-7: 不再裸吞 BaseException — KeyboardInterrupt / SystemExit
        # 照常传播; 普通异常记入服务端日志(各 job 自身状态已反映结果)。
        logger.exception("batch job failed")
    finally:
        for jid in job_ids:
            async_pipelines.pop(jid, None)


# ---------------------------------------------------------------------------
# WS progress plumbing (P2-9) — 常驻 drain 线程桥接阻塞 events 队列
# ---------------------------------------------------------------------------
_WS_TERMINAL_EVENTS = frozenset({"succeeded", "failed", "cancelled"})


def _aio_queue_put_drop_oldest(q: "asyncio.Queue", item: Any) -> None:
    """投入 asyncio.Queue; 满时丢最旧(终态事件后发, 天然不丢)。"""
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:  # pragma: no cover - defensive
            pass
    q.put_nowait(item)


class _JobEventBridge:
    """把 ``JobProgress.events``(阻塞 queue)桥接到 asyncio 事件循环。

    P2-9: 旧实现每个 WS 连接每 0.5s 向线程池提交一次
    ``asyncio.to_thread(job.events.get, ...)`` — N 个连接就是 N 个
    0.5s 周期的线程池任务。这里改为每个 job 一个常驻 daemon drain
    线程, 经 ``loop.call_soon_threadsafe`` 把事件扇出到每个订阅者的
    ``asyncio.Queue``; 终态事件分发完毕后线程自动退出。
    """

    def __init__(self, job: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._job = job
        self._loop = loop
        self._closed = threading.Event()
        self._subscribers: "set[asyncio.Queue]" = set()
        self._subscribers_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._drain,
            daemon=True,
            name="v2t-ws-bridge",
        )
        self._thread.start()

    @property
    def subscriber_count(self) -> int:
        with self._subscribers_lock:
            return len(self._subscribers)

    def subscribe(self) -> "asyncio.Queue":
        q: "asyncio.Queue" = asyncio.Queue(maxsize=1000)
        with self._subscribers_lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: "asyncio.Queue") -> None:
        with self._subscribers_lock:
            self._subscribers.discard(q)

    def close(self) -> None:
        self._closed.set()

    def _drain(self) -> None:
        job = self._job
        while not self._closed.is_set():
            try:
                event = job.events.get(timeout=1.0)
            except queue.Empty:
                if job.finished:
                    break
                continue
            with self._subscribers_lock:
                subs = list(self._subscribers)
            for q in subs:
                try:
                    self._loop.call_soon_threadsafe(
                        _aio_queue_put_drop_oldest,
                        q,
                        event,
                    )
                except RuntimeError:
                    # 事件循环已关闭(应用停机)— 退出线程。
                    self._closed.set()
                    break
            if event.get("event") in _WS_TERMINAL_EVENTS:
                break


def create_app(
    workspace: Optional[Path] = None,
    api_token: Optional[str] = None,
    cors_origins: Optional[List[str]] = None,
):
    """Build the FastAPI app.  ``workspace`` is the temp dir for transcripts.

    Args:
        workspace: directory where transcripts and audio are written.
        api_token: optional shared secret.  If ``None``, falls back to
            ``MEDIASCRIBE_API_TOKEN`` from the environment; if neither is
            set, the API is unauthenticated (fine for ``127.0.0.1``-only
            dev usage; **do not** expose such an instance on a public
            network).
        cors_origins: explicit list of allowed origins.  ``None`` falls
            back to ``MEDIASCRIBE_CORS_ORIGINS`` or the built-in
            extension-friendly defaults.
    """
    if not _FASTAPI_AVAILABLE:
        raise RuntimeError("FastAPI is not installed. Run: pip install mediascribe[web]")
    # 附加修复: docker-compose 已设 ``MEDIASCRIBE_LOG_LEVEL=info`` 但代码
    # 此前不读 — 在应用装配处应用一次(幂等)。
    _configure_logging_from_env()

    # v3.2.0c-fix: release worker threads + GPU memory on shutdown.
    # Without this the ``ThreadPoolExecutor`` threads outlive the app
    # (it is the only deviation in the codebase — every other usage
    # wraps the executor in a ``with`` block) and may write to a
    # tempdir that's already been deleted.  ``cancel_futures=True``
    # abandons not-yet-started submissions; running tasks cooperate
    # via the ``job.cancelled`` flag between stages.
    @asynccontextmanager
    async def _lifespan(app: "FastAPI"):
        yield
        executor = getattr(app.state, "job_executor", None)
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("executor 关闭异常: %r", exc)
        # P2-9: 停机时叫停所有 WS bridge drain 线程。
        for bridge in list(getattr(app.state, "ws_bridges", {}).values()):
            bridge.close()

    app = FastAPI(
        title="MediaScribe Web UI",
        version="3.2.0c",
        description="Transcribe videos to Markdown from your browser.",
        lifespan=_lifespan,
    )

    # CORS — pre-allow extension + local origins; the user can override.
    if api_token is None:
        api_token = os.environ.get("MEDIASCRIBE_API_TOKEN", "").strip() or None
    if cors_origins is None:
        cors_origins, cors_regex = _resolve_cors_origins()
    else:
        cors_regex = None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_origin_regex=cors_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    # Persist the active token on app.state for tests / introspection.
    app.state.api_token_configured = bool(api_token)
    # Rate limiter: per-client, sliding-window.  Default = 10 req / 60 s.
    # Disable by setting ``MEDIASCRIBE_RATE_LIMIT=0``.
    app.state.rate_limiter = _build_rate_limiter()
    # Job progress registry (v3.2.0a) — backs ``/ws/progress/{job_id}``
    # and the ``/api/jobs/{job_id}`` REST helpers.
    from mediascribe.pipeline import resolve_device
    from mediascribe.progress import ProgressRegistry

    app.state.jobs = ProgressRegistry()
    # v3.2.0c Tier 1: background job runner — ThreadPoolExecutor for
    # ``with_progress`` thunks + a results store keyed by job_id.
    # The executor lives for the lifetime of the app; on shutdown the
    # pending tasks are abandoned (cooperative cancel inside
    # ``with_progress`` checks ``job.cancelled`` between stages).
    # 并发度可通过环境变量覆盖（参考 AsyncPipeline._default_max_concurrent）。
    # 默认 2：转录是 GPU/IO 密集型，过多并发反而争抢显存。
    try:
        _max_workers = int(os.environ.get("MEDIASCRIBE_MAX_WORKERS", "2"))
    except ValueError:
        _max_workers = 2
    if _max_workers <= 0:
        _max_workers = 2
    app.state.job_executor = ThreadPoolExecutor(
        max_workers=_max_workers, thread_name_prefix="v2t-job"
    )
    app.state.job_results: Dict[str, Dict[str, Any]] = {}
    # v3.2.0c-fix (P3-2): lock guarding ``app.state.job_results`` against
    # concurrent mutation between the worker thread (``_run_job_safely``)
    # and the /api/health iteration+pop in the request thread. Without
    # this, /api/health's ``[k for k in app.state.job_results if ...]``
    # could raise ``RuntimeError: dictionary changed size during
    # iteration`` when a worker writes between iterations.
    #
    # v3.2.0c-fix (R2-F4 rename): the original name ``job_results_lock``
    # under-described the lock's actual scope. Per the R6 invariant
    # declared on ``_run_job_safely``, this lock guards BOTH
    # ``app.state.job_results`` (markdown dict) AND ``app.state.jobs``
    # (ProgressRegistry mutate paths — purge, future DELETE endpoints).
    # The renamed ``jobs_state_lock`` reflects that expanded duty so
    # future maintainers do not assume a narrower "results-only" scope
    # and add unsynchronised registry mutations.
    app.state.jobs_state_lock = threading.Lock()
    # Optional mapping from job_id → AsyncPipeline (for future
    # async-wired cancellations).  Populated by callers that wrap
    # Pipeline in AsyncPipeline; empty by default.
    app.state.async_pipelines: Dict[str, Any] = {}
    # P2-9: job_id → WS event bridge (resident drain thread per job).
    app.state.ws_bridges: Dict[str, _JobEventBridge] = {}
    app.state.ws_bridge_lock = threading.Lock()

    # 附加修复: Docker 镜像已设 ``MEDIASCRIBE_WORKSPACE=/workspace`` 且
    # compose 已挂卷, 但代码此前不读该变量, 导致容器内数据不落卷。
    if workspace is None:
        env_ws = os.environ.get("MEDIASCRIBE_WORKSPACE", "").strip()
        workspace = Path(env_ws) if env_ws else Path.cwd() / "web-workspace"

    # Karpathy 式知识库 vault (Phase 1, mediascribe.wiki) — 每次任务成功后
    # 把转写稿归档进 <workspace>/vault/; MEDIASCRIBE_WIKI=0 停用。
    from mediascribe.wiki import vault_for_workspace

    app.state.wiki_vault = vault_for_workspace(workspace)

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        # v3.2.0c-fix: opportunistic purge of finished jobs older than
        # 1 hour, run on each health probe (dashboard polls every 5s).
        # ``ProgressRegistry.purge`` was previously never invoked, so
        # the registry leaked memory in long-running deployments.
        # Piggybacking on /api/health matches the GPU-cache pattern —
        # cheap, idempotent, no extra background task.
        # v3.2.0c-fix (R4): purge only cleans ``ProgressRegistry._jobs``;
        # ``app.state.job_results`` (the markdown store) must be
        # sync-cleaned for the same victim job_ids, otherwise REST
        # ``/api/jobs/{id}/result`` 404s after the 1-hour TTL while the
        # markdown payload still leaks in memory.
        try:
            # v3.2.0c-fix (P3-2 + R6): purge + orphan cleanup run UNDER
            # the same lock that _run_job_safely acquires for its
            # check-and-write. This makes purge atomic with respect to
            # the worker's registry-check, closing the TOCTOU window
            # where a worker could see the job as live (check passes)
            # then write an orphan entry while purge is mid-flight.
            with app.state.jobs_state_lock:
                app.state.jobs.purge(older_than_seconds=3600)
                # Sync-clean orphaned results: anything in job_results
                # that is no longer in the registry is dead data.
                live_ids = {j.job_id for j in app.state.jobs.list()}
                orphan_ids = [k for k in app.state.job_results if k not in live_ids]
                for orphan_id in orphan_ids:
                    app.state.job_results.pop(orphan_id, None)
        except Exception as exc:  # v3.2.0c-fix (R2): surface purge failures
            # Pre-R6 the swallowed surface was smaller; R6 widened it
            # to cover the new lock acquisition + orphan cleanup loop.
            # A recurring purge failure (e.g. future bug in
            # ProgressRegistry.purge) silently halts memory
            # reclamation with zero operator signal — log so the
            # dashboard can alert on recurring warnings.
            logger.warning("/api/health purge failed: %r", exc)
        # v3.2.0c Tier 2: GPU probe is cached for 1 s — the dashboard
        # polls every few seconds, and each ``torch.cuda.*`` probe
        # costs ~30 ms.
        gpu = _cached_gpu_health(ttl_seconds=1.0)
        return {
            "ok": True,
            "version": "3.2.0c",
            "auth_enabled": app.state.api_token_configured,
            "rate_limit_enabled": app.state.rate_limiter.enabled,
            "rate_limit_max": app.state.rate_limiter.max_requests,
            "rate_limit_window": app.state.rate_limiter.window_seconds,
            "device_hint": resolve_device(os.environ.get("MEDIASCRIBE_DEVICE", "auto")),
            "gpu": gpu,
        }

    @app.get("/extension", response_class=HTMLResponse)
    def extension_page() -> HTMLResponse:
        """The user-facing install page for the browser extension.

        The page is rendered as a server-side template so we can show
        a useful banner when ``MEDIASCRIBE_API_TOKEN`` is set, and we
        do not need a second build step.
        """
        auth_on = app.state.api_token_configured
        token_hint = (
            "Enter your Bearer token on the options page after loading "
            "the extension. The server you are talking to requires one."
            if auth_on
            else "No token is required by the server, so the extension options page is optional."
        )
        # Render the same minimal dark theme as the main UI (KIMI-style
        # neutral dark) for visual consistency.
        html = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="color-scheme" content="dark">'
            "<title>MediaScribe Browser Extension</title>"
            "<style>"
            "body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,"
            "Roboto,'PingFang SC','Microsoft YaHei','Noto Sans',sans-serif;"
            "background:#181817;color:rgba(255,255,255,.84);padding:32px;line-height:1.6;}"
            "a{color:#007cff;}h1{margin:0 0 4px;font-size:20px;}"
            "h2{margin-top:28px;font-size:14px;color:rgba(255,255,255,.56);}"
            "ol{padding-left:20px;}code,pre{background:#121212;border:1px solid rgba(255,255,255,.12);"
            "border-radius:6px;padding:2px 6px;font-size:12px;"
            "font-family:ui-monospace,'SF Mono','Cascadia Code',Consolas,monospace;}"
            "pre{padding:10px 12px;overflow:auto;}"
            ".card{background:#1f1f1f;border:1px solid rgba(255,255,255,.12);border-radius:12px;"
            "padding:20px 22px;max-width:760px;}"
            ".btn{display:inline-block;background:rgba(255,255,255,.84);color:#121212;font-weight:500;"
            "padding:9px 14px;border-radius:10px;text-decoration:none;margin-top:8px;}"
            ".btn:hover{background:#ffffff;}"
            ".btn.secondary{background:rgba(255,255,255,.05);color:rgba(255,255,255,.84);}"
            ".tag{display:inline-block;background:rgba(255,255,255,.05);color:rgba(255,255,255,.56);"
            "padding:2px 8px;border-radius:999px;font-size:11px;margin-left:6px;}"
            ".ok{color:#16c456;}.warn{color:#ff9f0a;}"
            "</style></head><body>"
            '<div class="card">'
            "<h1>MediaScribe Browser Extension"
            f'<span class="tag">manifest v{_EXTENSION_MANIFEST_VERSION}</span>'
            "</h1>"
            "<p>Send the URL of the page you are reading straight to this "
            "Web UI and read the transcript in a toolbar popup or the "
            "browser's right-side panel.</p>"
            f"<p>{token_hint}</p>"
            '<a class="btn" href="/api/extension/download">Download '
            "extension (.zip)</a> "
            '<a class="btn secondary" href="/api/extension/install.md">'
            "Install guide (markdown)</a>"
            "<h2>Install in 30 seconds</h2>"
            "<ol>"
            "<li>Click <b>Download extension</b> above and save the ZIP.</li>"
            "<li>Extract it into a permanent folder, e.g. "
            "<code>~/mediascribe-extension/</code>.</li>"
            "<li>Open <code>chrome://extensions/</code> "
            "(or <code>edge://extensions/</code>) and turn on "
            "<b>Developer mode</b>.</li>"
            "<li>Click <b>Load unpacked</b> and select the extracted folder.</li>"
            "<li>Click the toolbar icon (or open the side panel) — done.</li>"
            "</ol>"
            "<h2>Troubleshooting</h2>"
            "<ul>"
            "<li><b>Side panel button missing</b> — update to Chrome / "
            "Edge 114 or newer.</li>"
            "<li><b>401 Unauthorized</b> — your server has "
            "<code>MEDIASCRIBE_API_TOKEN</code> set. Paste the same token "
            "in the extension options page.</li>"
            "<li><b>CORS blocked</b> — the server is configured to reject "
            "browser-extension origins. Check <code>MEDIASCRIBE_CORS_ORIGINS</code>."
            "</li>"
            "</ul>"
            "</div></body></html>"
        )
        return HTMLResponse(content=html)

    @app.get("/api/extension/download")
    def extension_download(request: Request):
        """Stream a freshly-built ZIP of the ``extension/`` tree.

        The ZIP is built in-memory each time so the user always gets
        a copy that matches the running server's version.  ``INSTALL.md``
        is generated per-request so it embeds the actual endpoint URL
        instead of the ``example.com`` placeholder.
        """
        _enforce_rate_limit(app.state.rate_limiter, request)
        try:
            # Tolerate both ``app`` (loaded as a script module by the
            # test client) and the proper package import.
            import importlib

            try:
                builder = importlib.import_module("mediascribe.web.extension_builder")
            except Exception:
                builder = importlib.import_module("extension_builder")
            build_extension_zip = builder.build_extension_zip
            build_install_markdown = builder.build_install_markdown
        except Exception:  # pragma: no cover - defensive
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="extension builder module is not importable",
            )
        # Build the install doc with the user's actual origin so the
        # one-click instructions match the running server.
        # P2-10: 不再原样反射 Host 头 — 优先 env, 否则剥离 userinfo
        # 只留 scheme+host。
        origin = _public_base_url(request)
        install_md = build_install_markdown(web_ui_origin=origin)
        try:
            zip_bytes, filename = build_extension_zip(
                _EXTENSION_ROOT,
                extra_files={"INSTALL.md": install_md},
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(exc),
            )
        # Use a Response so we can set Content-Disposition explicitly.
        from fastapi.responses import Response

        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    @app.get("/api/extension/install.md", response_class=HTMLResponse)
    def extension_install_md(request: Request) -> HTMLResponse:
        """Serve the per-user install guide as raw markdown.

        ``text/markdown`` is the right MIME type per RFC 7763, but
        browsers will try to render it as plain text.  ``HTMLResponse``
        is fine because the endpoint is also linked from the install
        page; if you need the raw bytes, hit
        ``/api/extension/install.md?raw=1``.
        """
        try:
            import importlib

            try:
                builder = importlib.import_module("mediascribe.web.extension_builder")
            except Exception:
                builder = importlib.import_module("extension_builder")
            build_install_markdown = builder.build_install_markdown
        except Exception:  # pragma: no cover
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="extension builder module is not importable",
            )
        # P2-10: 同上 — 不反射原始 Host 头。
        origin = _public_base_url(request)
        md = build_install_markdown(web_ui_origin=origin)
        if request.query_params.get("raw") == "1":
            from fastapi.responses import Response

            return Response(
                content=md,
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": 'inline; filename="INSTALL.md"'},
            )
        # Otherwise wrap the markdown in a tiny HTML shim so the
        # browser renders something readable.
        from html import escape

        body = (
            '<!doctype html><meta charset="utf-8">'
            '<meta name="color-scheme" content="dark">'
            "<title>MediaScribe — Install guide</title>"
            "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,"
            "Roboto,'PingFang SC','Microsoft YaHei','Noto Sans',sans-serif;"
            "background:#181817;color:rgba(255,255,255,.84);"
            "padding:32px;line-height:1.6;max-width:760px;}"
            "pre,code{background:#121212;border:1px solid rgba(255,255,255,.12);"
            "border-radius:6px;padding:2px 6px;"
            "font-family:ui-monospace,'SF Mono','Cascadia Code',Consolas,monospace;}"
            "a{color:#007cff;}</style>"
            '<a href="/extension">&larr; Back</a>'
            f"<pre>{escape(md)}</pre>"
        )
        return HTMLResponse(content=body)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(content=html)

    @app.post(
        "/api/transcribe",
        response_model=TranscribeResponse,
        dependencies=[Depends(_require_api_token), Depends(_require_json_content_type)],
    )
    def transcribe(req: TranscribeRequest, request: Request) -> TranscribeResponse:
        # Rate limit is applied after auth so unauthenticated callers
        # cannot exhaust the limiter for legitimate users.
        _enforce_rate_limit(app.state.rate_limiter, request)
        if not req.urls:
            raise HTTPException(status_code=400, detail="urls must be non-empty")
        # P1-2 + P1-3: SSRF / 本地路径校验 — 私网 URL 与 workspace 外
        # 本地路径在提交入口拒绝。
        try:
            _validate_submitted_urls(req.urls, workspace)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        try:
            pipeline = _build_pipeline(req, workspace)
        except Exception:
            # P2-8: 内部异常细节(路径/stack)不回显客户端, 落服务端日志。
            logger.exception("pipeline init failed")
            raise HTTPException(
                status_code=500,
                detail="internal error while initialising the pipeline; see server logs",
            )
        items: List[TranscribeItem] = []
        for url in req.urls:
            items.append(_run_one(pipeline, url, workspace / "out", req))
        return TranscribeResponse(results=items)

    # -------------------------------------------------------------------------
    # Job progress (v3.2.0a) — WebSocket-friendly
    # -------------------------------------------------------------------------
    @app.websocket("/ws/progress/{job_id}")
    async def ws_progress(websocket: WebSocket, job_id: str) -> None:
        """Stream per-stage progress events for a job.

        The job is created by :class:`mediascribe.progress.ProgressRegistry`
        and its events queue is drained here.  The connection stays
        open until the job finishes (event ``succeeded`` / ``failed``
        / ``cancelled``) and then closes with code ``1000``.

        P1-1 — 握手鉴权: 服务端设置了 ``MEDIASCRIBE_API_TOKEN`` 时,
        WS 必须携带 token(与 HTTP 侧同源、同为常数时间比较):
          * query param ``?token=<tok>``, 或
          * ``Sec-WebSocket-Protocol`` 首段(浏览器无法给 WS 加自定义头)。
        未配置 token 时放行(与 HTTP 行为一致)。校验失败直接
        ``close(1008)``, 不 accept。``cancel`` 指令同样只对已通过鉴权
        的连接生效。

        P2-9 — 事件桥接: 每个 job 一个常驻 drain 线程
        (:class:`_JobEventBridge`)把阻塞 events 队列桥接到 asyncio,
        多个 WS 连接订阅同一 bridge, 不再每连接每 0.5s 提交线程池任务。
        连接另有最大时长(``MEDIASCRIBE_WS_MAX_SESSION_SECONDS``, 默认
        3600s)与空闲超时(``MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS``,
        默认 300s), 超时以 ``1000`` 正常关闭。

        v3.2.0c Tier 1 — clients may send ``{"event": "cancel"}`` to
        request cancellation; the server then calls
        ``registry.cancel(job_id)`` (and, if registered, the matching
        ``AsyncPipeline.cancel()``) and pushes a ``cancelled`` event
        back to all listeners.
        """
        # P1-1: accept 之前完成鉴权 — 失败则握手拒绝, 不进入事件循环。
        presented = websocket.query_params.get("token")
        if not presented:
            protocol_header = websocket.headers.get("sec-websocket-protocol", "")
            presented = protocol_header.split(",")[0].strip() or None
        ws_authenticated = _verify_api_token(presented)
        if not ws_authenticated:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            from mediascribe.progress import ProgressRegistry
        except ImportError:
            await websocket.send_json({"event": "error", "message": "progress module missing"})
            await websocket.close(code=1011)
            return
        registry: ProgressRegistry = app.state.jobs  # type: ignore[attr-defined]
        job = registry.get(job_id)
        if job is None:
            await websocket.send_json({"event": "error", "message": "unknown job"})
            await websocket.close(code=4404)
            return
        # Replay current state, then drain new events
        await websocket.send_json({"event": "snapshot", **job.to_dict()})

        import json as _json

        loop = asyncio.get_running_loop()
        with app.state.ws_bridge_lock:
            bridge = app.state.ws_bridges.get(job.job_id)
            if bridge is None or bridge.subscriber_count == 0 or not bridge._thread.is_alive():
                bridge = _JobEventBridge(job, loop)
                app.state.ws_bridges[job.job_id] = bridge
        subscriber_queue = bridge.subscribe()
        max_session_seconds = _env_float("MEDIASCRIBE_WS_MAX_SESSION_SECONDS", 3600.0)
        if max_session_seconds <= 0:
            max_session_seconds = float("inf")  # 显式禁用会话上限
        idle_timeout = _env_float("MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS", 300.0)
        # ping 周期取空闲超时与 30s 的较小者 — 让客户端能探测连接活性。
        ping_interval = min(30.0, idle_timeout) if idle_timeout > 0 else 30.0

        # v3.2.0c Tier 1 — clients may send ``{"event": "cancel"}`` to
        # request cancellation.  We use :func:`asyncio.wait` with
        # ``FIRST_COMPLETED`` so that both the event-drain task and the
        # client-listen task are polled within the *same* event-loop
        # iteration.  This makes cancel delivery reliable under
        # Starlette's TestClient (which drives the server forward one
        # client call at a time) — a separate ``create_task`` listener
        # would only be scheduled when the main loop yields, which can
        # race with the client's send/receive sequence.
        event_task = asyncio.create_task(subscriber_queue.get())
        client_task = asyncio.create_task(websocket.receive_text())
        session_deadline = loop.time() + max_session_seconds
        last_activity = loop.time()
        try:
            while not job.finished:
                now = loop.time()
                if now >= session_deadline:
                    break  # P2-9: 最大会话时长 — 防止连接永久挂起
                if idle_timeout > 0 and (now - last_activity) >= idle_timeout:
                    break  # P2-9: 空闲超时 — 无事件且无客户端消息
                wait_timeout = min(
                    ping_interval,
                    max(0.05, session_deadline - now),
                )
                if idle_timeout > 0:
                    wait_timeout = min(
                        wait_timeout,
                        max(0.05, last_activity + idle_timeout - now),
                    )
                done, _ = await asyncio.wait(
                    {event_task, client_task},
                    timeout=wait_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if event_task in done:
                    event = event_task.result()
                    last_activity = loop.time()
                    try:
                        await websocket.send_json(event)
                    except Exception:
                        return
                    event_task = asyncio.create_task(subscriber_queue.get())
                if client_task in done:
                    try:
                        msg = client_task.result()
                    except Exception:
                        # WebSocket closed by client.
                        return
                    last_activity = loop.time()
                    try:
                        parsed = _json.loads(msg)
                    except Exception:
                        parsed = None
                    if isinstance(parsed, dict) and parsed.get("event") == "cancel":
                        if ws_authenticated:
                            registry.cancel(job_id)
                            # Also cancel any registered AsyncPipeline so
                            # in-flight ``asyncio.Task``s are torn down.
                            ap = app.state.async_pipelines.get(job_id)
                            if ap is not None and hasattr(ap, "cancel"):
                                try:
                                    ap.cancel()
                                except Exception:
                                    pass
                        else:
                            # P1-1: 未鉴权连接不允许驱动 cancel。
                            try:
                                await websocket.send_json(
                                    {
                                        "event": "error",
                                        "message": "cancel requires an authenticated connection",
                                    }
                                )
                            except Exception:
                                return
                    client_task = asyncio.create_task(websocket.receive_text())
                if not done:
                    # 超时 — 发 ping 让客户端探测连接活性; 继续等待。
                    try:
                        await websocket.send_json({"event": "ping"})
                    except Exception:
                        return
            # Final snapshot then close
            try:
                await websocket.send_json({"event": "snapshot", **job.to_dict()})
            except Exception:
                pass
        finally:
            # Cancel background tasks without awaiting them — awaiting
            # a cancelled ``websocket.receive_text()`` task can interfere
            # with Starlette's WS teardown and surface as a
            # ``CancelledError`` in the TestClient's ``__exit__``.
            event_task.cancel()
            client_task.cancel()
            # P2-9: 退订; 最后一个订阅者离开时关闭并摘除 bridge。
            bridge.unsubscribe(subscriber_queue)
            if bridge.subscriber_count == 0:
                bridge.close()
                with app.state.ws_bridge_lock:
                    if app.state.ws_bridges.get(job.job_id) is bridge:
                        app.state.ws_bridges.pop(job.job_id, None)
        await websocket.close(code=1000)

    @app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(_require_api_token)])
    def cancel_job(job_id: str) -> Dict[str, Any]:
        """Cancel a running job (no-op if it doesn't exist).

        v3.2.0c Tier 1 — also cancels any registered :class:`AsyncPipeline`
        so in-flight ``asyncio.Task``s are torn down, not just the
        cooperative ``job.cancelled`` flag.
        """
        from mediascribe.progress import ProgressRegistry

        registry: ProgressRegistry = app.state.jobs  # type: ignore[attr-defined]
        cancelled = registry.cancel(job_id)
        if not cancelled:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        ap = app.state.async_pipelines.get(job_id)
        if ap is not None and hasattr(ap, "cancel"):
            try:
                ap.cancel()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("AsyncPipeline.cancel 异常: %r", exc)
        return {"job_id": job_id, "cancelled": True}

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(_require_api_token)])
    def job_status(job_id: str) -> Dict[str, Any]:
        """Return the current snapshot of a job's progress."""
        from mediascribe.progress import ProgressRegistry

        registry: ProgressRegistry = app.state.jobs  # type: ignore[attr-defined]
        job = registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return job.to_dict()

    # -----------------------------------------------------------------
    # v3.2.0c Tier 1 — async job submission + result retrieval
    # -----------------------------------------------------------------
    @app.post(
        "/api/jobs", dependencies=[Depends(_require_api_token), Depends(_require_json_content_type)]
    )
    def submit_jobs(
        req: TranscribeRequest,
        request: Request,
    ) -> Dict[str, Any]:
        """Submit URLs as background jobs; returns immediately with job_ids.

        Each URL is run via :func:`mediascribe.progress.with_progress` on
        the app's :class:`ThreadPoolExecutor`.  The caller should:

        1. ``GET /ws/progress/{job_id}`` (WebSocket) to receive
           ``stage_start`` / ``stage_progress`` / ``stage_done`` / ``succeeded``
           / ``failed`` / ``cancelled`` events.
        2. ``POST /api/jobs/{job_id}/cancel`` to abort a running job.
        3. ``GET /api/jobs/{job_id}/result`` to fetch the Markdown once
           the job reaches ``finished``.

        P2-6: 重复 URL 会去重(保留首次出现顺序)— 每个唯一 URL 只创建
        一个 job, 避免孤儿 job 与并发写同一 out_path。响应中的
        ``requested`` / ``unique`` 计数可用于感知是否发生了去重。

        The synchronous ``POST /api/transcribe`` endpoint is preserved
        for backwards compatibility with the Chrome extension and MCP.
        """
        _enforce_rate_limit(app.state.rate_limiter, request)
        if not req.urls:
            raise HTTPException(status_code=400, detail="urls must be non-empty")
        # P2-6: 去重保序 — 重复 URL 复用同一 job。
        urls = list(dict.fromkeys(req.urls))
        # P1-2 + P1-3: SSRF / 本地路径校验 — 私网 URL 与 workspace 外
        # 本地路径在提交入口拒绝。
        try:
            _validate_submitted_urls(urls, workspace)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        try:
            pipeline = _build_pipeline(req, workspace)
        except Exception:
            # P2-8: 内部异常细节不回显客户端, 落服务端日志。
            logger.exception("pipeline init failed")
            raise HTTPException(
                status_code=500,
                detail="internal error while initialising the pipeline; see server logs",
            )
        from mediascribe.pipeline_async import AsyncPipeline
        from mediascribe.progress import ProgressRegistry, with_progress

        registry: ProgressRegistry = app.state.jobs  # type: ignore[attr-defined]
        out_dir = workspace / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        # v3.2.0e wiring: 整批走一个 AsyncPipeline.run_batch,获得 GPU 感知并发
        # + 单视频失败隔离 + 可取消(见 cancel_job / ws_progress)。每个 URL 的
        # runner 用 with_progress 包装以保留进度事件与落盘,再套 _store_job_result
        # 安全写回 job_results。
        ap = AsyncPipeline(pipeline)
        runners: Dict[str, Callable[[], Any]] = {}
        jobs_out: List[Dict[str, Any]] = []
        job_ids: List[str] = []
        for url in urls:
            job = registry.create(url)
            out_path = out_dir / f"{int(time.time())}_{uuid.uuid4().hex[:8]}.md"
            wp = with_progress(pipeline, job, url, out_path=out_path)
            runners[url] = functools.partial(
                _store_job_result,
                wp,
                job.job_id,
                out_path,
                url,
                app.state.job_results,
                app.state.jobs_state_lock,
                registry,
                vault=app.state.wiki_vault,
            )
            jobs_out.append(
                {
                    "job_id": job.job_id,
                    "url": url,
                    "status": "queued",
                    "ws": f"/ws/progress/{job.job_id}",
                    "result": f"/api/jobs/{job.job_id}/result",
                }
            )
            job_ids.append(job.job_id)
        # 注册 AP 到本批所有 job_id — cancel 任一 job 都能命中并中止整批
        # 尚未启动的排队任务(已启动的仍由 registry.cancel 协作取消)。
        for jid in job_ids:
            app.state.async_pipelines[jid] = ap
        app.state.job_executor.submit(
            _run_batch_job,
            ap,
            urls,
            runners,
            job_ids,
            app.state.async_pipelines,
        )
        return {
            "jobs": jobs_out,
            "requested": len(req.urls),
            "unique": len(urls),
        }

    @app.get("/api/jobs/{job_id}/result", dependencies=[Depends(_require_api_token)])
    def job_result(job_id: str) -> Dict[str, Any]:
        """Return the markdown + metadata for a job.

        Returns ``finished=False`` until the job reaches a terminal
        state.  After completion, returns ``markdown`` (string) and
        optional ``engine`` / ``title``.  Cancelled jobs return
        ``cancelled=True`` with ``markdown=None``.
        """
        from mediascribe.progress import ProgressRegistry

        registry: ProgressRegistry = app.state.jobs  # type: ignore[attr-defined]
        job = registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        # v3.2.0c-fix (P3-2): hold the lock while reading so a
        # concurrent /api/health purge cannot pop the entry mid-read.
        with app.state.jobs_state_lock:
            stored = app.state.job_results.get(job_id)
            stored_copy = dict(stored) if stored else None
        stored = stored_copy
        return {
            "job_id": job_id,
            "finished": job.finished,
            "cancelled": job.cancelled,
            "error": job.error or (stored or {}).get("error"),
            "markdown": (stored or {}).get("markdown") if stored else None,
            "engine": (stored or {}).get("engine") if stored else None,
            "title": (stored or {}).get("title") if stored else None,
            "wiki": (stored or {}).get("wiki") if stored else None,
            "stage": job.stage,
            "stage_current": job.stage_current,
            "stage_total": job.stage_total,
        }

    return app


# ---------------------------------------------------------------------------
# CLI (run via ``python -m mediascribe.web.app``)
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--workspace", type=Path, default=None)
    args = parser.parse_args(argv)

    if not _FASTAPI_AVAILABLE:
        print("FastAPI not installed. Run: pip install mediascribe[web]")
        return 1
    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed. Run: pip install mediascribe[web]")
        return 1
    app = create_app(workspace=args.workspace)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
