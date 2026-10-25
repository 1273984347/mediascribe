"""Pipeline 构建 / 复用, 与单条转写执行。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional

from mediascribe.web.models import TranscribeItem, TranscribeRequest

logger = logging.getLogger(__name__)


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


_PIPELINE_CACHE: "OrderedDict[tuple, Any]" = OrderedDict()


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
