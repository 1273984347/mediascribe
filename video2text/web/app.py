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
import os
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

# FastAPI / Pydantic are optional dependencies.  The CLI may
# still want to print the help without them, so we keep imports
# inside the route handlers.
try:
    from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel, Field
    _FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - optional
    _FASTAPI_AVAILABLE = False

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
        model: str = Field("small", pattern="^(tiny|base|small|medium|large)$")
        language: Optional[str] = None
        ocr_engine: str = Field("auto", pattern="^(auto|paddleocr|pytesseract|easyocr|none)$")
        wechat_cookies: Optional[str] = None
        save_images: bool = False
        bilingual: bool = False

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
    from video2text.config import Settings
    from video2text.pipeline import Pipeline

    cookies: Optional[Dict[str, str]] = None
    if req.wechat_cookies:
        cookies = {}
        for part in req.wechat_cookies.split(";"):
            if "=" in part:
                k, _, v = part.partition("=")
                cookies[k.strip()] = v.strip()

    settings = Settings(
        workspace_root=workspace,
        model=req.model,
        engine=req.engine,
        language=req.language,
        wechat_cookies=cookies,
    )
    return Pipeline(settings)


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

    app = FastAPI(
        title="Video2Text Web UI",
        version="3.0.0",
        description="Transcribe videos to Markdown from your browser.",
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

    workspace = workspace or Path.cwd() / "web-workspace"

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        # Health is always public so the launcher + Docker can probe it
        # without holding a secret.  Return whether auth + rate limiting
        # are enabled so a dashboard can show a useful banner.
        return {
            "ok": True,
            "version": "3.0.0",
            "auth_enabled": app.state.api_token_configured,
            "rate_limit_enabled": app.state.rate_limiter.enabled,
            "rate_limit_max": app.state.rate_limiter.max_requests,
            "rate_limit_window": app.state.rate_limiter.window_seconds,
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
