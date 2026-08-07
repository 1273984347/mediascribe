"""
Web UI for Video2Text (FastAPI + vanilla HTML).

A minimal browser interface that lets a non-technical user paste
one or more URLs and get back Markdown transcriptions.  The page
calls ``/api/transcribe`` for each URL; the server runs the same
``Pipeline`` that the CLI uses and returns the resulting markdown
inline.  No persistent storage on the server — the markdown is
shown in a copyable textbox and can be downloaded with one click.

This module is *optional*: it depends on ``fastapi`` and ``uvicorn``,
which are NOT hard dependencies.  Install them via
``pip install video2text[web]``.

Usage:
    uvicorn video2text.web.app:app --reload --port 8000
    # or
    python -m video2text.web.app --port 8000

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
      by default.  Set ``VIDEO2TEXT_CORS_ORIGINS`` to a comma-separated
      list to override.  ``*`` is also accepted.
    * Auth: if ``VIDEO2TEXT_API_TOKEN`` is set, every API request
      (except ``/api/health`` and ``/``) must carry
      ``Authorization: Bearer <token>``.  The token is loaded once at
      startup; rotate by restarting the server.
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import logging
import os
import sys
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

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
                "file://", "ftp://", "ftps://", "data:", "gopher://",
                "dict://", "ldap://", "ldaps://", "jar://", "netdoc://",
                # DRL R2 补全 (F-1): 浏览器/脚本 scheme (XSS 反射 + 浏览器协议误用)
                "javascript:", "vbscript:", "blob:", "view-source:",
                "about:", "chrome:", "chrome-extension:", "moz-extension:",
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
    from video2text.config import Settings
    from video2text.pipeline import Pipeline, resolve_device

    cookies: Optional[Dict[str, str]] = None
    if req.wechat_cookies:
        cookies = {}
        for part in req.wechat_cookies.split(";"):
            if "=" in part:
                k, _, v = part.partition("=")
                cookies[k.strip()] = v.strip()

    device = resolve_device(os.environ.get("VIDEO2TEXT_DEVICE", "auto"))
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
_PIPELINE_CACHE: "OrderedDict[tuple, Any]" = __import__(
    "collections"
).OrderedDict()
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
                url=url, ok=False,
                error="Pipeline returned but no .md file produced",
            )
        md = out_path.read_text(encoding="utf-8", errors="replace")
        meta = result.metadata or {}
        return TranscribeItem(
            url=url, ok=True,
            engine=result.engine,
            markdown=md,
            title=meta.get("title"),
            wechat_mp_status=meta.get("wechat_mp_status"),
            ocr_success=int(meta.get("ocr_success", 0)),
            ocr_total=int(meta.get("ocr_total", 0)),
        )
    except Exception as exc:  # pragma: no cover - defensive
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
    r"|^file://.+$"
)


def _resolve_cors_origins() -> tuple[List[str], Optional[str]]:
    """Return ``(origins, regex)`` honouring the env override."""
    override = os.environ.get("VIDEO2TEXT_CORS_ORIGINS", "").strip()
    if not override:
        return _default_cors_origins(), _DEFAULT_CORS_ORIGIN_REGEX
    origins = [o.strip() for o in override.split(",") if o.strip()]
    # When the user opts in via env, drop the regex to keep behaviour
    # explicit — they can always add their own regex patterns.
    return origins, None


def _require_api_token(authorization: Optional[str] = Header(default=None)) -> None:
    """Enforce a single shared bearer token on every API route that depends
    on it.  Disabled (no-op) when ``VIDEO2TEXT_API_TOKEN`` is unset so local
    development keeps working without ceremony.  Comparison is constant-time
    to prevent timing oracles against the secret.
    """
    import hmac
    expected = os.environ.get("VIDEO2TEXT_API_TOKEN", "").strip()
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

    Thread-safe (a lock guards the underlying map).  Memory is bounded
    by the number of distinct clients seen in the last ``window``
    seconds — old buckets are pruned on every request.
    """

    max_requests: int = 10
    window_seconds: float = 60.0
    enabled: bool = True
    _buckets: Dict[str, Deque[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

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

    * ``VIDEO2TEXT_RATE_LIMIT`` — integer max requests per minute
      (default ``10``).  ``0`` disables the limiter entirely.
    * ``VIDEO2TEXT_RATE_LIMIT_WINDOW`` — float window in seconds
      (default ``60``).  This is a free-form knob in case users want
      "5 requests per 10 seconds" style limits.
    """
    try:
        max_req = int(os.environ.get("VIDEO2TEXT_RATE_LIMIT", "10"))
    except ValueError:
        max_req = 10
    try:
        window = float(os.environ.get("VIDEO2TEXT_RATE_LIMIT_WINDOW", "60"))
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
    from video2text.pipeline import gpu_health
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
) -> None:
    """执行 ``runner()`` 并把结果安全写入 ``results_store``。

    ``runner`` 由调用方构造(可包含 :func:`video2text.progress.with_progress`
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
            }
    except Exception as exc:  # pragma: no cover - defensive
        # ``with_progress`` already marks the job as failed via
        # ``job.fail(...)``; we just record the failure here so the
        # REST ``/api/jobs/{id}/result`` endpoint can surface it.
        # P3-1 + R6-fix: same TOCTOU-closing pattern on exception path.
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
) -> None:
    """遗留单任务提交路径的薄封装 — 委托 :func:`_store_job_result`。

    v3.2.0e:批量提交已改走 :func:`_run_batch_job`(``AsyncPipeline.run_batch``),
    此处保留以兼容单 URL 直接 ``ThreadPoolExecutor.submit`` 的调用方。
    """
    _store_job_result(
        runner, job_id, out_path, url, results_store, results_lock, registry
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
    except BaseException:
        # 取消或其他异常:后台线程静默结束,各 job 自身状态已反映结果。
        pass
    finally:
        for jid in job_ids:
            async_pipelines.pop(jid, None)


def create_app(workspace: Optional[Path] = None,
               api_token: Optional[str] = None,
               cors_origins: Optional[List[str]] = None):
    """Build the FastAPI app.  ``workspace`` is the temp dir for transcripts.

    Args:
        workspace: directory where transcripts and audio are written.
        api_token: optional shared secret.  If ``None``, falls back to
            ``VIDEO2TEXT_API_TOKEN`` from the environment; if neither is
            set, the API is unauthenticated (fine for ``127.0.0.1``-only
            dev usage; **do not** expose such an instance on a public
            network).
        cors_origins: explicit list of allowed origins.  ``None`` falls
            back to ``VIDEO2TEXT_CORS_ORIGINS`` or the built-in
            extension-friendly defaults.
    """
    if not _FASTAPI_AVAILABLE:
        raise RuntimeError(
            "FastAPI is not installed. Run: pip install video2text[web]"
        )

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

    app = FastAPI(
        title="Video2Text Web UI",
        version="3.2.0c",
        description="Transcribe videos to Markdown from your browser.",
        lifespan=_lifespan,
    )

    # CORS — pre-allow extension + local origins; the user can override.
    if api_token is None:
        api_token = os.environ.get("VIDEO2TEXT_API_TOKEN", "").strip() or None
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
    # Disable by setting ``VIDEO2TEXT_RATE_LIMIT=0``.
    app.state.rate_limiter = _build_rate_limiter()
    # Job progress registry (v3.2.0a) — backs ``/ws/progress/{job_id}``
    # and the ``/api/jobs/{job_id}`` REST helpers.
    from video2text.pipeline import resolve_device
    from video2text.progress import ProgressRegistry

    app.state.jobs = ProgressRegistry()
    # v3.2.0c Tier 1: background job runner — ThreadPoolExecutor for
    # ``with_progress`` thunks + a results store keyed by job_id.
    # The executor lives for the lifetime of the app; on shutdown the
    # pending tasks are abandoned (cooperative cancel inside
    # ``with_progress`` checks ``job.cancelled`` between stages).
    # 并发度可通过环境变量覆盖（参考 AsyncPipeline._default_max_concurrent）。
    # 默认 2：转录是 GPU/IO 密集型，过多并发反而争抢显存。
    try:
        _max_workers = int(os.environ.get("VIDEO2TEXT_MAX_WORKERS", "2"))
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

    workspace = workspace or Path.cwd() / "web-workspace"

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
            "device_hint": resolve_device(os.environ.get("VIDEO2TEXT_DEVICE", "auto")),
            "gpu": gpu,
        }

    @app.get("/extension", response_class=HTMLResponse)
    def extension_page() -> HTMLResponse:
        """The user-facing install page for the browser extension.

        The page is rendered as a server-side template so we can show
        a useful banner when ``VIDEO2TEXT_API_TOKEN`` is set, and we
        do not need a second build step.
        """
        auth_on = app.state.api_token_configured
        token_hint = (
            "Enter your Bearer token on the options page after loading "
            "the extension. The server you are talking to requires one."
            if auth_on
            else "No token is required by the server, so the extension "
            "options page is optional."
        )
        # Render the same dark theme as the main UI for visual consistency.
        html = (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>Video2Text Browser Extension</title>"
            "<style>"
            "body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
            "background:#0f172a;color:#e2e8f0;padding:32px;line-height:1.55;}"
            "a{color:#38bdf8;}h1{margin:0 0 4px;font-size:22px;}"
            "h2{margin-top:28px;font-size:16px;color:#94a3b8;letter-spacing:.04em;text-transform:uppercase;}"
            "ol{padding-left:20px;}code,pre{background:#0b1220;border:1px solid #334155;"
            "border-radius:4px;padding:2px 6px;font-size:12px;}"
            "pre{padding:10px 12px;overflow:auto;}"
            ".card{background:#0b1220;border:1px solid #334155;border-radius:8px;padding:18px 22px;"
            "max-width:760px;}"
            ".btn{display:inline-block;background:#38bdf8;color:#0b1220;font-weight:600;"
            "padding:10px 16px;border-radius:4px;text-decoration:none;margin-top:8px;}"
            ".btn:hover{filter:brightness(1.1);}"
            ".tag{display:inline-block;background:#0b1220;border:1px solid #334155;"
            "color:#94a3b8;padding:2px 8px;border-radius:99px;font-size:11px;margin-left:6px;}"
            ".ok{color:#22c55e;}.warn{color:#f59e0b;}"
            "</style></head><body>"
            "<div class=\"card\">"
            "<h1>Video2Text Browser Extension"
            f"<span class=\"tag\">manifest v{_EXTENSION_MANIFEST_VERSION}</span>"
            "</h1>"
            "<p>Send the URL of the page you are reading straight to this "
            "Web UI and read the transcript in a toolbar popup or the "
            "browser's right-side panel.</p>"
            f"<p>{token_hint}</p>"
            "<a class=\"btn\" href=\"/api/extension/download\">Download "
            "extension (.zip)</a> "
            "<a class=\"btn\" href=\"/api/extension/install.md\" "
            "style=\"background:#0b1220;color:#e2e8f0;border:1px solid #334155;\">"
            "Install guide (markdown)</a>"
            "<h2>Install in 30 seconds</h2>"
            "<ol>"
            "<li>Click <b>Download extension</b> above and save the ZIP.</li>"
            "<li>Extract it into a permanent folder, e.g. "
            "<code>~/video2text-extension/</code>.</li>"
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
            "<code>VIDEO2TEXT_API_TOKEN</code> set. Paste the same token "
            "in the extension options page.</li>"
            "<li><b>CORS blocked</b> — the server is configured to reject "
            "browser-extension origins. Check <code>VIDEO2TEXT_CORS_ORIGINS</code>."
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
                builder = importlib.import_module(
                    "video2text.web.extension_builder"
                )
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
        try:
            origin = str(request.base_url).rstrip("/")
        except Exception:  # pragma: no cover
            origin = "http://127.0.0.1:8000"
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
                builder = importlib.import_module(
                    "video2text.web.extension_builder"
                )
            except Exception:
                builder = importlib.import_module("extension_builder")
            build_install_markdown = builder.build_install_markdown
        except Exception:  # pragma: no cover
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="extension builder module is not importable",
            )
        try:
            origin = str(request.base_url).rstrip("/")
        except Exception:  # pragma: no cover
            origin = "http://127.0.0.1:8000"
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
            "<!doctype html><meta charset=\"utf-8\">"
            "<title>Video2Text — Install guide</title>"
            "<style>body{font-family:-apple-system,BlinkMacSystemFont,"
            "'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;"
            "padding:32px;line-height:1.55;max-width:760px;}"
            "pre,code{background:#0b1220;border:1px solid #334155;"
            "border-radius:4px;padding:2px 6px;}"
            "a{color:#38bdf8;}</style>"
            "<a href=\"/extension\">&larr; Back</a>"
            f"<pre>{escape(md)}</pre>"
        )
        return HTMLResponse(content=body)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(content=html)

    @app.post("/api/transcribe", response_model=TranscribeResponse,
              dependencies=[Depends(_require_api_token)])
    def transcribe(req: TranscribeRequest, request: Request) -> TranscribeResponse:
        # Rate limit is applied after auth so unauthenticated callers
        # cannot exhaust the limiter for legitimate users.
        _enforce_rate_limit(app.state.rate_limiter, request)
        if not req.urls:
            raise HTTPException(status_code=400, detail="urls must be non-empty")
        try:
            pipeline = _build_pipeline(req, workspace)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"init failed: {exc}")
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

        The job is created by :class:`video2text.progress.ProgressRegistry`
        and its events queue is drained here.  The connection stays
        open until the job finishes (event ``succeeded`` / ``failed``
        / ``cancelled``) and then closes with code ``1000``.

        v3.2.0c Tier 1 — clients may send ``{"event": "cancel"}`` to
        request cancellation; the server then calls
        ``registry.cancel(job_id)`` (and, if registered, the matching
        ``AsyncPipeline.cancel()``) and pushes a ``cancelled`` event
        back to all listeners.
        """
        await websocket.accept()
        try:
            from video2text.progress import ProgressRegistry
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
        # v3.2.0c Tier 1 — clients may send ``{"event": "cancel"}`` to
        # request cancellation.  We use :func:`asyncio.wait` with
        # ``FIRST_COMPLETED`` so that both the event-drain task and the
        # client-listen task are polled within the *same* event-loop
        # iteration.  This makes cancel delivery reliable under
        # Starlette's TestClient (which drives the server forward one
        # client call at a time) — a separate ``create_task`` listener
        # would only be scheduled when the main loop yields, which can
        # race with the client's send/receive sequence.
        import asyncio
        import json as _json

        async def _next_event():
            # ``queue.Queue.get(timeout=...)`` blocks in a worker thread
            # so the event loop stays free to run other tasks.
            return await asyncio.to_thread(job.events.get, timeout=0.5)

        async def _next_client_msg():
            return await websocket.receive_text()

        event_task = asyncio.create_task(_next_event())
        client_task = asyncio.create_task(_next_client_msg())
        try:
            while not job.finished:
                done, _ = await asyncio.wait(
                    {event_task, client_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if event_task in done:
                    try:
                        event = event_task.result()
                    except Exception:
                        # Timeout — send a ping so the client can
                        # detect a disconnect; keep draining.
                        try:
                            await websocket.send_json({"event": "ping"})
                        except Exception:
                            return
                    else:
                        try:
                            await websocket.send_json(event)
                        except Exception:
                            return
                    event_task = asyncio.create_task(_next_event())
                if client_task in done:
                    try:
                        msg = client_task.result()
                    except Exception:
                        # WebSocket closed by client.
                        return
                    try:
                        parsed = _json.loads(msg)
                    except Exception:
                        parsed = None
                    if isinstance(parsed, dict) and parsed.get("event") == "cancel":
                        registry.cancel(job_id)
                        # Also cancel any registered AsyncPipeline so
                        # in-flight ``asyncio.Task``s are torn down.
                        ap = app.state.async_pipelines.get(job_id)
                        if ap is not None and hasattr(ap, "cancel"):
                            try:
                                ap.cancel()
                            except Exception:
                                pass
                    client_task = asyncio.create_task(_next_client_msg())
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
        await websocket.close(code=1000)

    @app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(_require_api_token)])
    def cancel_job(job_id: str) -> Dict[str, Any]:
        """Cancel a running job (no-op if it doesn't exist).

        v3.2.0c Tier 1 — also cancels any registered :class:`AsyncPipeline`
        so in-flight ``asyncio.Task``s are torn down, not just the
        cooperative ``job.cancelled`` flag.
        """
        from video2text.progress import ProgressRegistry

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
        from video2text.progress import ProgressRegistry

        registry: ProgressRegistry = app.state.jobs  # type: ignore[attr-defined]
        job = registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return job.to_dict()

    # -----------------------------------------------------------------
    # v3.2.0c Tier 1 — async job submission + result retrieval
    # -----------------------------------------------------------------
    @app.post("/api/jobs", dependencies=[Depends(_require_api_token)])
    def submit_jobs(
        req: TranscribeRequest, request: Request,
    ) -> Dict[str, Any]:
        """Submit URLs as background jobs; returns immediately with job_ids.

        Each URL is run via :func:`video2text.progress.with_progress` on
        the app's :class:`ThreadPoolExecutor`.  The caller should:

        1. ``GET /ws/progress/{job_id}`` (WebSocket) to receive
           ``stage_start`` / ``stage_progress`` / ``stage_done`` / ``succeeded``
           / ``failed`` / ``cancelled`` events.
        2. ``POST /api/jobs/{job_id}/cancel`` to abort a running job.
        3. ``GET /api/jobs/{job_id}/result`` to fetch the Markdown once
           the job reaches ``finished``.

        The synchronous ``POST /api/transcribe`` endpoint is preserved
        for backwards compatibility with the Chrome extension and MCP.
        """
        _enforce_rate_limit(app.state.rate_limiter, request)
        if not req.urls:
            raise HTTPException(status_code=400, detail="urls must be non-empty")
        try:
            pipeline = _build_pipeline(req, workspace)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"init failed: {exc}")
        from video2text.progress import ProgressRegistry, with_progress
        from video2text.pipeline_async import AsyncPipeline

        registry: ProgressRegistry = app.state.jobs  # type: ignore[attr-defined]
        out_dir = workspace / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        # v3.2.0e wiring: 整批走一个 AsyncPipeline.run_batch,获得 GPU 感知并发
        # + 单视频失败隔离 + 可取消(见 cancel_job / ws_progress)。每个 URL 的
        # runner 用 with_progress 包装以保留进度事件与落盘,再套 _store_job_result
        # 安全写回 job_results。
        ap = AsyncPipeline(pipeline)
        urls = list(req.urls)
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
            )
            jobs_out.append({
                "job_id": job.job_id,
                "url": url,
                "status": "queued",
                "ws": f"/ws/progress/{job.job_id}",
                "result": f"/api/jobs/{job.job_id}/result",
            })
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
        return {"jobs": jobs_out}

    @app.get("/api/jobs/{job_id}/result", dependencies=[Depends(_require_api_token)])
    def job_result(job_id: str) -> Dict[str, Any]:
        """Return the markdown + metadata for a job.

        Returns ``finished=False`` until the job reaches a terminal
        state.  After completion, returns ``markdown`` (string) and
        optional ``engine`` / ``title``.  Cancelled jobs return
        ``cancelled=True`` with ``markdown=None``.
        """
        from video2text.progress import ProgressRegistry

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
            "stage": job.stage,
            "stage_current": job.stage_current,
            "stage_total": job.stage_total,
        }

    return app


# ---------------------------------------------------------------------------
# CLI (run via ``python -m video2text.web.app``)
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--workspace", type=Path, default=None)
    args = parser.parse_args(argv)

    if not _FASTAPI_AVAILABLE:
        print("FastAPI not installed. Run: pip install video2text[web]")
        return 1
    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed. Run: pip install video2text[web]")
        return 1
    app = create_app(workspace=args.workspace)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
