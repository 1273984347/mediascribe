"""
Pipeline stage framework (v3.2.0b prep).

把原 ``Pipeline.transcribe()`` 中串行的 4 步拆成可独立编排的
``Stage`` 子类:

* :class:`ParseSourceStage`     — ``parse_source(source_input)``
* :class:`DownloadStage`        — smart-routed ``downloader.download(...)``
* :class:`ExtractAudioStage`    — ffmpeg-based audio extraction
* :class:`TranscribeStage`      — ``transcriber.transcribe(...)``
* :class:`AssembleStage`        — Markdown + metadata 落盘

调用方 (``Pipeline.transcribe``) 仍负责 ``wechat_mp`` 文本型文章
等特殊分支;stage 框架处理的是 URL / 本地视频 / 音频这一主流
路径,因此 stage 链不破坏 v3.1.0 / v3.2.0a 暴露给 MCP、Web、CLI
的任何公共契约。

设计要点
--------
1. **Context 而非 kwargs** — 用 :class:`PipelineContext` 累加中间
   结果,避免 5+ 个 ``run`` 调用塞十几个参数。
2. **should_run() 旁路** — 音频文件跳过 DownloadStage、本地文件
   跳过 DownloadStage,etc,各 stage 自治。
3. **同步 + thread-friendly** — ``Stage.run`` 是同步方法,
   ``asyncio.Pipeline`` (v3.2.0b Tier 1) 用 ``asyncio.to_thread``
   把它推到默认 executor。这避免在 v3.2.0b 阶段再改 stage 签名。
4. **可观测** — 每个 stage 暴露 :attr:`name` 与
   :meth:`run_with_progress` 包装器,被 :class:`Pipeline` 用来驱动
   v3.2.0a 的 ``ProgressRegistry`` 3-bar 进度条。
5. **可取消 (v3.2.0e)** — stage 在阻塞操作前后调
   :meth:`PipelineContext.check_cancel`,置位时抛
   :class:`PipelineCancelled` 让上层 ``AsyncPipeline`` 提早退出。

向后兼容
--------
v3.1.0 / v3.2.0a 的公共契约全部保持:

* ``Pipeline(settings, downloader, transcriber)`` 签名不变。
* ``Pipeline.transcribe(source_input, **kwargs)`` 签名不变。
* 任何调用方通过 ``Pipeline(...).transcribe(...)`` 拿到的
  :class:`TranscriptResult` 与 v3.2.0a 完全一致。
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional

from .audio_utils import extract_audio
from .cache import PersistentDownloadCache
from .config import Settings
from .downloaders import (
    DouyinDownloader,
    Downloader,
    WechatMpDownloader,
    XiaohongshuDownloader,
    YouTubeDownloader,
    YtDlpDownloader,
)
from .inputs import parse_source, safe_stem
from .models import DownloadResult, SourceRef, TranscriptResult
from .transcribers import Transcriber

logger = logging.getLogger(__name__)

# 来源类型白名单(URL 类,需要下载)
URL_KINDS = frozenset({"bilibili", "douyin", "tiktok", "youtube", "xiaohongshu"})
# 来源类型白名单(视频类,需要下载后提取音频)
VIDEO_KINDS = URL_KINDS | {"video"}


# ---------------------------------------------------------------------------
# v3.2.0x P2-5: 持久化下载缓存接线(默认关闭)
# ---------------------------------------------------------------------------
# 环境变量 ``VIDEO2TEXT_DOWNLOAD_CACHE=1`` 启用(读 os.environ,不改
# config.py)。启用后 DownloadStage 下载前先按 URL 查
# :class:`PersistentDownloadCache`,命中直接复用缓存文件,未命中下载
# 成功后回写缓存 — 同一 URL 跨 run 不再重复下载。
_DOWNLOAD_CACHE_ENV = "VIDEO2TEXT_DOWNLOAD_CACHE"

# 进程级单例(懒创建);测试用 :func:`_reset_download_cache` 重置。
_download_cache_singleton: Optional[PersistentDownloadCache] = None


def _download_cache_enabled() -> bool:
    raw = (os.environ.get(_DOWNLOAD_CACHE_ENV) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _get_download_cache() -> Optional[PersistentDownloadCache]:
    """返回进程级下载缓存单例;环境开关未启用时返回 ``None``。"""
    global _download_cache_singleton
    if not _download_cache_enabled():
        return None
    if _download_cache_singleton is None:
        _download_cache_singleton = PersistentDownloadCache()
    return _download_cache_singleton


def _reset_download_cache() -> None:
    """丢弃进程级单例(测试钩子;关闭开关后也用它释放实例)。"""
    global _download_cache_singleton
    _download_cache_singleton = None


# ---------------------------------------------------------------------------
# v3.2.0e: 取消异常 — stage 内部检测到 cancel_event 时抛
# ---------------------------------------------------------------------------
class PipelineCancelled(Exception):
    """``PipelineContext.cancel_event`` 被置位时 stage 抛此异常。

    ``AsyncPipeline.run_batch`` 把它当作普通 ``Exception`` 包装进
    ``_FailedResult(src, exc)`` (不区分取消/失败)。调用方可用
    ``isinstance(r.exc, PipelineCancelled)`` 区分取消 vs 失败语义。
    """


@dataclass
class PipelineContext:
    """
    在 stage 链上流动的不可变-ish 上下文。

    ``source_input`` 等 6 个字段由调用方 ``Pipeline.transcribe``
    在 chain 启动前填好;之后 stage 按 ``download → extract_audio →
    transcribe → assemble`` 顺序累加 ``downloaded`` / ``audio_path``
    / ``transcription`` / ``transcript_path`` 等字段,最终
    :class:`AssembleStage` 在 ``result`` 字段写入
    :class:`TranscriptResult` 实例。

    Attributes
    ----------
    progress_cb
        ``Callable[[stage_name, percent], None]``,v3.2.0a 的
        WebSocket 进度条用。``None`` 表示不报告。
    cancel_event
        ``threading.Event`` — 置位表示上层请求取消,各 stage 应在
        阻塞操作之间检查并提早退出(v3.2.0b 引入)。
    """

    settings: Settings
    source_input: str
    prompt: Optional[str] = None
    output: Optional[Path] = None
    language: Optional[str] = None
    ocr_engine: Optional[str] = None
    ocr_lang: Optional[str] = None
    save_images: bool = False
    bilingual: bool = False

    # 阶段累加
    source: Optional[SourceRef] = None
    downloaded: Optional[DownloadResult] = None
    video_path: Optional[Path] = None
    audio_path: Optional[Path] = None
    base_name: Optional[str] = None
    transcription: Optional[Dict[str, Any]] = None
    text: Optional[str] = None
    # v3.2.0x P2-12: TranscribeStage 写入的 transcriber.name 供
    # AssembleStage._transcriber_name 读取 — 显式声明,不再靠
    # getattr 兜底访问未声明字段。
    engine_name: Optional[str] = None
    transcript_path: Optional[Path] = None
    metadata_path: Optional[Path] = None
    metadata: Optional[Dict[str, Any]] = None
    result: Optional[TranscriptResult] = None

    # 控制
    cancel_event: Optional[threading.Event] = field(default=None)
    progress_cb: Optional[Callable[[str, float], None]] = field(default=None)

    def emit_progress(self, stage: str, percent: float) -> None:
        """调 ``progress_cb``,保护性捕获所有异常以避免污染 stage。"""
        if self.progress_cb is None:
            return
        try:
            self.progress_cb(stage, max(0.0, min(100.0, float(percent))))
        except Exception:
            # progress 上报失败不应中断 pipeline
            pass

    def check_cancel(self) -> bool:
        """v3.2.0b 占位: 取消事件置位时返回 True,stage 应尽快退出。"""
        return bool(self.cancel_event and self.cancel_event.is_set())

    def raise_if_cancelled(self) -> None:
        """v3.2.0e: 阻塞点检查 — 置位时抛 :class:`PipelineCancelled`。

        各 stage 在耗时操作(下载 / ffmpeg / ASR / 落盘)前后调用,
        让 ``AsyncPipeline.run_batch`` 能在取消时尽早退出而非等
        当前 stage 跑完。与 :meth:`check_cancel` 互补:后者返回 bool
        供调用方自行决定退出策略,本方法直接抛异常。
        """
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise PipelineCancelled()


# ---------------------------------------------------------------------------
# Stage 基类
# ---------------------------------------------------------------------------
class Stage(ABC):
    """单个 stage 的抽象基类。

    子类必须实现 :meth:`should_run` 和 :meth:`run`。
    :attr:`name` 既作为 :class:`PipelineContext` 进度上报里的
    ``stage`` 字段,也作为日志前缀。
    """

    name: ClassVar[str] = "stage"

    @abstractmethod
    def should_run(self, ctx: PipelineContext) -> bool:
        """是否在当前 ctx 上执行 :meth:`run`。

        默认实现: 不支持 should_run 的子类可返回 ``True``。常见的
        拒绝理由: ctx.source 还没解析、来源类型不需要该 stage 等。
        """
        raise NotImplementedError

    @abstractmethod
    def run(self, ctx: PipelineContext) -> PipelineContext:
        """执行 stage,返回更新后的 ctx(便于链式调用)。"""
        raise NotImplementedError

    def run_with_progress(
        self, ctx: PipelineContext
    ) -> PipelineContext:
        """包一层进度上报,确保每个 stage 至少 emit 一次 100%。

        v3.2.0a 的 WebSocket 进度条依赖 ``stage`` 字段,这里保证
        ``download / transcribe / assemble`` 三个 stage 在完成后
        一定会上报 100%,避免前端进度条卡在 99%。

        v3.2.0e 取消语义:
        * 正常完成 → emit 100%
        * 普通 ``Exception`` (含 RuntimeError) → 仍 emit 100%
          (前端把"出错"也视作"该 stage 已结束",避免进度条卡死)
        * :class:`PipelineCancelled` → 跳过 100% emit,不 emit 任何完成信号
          (取消 = 显式"未完成";前端通过 ``JobProgress.cancelled`` 事件
          获知取消态并切到 cancelled CSS 类,不依赖 per-stage 进度值。
          注意:``emit_progress`` 会 clamp 到 [0,100],所以不能用 -1.0
          之类的负值作信号 — 那会被压成 0.0 与"开始"不可区分)
        """
        ctx.emit_progress(self.name, 0.0)
        cancelled = False
        try:
            return self.run(ctx)
        except PipelineCancelled:
            cancelled = True
            raise
        finally:
            if not cancelled:
                ctx.emit_progress(self.name, 100.0)


# ---------------------------------------------------------------------------
# 具体 stage
# ---------------------------------------------------------------------------
class ParseSourceStage(Stage):
    """``parse_source(source_input)`` — 把字符串解析成 :class:`SourceRef`。
    """

    name = "parse"

    def should_run(self, ctx: PipelineContext) -> bool:
        return ctx.source is None

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.raise_if_cancelled()
        ctx.source = parse_source(ctx.source_input)
        return ctx


class DownloadStage(Stage):
    """根据 :attr:`SourceRef.kind` 选 downloader,下载视频到本地。

    智能路由策略与 v3.2.0a ``Pipeline._get_downloader`` 完全一致:

    * 小红书 → ``XiaohongshuDownloader`` (Playwright 必需)
    * 抖音   → ``DouyinDownloader``   (无需 cookies)
    * YouTube → ``YouTubeDownloader``  (player_client 调优)
    * 微信公众号 → ``WechatMpDownloader``
    * 其他   → ``YtDlpDownloader``    (兜底)

    ``downloader_getter`` 优先用 ``Pipeline.downloader``,否则按
    上述策略返回。失败的 downloader 初始化会回退 ``YtDlpDownloader``
    并写一行 warning,与旧行为一致。
    """

    name = "download"

    def __init__(
        self,
        downloader: Optional[Downloader],
        downloader_getter: Optional[Callable[[SourceRef], Downloader]] = None,
    ):
        # ``downloader`` 优先(Pipeline.__init__ 注入的固定下载器)
        # ``downloader_getter`` 退后(由 Pipeline 提供的智能路由)
        self._static_downloader = downloader
        self._getter = downloader_getter

    def should_run(self, ctx: PipelineContext) -> bool:
        if ctx.source is None:
            return False
        if ctx.source.kind in URL_KINDS:
            return True
        if ctx.source.kind == "video" and ctx.source.path is not None:
            return ctx.video_path is None
        if bool(ctx.source.url) and not ctx.source.path:
            return True
        return False

    def run(self, ctx: PipelineContext) -> PipelineContext:
        # P2-11: 跨 stage 边界校验改 RuntimeError(assert 会被 -O 剥掉)
        if ctx.source is None:
            raise RuntimeError(
                "DownloadStage requires ctx.source (run ParseSourceStage first)"
            )
        ctx.raise_if_cancelled()  # 网络下载前
        # 本地视频文件: 直接把 source.path 视作 video_path
        if ctx.source.kind == "video" and ctx.source.path is not None:
            ctx.video_path = ctx.source.path
            ctx.base_name = ctx.source.display_name
            return ctx

        cache = _get_download_cache()
        url = ctx.source.url
        # P2-5: 持久化下载缓存 — 命中直接复用,跳过网络
        if cache is not None and url:
            cached = cache.get(url)
            if cached is not None:
                ctx.downloaded = DownloadResult(
                    source=ctx.source,
                    video_path=cached,
                    title=ctx.source.display_name,
                    metadata={"download_cache": "hit"},
                )
                ctx.video_path = cached
                ctx.base_name = ctx.source.display_name
                return ctx

        downloader = self._resolve_downloader(ctx.source)
        downloaded = downloader.download(ctx.source, ctx.settings)
        ctx.raise_if_cancelled()  # 下载完成后(可能耗时数十秒)
        # P2-5: 下载成功后回写缓存(失败不阻塞主流程)
        if cache is not None and url and downloaded.video_path is not None:
            try:
                cache.put(
                    url,
                    downloaded.video_path,
                    suffix=downloaded.video_path.suffix,
                )
            except Exception as exc:  # 缓存写失败不影响本次结果
                logger.warning(
                    "PersistentDownloadCache.put failed for %s: %r", url, exc
                )
        ctx.downloaded = downloaded
        ctx.video_path = downloaded.video_path
        ctx.base_name = downloaded.title or ctx.source.display_name
        return ctx

    def _resolve_downloader(self, source: SourceRef) -> Downloader:
        if self._static_downloader is not None:
            return self._static_downloader
        if self._getter is not None:
            return self._getter(source)
        # 兜底:跟旧 _get_downloader 行为一致
        return _smart_pick_downloader(source)


def _smart_pick_downloader(source: SourceRef) -> Downloader:
    """与 v3.2.0a ``Pipeline._get_downloader`` 行为兼容的路由函数。

    v3.2.0x P2-14: 抽出为唯一实现 — ``Pipeline._get_downloader``
    直接委托到这里,删除旧副本;fallback 提示从 print 改 logger。

    抽出来便于 stage 测试中独立 mock 任一具体 downloader。
    """
    if source.kind == "xiaohongshu":
        try:
            return XiaohongshuDownloader()
        except Exception as e:  # pragma: no cover - 真实 fallback
            logger.warning("小红书下载器初始化失败: %s — 将回退到 yt-dlp", e)
    if source.kind == "douyin":
        try:
            return DouyinDownloader()
        except Exception as e:  # pragma: no cover
            logger.warning("抖音专用下载器初始化失败: %s — 将回退到 yt-dlp", e)
    if source.kind == "youtube":
        try:
            return YouTubeDownloader()
        except Exception as e:  # pragma: no cover
            logger.warning("YouTube 下载器初始化失败: %s — 将回退到 yt-dlp", e)
    if source.kind == "wechat_mp":
        try:
            return WechatMpDownloader()
        except Exception as e:  # pragma: no cover
            logger.warning("微信公众号下载器初始化失败: %s — 将回退到 yt-dlp", e)
    return YtDlpDownloader()


class ExtractAudioStage(Stage):
    """ffmpeg 抽音频到 ``settings.audio_dir``。

    旁路条件
    --------
    * ``source.kind == "audio"`` 时 ``video_path`` 已经是
      音频文件,直接当 ``audio_path`` 用,跳过 ffmpeg。
    """

    name = "extract_audio"

    def should_run(self, ctx: PipelineContext) -> bool:
        if ctx.source is None:
            return False
        if ctx.source.kind == "audio":
            return ctx.audio_path is None and ctx.source.path is not None
        return ctx.source.kind in VIDEO_KINDS and ctx.video_path is not None

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.source is None:  # P2-11: 边界校验,-O 下 assert 会被剥掉
            raise RuntimeError(
                "ExtractAudioStage requires ctx.source (run ParseSourceStage first)"
            )
        ctx.raise_if_cancelled()  # ffmpeg 前
        if ctx.source.kind == "audio":
            assert ctx.source.path is not None
            if ctx.base_name is None:
                ctx.base_name = ctx.source.display_name
            ctx.audio_path = ctx.source.path
            return ctx
        if ctx.video_path is None:
            raise RuntimeError(
                "ExtractAudioStage requires ctx.video_path (run DownloadStage first)"
            )
        if ctx.base_name is None:
            ctx.base_name = ctx.source.display_name
        audio = extract_audio(
            ctx.video_path, ctx.settings.audio_dir, safe_stem(ctx.base_name)
        )
        if audio is None:
            raise RuntimeError("音频提取失败")
        ctx.raise_if_cancelled()  # ffmpeg 后
        ctx.audio_path = audio
        return ctx


class TranscribeStage(Stage):
    """调 ``transcriber.transcribe`` 完成 ASR。

    v3.2.0b: 如果用户未指定 prompt,自动根据来源领域拼接
    :func:`video2text.post_process.get_prompt_template`。
    """

    name = "transcribe"

    def __init__(self, transcriber: Transcriber):
        self._transcriber = transcriber

    def should_run(self, ctx: PipelineContext) -> bool:
        return ctx.audio_path is not None

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.audio_path is None:  # P2-11: 边界校验,-O 下 assert 会被剥掉
            raise RuntimeError(
                "TranscribeStage requires ctx.audio_path "
                "(run ExtractAudioStage first)"
            )
        ctx.raise_if_cancelled()  # ASR 前(最长阻塞,通常数分钟)
        lang = ctx.language or ctx.settings.language

        # 自动拼接领域 Prompt (用户未指定时)
        prompt = ctx.prompt
        if not prompt:
            prompt = self._auto_prompt(ctx)

        transcription = self._transcriber.transcribe(
            ctx.audio_path,
            prompt=prompt,
            progress=True,
            language=lang,
        )
        ctx.raise_if_cancelled()  # ASR 后
        text = (transcription.get("text") or "").strip()
        if not text:
            raise RuntimeError("转录结果为空")
        ctx.transcription = transcription
        ctx.text = text
        # 把 transcriber name 存到 ctx,供 AssembleStage._transcriber_name 使用
        ctx.engine_name = self._transcriber.name
        return ctx

    @staticmethod
    def _auto_prompt(ctx: PipelineContext) -> str | None:
        """根据来源类型推断领域,合并学习术语,返回 prompt。

        领域模板 + 学习术语自然语言句子合并为完整 prompt。
        Whisper 对自然语言 prompt 效果最好。
        """
        try:
            from .post_process import get_prompt_template
        except Exception:
            get_prompt_template = None  # type: ignore
        try:
            from .learn import get_prompt_terms
        except Exception:
            get_prompt_terms = None  # type: ignore

        source = ctx.source
        domain = "general"
        if source is not None:
            kind_to_domain: dict[str, str] = {
                "douyin": "education",
                "bilibili": "education",
                "youtube": "tech",
                "xiaohongshu": "general",
            }
            domain = kind_to_domain.get(source.kind, "general")

        parts: list[str] = []
        if get_prompt_template is not None:
            base = get_prompt_template(domain)
            if base:
                parts.append(base)
        if get_prompt_terms is not None:
            learned = get_prompt_terms()
            if learned:
                parts.append(learned)

        return " ".join(parts) if parts else None


class AssembleStage(Stage):
    """写 Markdown + metadata JSON,封装 :class:`TranscriptResult`。

    路径解析与 markdown 段落切分复用 v3.2.0a ``Pipeline`` 内的
    私有方法。``Pipeline`` 通过 :meth:`set_helpers` 注入,避免
    stage 持有整条 Pipeline 引用形成循环。
    """

    name = "assemble"

    def __init__(
        self,
        resolve_output_path: Callable[[str, Optional[Path]], Path],
        resolve_metadata_path: Callable[[Path], Path],
        build_markdown: Callable[..., str],
    ):
        self._resolve_output_path = resolve_output_path
        self._resolve_metadata_path = resolve_metadata_path
        self._build_markdown = build_markdown

    def should_run(self, ctx: PipelineContext) -> bool:
        return bool(ctx.text)

    def run(self, ctx: PipelineContext) -> PipelineContext:
        # P2-11: 跨 stage 边界的关键校验改 RuntimeError(-O 下 assert
        # 会被剥掉,ctx 不完整时会带着 None 一路炸到更深处)。
        if ctx.source is None:
            raise RuntimeError(
                "AssembleStage requires ctx.source (run ParseSourceStage first)"
            )
        if ctx.text is None:
            raise RuntimeError(
                "AssembleStage requires ctx.text (run TranscribeStage first)"
            )
        if ctx.transcription is None:
            raise RuntimeError(
                "AssembleStage requires ctx.transcription "
                "(run TranscribeStage first)"
            )
        ctx.raise_if_cancelled()  # 落盘前(LLM 后处理可能阻塞)
        base_name = ctx.base_name or ctx.source.display_name

        # v3.2.0b: 自动后处理 (术语校正,含学习术语)
        text = self._post_process(ctx.text)

        # v3.2.0d: LLM 后处理 (专有名词 / 同音字 / 标点 / 分段)
        # 失败时回退到原文,不阻塞 pipeline
        text, llm_status, llm_model = self._llm_post_process(text, ctx)
        ctx.raise_if_cancelled()  # LLM 后处理后(可能阻塞)

        transcript_path = self._resolve_output_path(base_name, ctx.output)
        metadata_path = self._resolve_metadata_path(transcript_path)
        transcript_path.parent.mkdir(parents=True, exist_ok=True)

        markdown = self._build_markdown(
            base_name,
            text,
            ctx.transcription,
            ctx.downloaded,
        )
        # 在 markdown 头部插入后处理状态 banner
        markdown = self._inject_status_banner(markdown, llm_status, llm_model)
        # v3.2.0e+: 原子写避免半写污染（markdown 通常 5-200KB）
        _atomic_write_text(transcript_path, markdown, encoding="utf-8")

        metadata = {
            "source": {
                "raw_input": ctx.source.raw_input,
                "kind": ctx.source.kind,
                "bv": ctx.source.bv,
                "url": ctx.source.url,
                "path": str(ctx.source.path) if ctx.source.path else None,
            },
            "engine": self._transcriber_name(ctx),
            "model": ctx.transcription.get("model"),
            "audio_path": str(ctx.audio_path) if ctx.audio_path else None,
            "video_path": str(ctx.video_path) if ctx.video_path else None,
            "download_metadata": ctx.downloaded.metadata if ctx.downloaded else None,
            "language": ctx.transcription.get("language"),
            "speaker_diarization": ctx.transcription.get(
                "speaker_diarization", False
            ),
            "llm_post_process": {
                "status": llm_status,
                "model": llm_model,
            },
            "generated_at": datetime.now().isoformat(),
        }
        _atomic_write_text(metadata_path, _json_dump(metadata), encoding="utf-8")

        ctx.transcript_path = transcript_path
        ctx.metadata_path = metadata_path
        ctx.metadata = metadata
        ctx.result = TranscriptResult(
            source=ctx.source,
            engine=metadata["engine"],
            model=str(ctx.transcription.get("model", "")),
            text=ctx.text,
            audio_path=ctx.audio_path,
            transcript_path=transcript_path,
            video_path=ctx.video_path,
            metadata_path=metadata_path,
            metadata=metadata,
            segments=ctx.transcription.get("segments"),
            language=ctx.transcription.get("language"),
            speaker_diarization=ctx.transcription.get(
                "speaker_diarization", False
            ),
        )
        return ctx

    @staticmethod
    def _transcriber_name(ctx: PipelineContext) -> str:
        # P2-12: engine_name 已在 PipelineContext 显式声明,直接读。
        return ctx.engine_name or "unknown"

    @staticmethod
    def _post_process(text: str) -> str:
        """自动术语校正 (含学习术语)。"""
        try:
            from .post_process import post_process_transcript
            return post_process_transcript(text)
        except Exception:
            return text

    @staticmethod
    def _llm_post_process(
        text: str, ctx: PipelineContext
    ) -> tuple[str, str, str]:
        """LLM 后处理 (专有名词 / 同音字 / 标点 / 分段)。

        Returns
        -------
        (text, status, model) : tuple
            - text: 处理后文本 (失败回退原文)
            - status: ``llm-reviewed`` / ``llm-failed`` / ``llm-disabled`` /
              ``llm-skipped``
            - model: 使用的模型名 (未启用时为空字符串)
        """
        try:
            from .llm_post_process import LLMPostProcessor
        except ImportError:
            return text, "llm-disabled", ""
        try:
            processor = LLMPostProcessor.from_settings(ctx.settings)
            if processor is None:
                return text, "llm-disabled", ""
            processed, status = processor.post_process(
                text,
                context=_build_llm_context(ctx),
            )
            return processed, status, processor.model
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "LLM post-process pipeline stage failed: %r", exc
            )
            return text, "llm-failed", ""

    @staticmethod
    def _inject_status_banner(
        markdown: str, status: str, model: str
    ) -> str:
        """在 markdown 头部插入后处理状态 HTML 注释。

        Banner 形如::

            <!-- post-process: llm-reviewed (model=deepseek-chat) -->

        原有 markdown 内容保持不变。
        """
        try:
            from .llm_post_process import build_status_banner
            banner = build_status_banner(status, model)
        except Exception:
            banner = f"<!-- post-process: {status} -->"
        return f"{banner}\n\n{markdown}"


def _build_llm_context(ctx: PipelineContext) -> dict:
    """从 PipelineContext 提取 LLM 后处理需要的上下文。"""
    context: dict = {}
    if ctx.downloaded and ctx.downloaded.metadata:
        meta = ctx.downloaded.metadata
        # 标题字段优先级: title > webpage_url_domain > None
        title = meta.get("title") or meta.get("webpage_url_domain")
        if title:
            context["title"] = title
    if ctx.source is not None:
        context["kind"] = ctx.source.kind
        if ctx.source.url:
            context["source_url"] = ctx.source.url
    return context


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _json_dump(obj: Any) -> str:
    """:func:`json.dumps` 包装, ``ensure_ascii=False`` + ``indent=2``。

    抽出来便于 :class:`AssembleStage` 测试中替换(monkey-patch)。
    """
    import json

    return json.dumps(obj, ensure_ascii=False, indent=2)


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """v3.2.0e+: 原子写文件 — 写临时文件 → ``os.replace`` 原子替换。

    避免大文件半写时进程崩溃留下部分内容污染下次运行（读取半写 markdown
    会让 LLM 后处理 / 下游消费方拿到截断文本）。Windows (``MoveFileEx``
    带 ``MOVEFILE_REPLACE_EXISTING``) 与 POSIX (``rename``) 上均原子。

    临时文件与最终文件同目录保证 ``os.replace`` 是同卷操作（跨卷会触发
    copy + delete，失去原子性）。tmp 名含 pid + uuid8 避免并发请求
    写同一 target 时的 tmp 碰撞。

    内部走 ``Path.write_text`` 而非 ``open()+f.write()``，保持测试 mock
    兼容性（测试 monkey-patch ``Path.write_text`` 验证 AssembleStage 落盘
    行为）。不调 ``fsync``，依赖 OS page cache flush — 对开发态单机场景
    足够；若未来需强持久性可在 settings 加 ``fsync=True`` 选项。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / (
        f".{path.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}{path.suffix}.tmp"
    )
    try:
        tmp_path.write_text(content, encoding=encoding)
        os.replace(tmp_path, path)
    except Exception:
        # 写失败时清理 tmp,避免污染目录
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# 默认 chain
# ---------------------------------------------------------------------------
def default_chain(
    transcriber: Transcriber,
    downloader: Optional[Downloader],
    downloader_getter: Optional[Callable[[SourceRef], Downloader]],
    resolve_output_path: Callable[[str, Optional[Path]], Path],
    resolve_metadata_path: Callable[[Path], Path],
    build_markdown: Callable[..., str],
) -> List[Stage]:
    """返回 :class:`Pipeline` 默认使用的 stage 链。

    暴露成模块级函数以便 :class:`Pipeline` 之外也能复用,例如
    v3.2.0b 的 ``asyncio.Pipeline`` 会直接拿这个 chain 然后
    ``asyncio.gather`` + ``asyncio.to_thread`` 包装。
    """
    return [
        ParseSourceStage(),
        DownloadStage(downloader, downloader_getter),
        ExtractAudioStage(),
        TranscribeStage(transcriber),
        AssembleStage(
            resolve_output_path=resolve_output_path,
            resolve_metadata_path=resolve_metadata_path,
            build_markdown=build_markdown,
        ),
    ]


__all__ = [
    "PipelineContext",
    "PipelineCancelled",
    "Stage",
    "ParseSourceStage",
    "DownloadStage",
    "ExtractAudioStage",
    "TranscribeStage",
    "AssembleStage",
    "default_chain",
    "URL_KINDS",
    "VIDEO_KINDS",
]
