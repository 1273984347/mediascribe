"""
核心 Pipeline - 真正参考 bili2text 的实现
这是整个系统的核心工作流

v3.2.0b 重构：把 ``transcribe()`` 中的串行 4 步抽到
:mod:`video2text.pipeline_stages` 的 5 个 :class:`Stage` 子类,
:func:`Pipeline._run_stage_chain` 负责把 chain 串起来。
保留所有 v3.1.0 / v3.2.0a 公共契约:

* ``Pipeline(settings, downloader, transcriber)`` 签名不变
* ``Pipeline.transcribe(source_input, **kwargs)`` 签名与返回类型不变
* 微信公众号文本型文章走老的 ``_handle_wechat_mp`` 路径
"""
from __future__ import annotations

import json
import logging
import platform
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .audio_utils import extract_audio
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
from .pipeline_stages import (
    PipelineContext,
    Stage,
    _atomic_write_text,
    default_chain,
)
from .transcribers import (
    FasterWhisperTranscriber,
    Transcriber,
    WhisperTranscriber,
    WhisperXTranscriber,
)

logger = logging.getLogger(__name__)


def resolve_device(requested: Optional[str] = "auto") -> str:
    """把 ``"auto"`` / ``None`` / 显式设备字符串归一化成 backend 名称。

    v3.2.0b Tier 1 — 端到端 GPU 加速的第一块砖。返回的字符串直接
    喂给 ``faster_whisper.WhisperModel(device=...)`` 与
    ``whisperx.load_model(device=...)``。

    探测顺序
    --------
    1. 显式 ``requested`` 非 ``auto`` / ``None`` → 原样返回
    2. ``torch.cuda.is_available()``     → ``"cuda"``
    3. Apple Silicon                    → ``"metal"``
    4. AMD ROCm                         → ``"rocm"``
    5. 兜底                             → ``"cpu"``

    任何 torch 探测失败都会被吞掉并退回到 ``cpu``,绝不抛异常 —
    启动期 GPU 探测失败不应阻塞 CLI / Web 启动。
    """
    if requested and requested != "auto":
        return requested

    # 1. CUDA (nvidia)
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda"
        # 2. Apple Silicon Metal
        if (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ):
            return "metal"
        # 3. AMD ROCm
        if getattr(torch.version, "hip", None):
            return "rocm"
    except Exception:
        # torch 未安装 / 损坏 — 退到 cpu 不抛错
        pass

    # 4. 兜底
    return "cpu"


def gpu_health() -> dict:
    """``GET /api/health`` 的 ``gpu`` 块 — 0.1 s 探针。

    v3.2.0b Tier 1 Tier 1 should-have 5。返回结构:

    .. code-block:: python

        {
            "available": True,
            "device": "cuda",
            "name": "NVIDIA GeForce RTX 4090",
            "vram_total_mb": 24576,
            "vram_used_mb": 10221,
            "temperature_c": 41,
            "utilisation_pct": 38,
            "backend": "torch",
        }

    任何子探测失败都会让 ``available`` 变 ``False`` 但不抛错。
    """
    info: dict = {
        "available": False,
        "device": "cpu",
        "name": None,
        "vram_total_mb": None,
        "vram_used_mb": None,
        "temperature_c": None,
        "utilisation_pct": None,
        "backend": None,
    }
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            info.update(
                available=True,
                device="cuda",
                backend="torch",
            )
            try:
                idx = torch.cuda.current_device()
                props = torch.cuda.get_device_properties(idx)
                info["name"] = props.name
                # 新版 API: torch.cuda.mem_get_info
                try:
                    free, total = torch.cuda.mem_get_info(idx)
                    info["vram_total_mb"] = int(total / (1024 * 1024))
                    info["vram_used_mb"] = int((total - free) / (1024 * 1024))
                except Exception:
                    pass
            except Exception:
                pass
            return info
        if (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
            and platform.system() == "Darwin"
        ):
            return {**info, "available": True, "device": "metal", "backend": "torch"}
    except Exception:
        pass
    return info


class Pipeline:
    """核心工作流 Pipeline"""

    def __init__(
        self,
        settings: Settings,
        downloader: Optional[Downloader] = None,
        transcriber: Optional[Transcriber] = None,
        *,
        profile: bool = False,
        profile_log: Optional[Path] = None,
    ):
        """
        Parameters
        ----------
        profile
            v3.2.0c Tier 2.  When ``True``, every ``stage.run_with_progress``
            call is wrapped in :func:`video2text.performance.profile_step`
            so per-stage wall-clock is recorded in ``STEP_TIMES`` and
            (optionally) appended to ``profile_log`` as JSONL.  Use the
            v3.2.0a ``profile`` CLI to read it back.
        profile_log
            Optional JSONL path; only used when ``profile=True``.
            If ``None``, timings are still recorded in memory via
            :data:`video2text.performance.STEP_TIMES` and can be
            inspected via :func:`get_step_times`.
        """
        self.settings = settings
        self.downloader = downloader
        self.transcriber = transcriber or self._create_transcriber(settings)
        self.profile = bool(profile)
        self.profile_log = Path(profile_log) if profile_log else None

    def _create_transcriber(self, settings: Settings) -> Transcriber:
        """根据设置创建转录器"""
        if settings.engine == "whisperx":
            return WhisperXTranscriber(
                model=settings.model,
                device=settings.device,
                hf_token=settings.hf_token,
                diarization=settings.diarization,
            )
        elif settings.engine == "faster-whisper":
            return FasterWhisperTranscriber(
                model=settings.model,
                device=settings.device,
            )
        else:
            return WhisperTranscriber(
                model=settings.model,
                device=settings.device,
            )

    def _get_downloader(self, source: SourceRef) -> Downloader:
        """根据源类型获取合适的下载器。

        路由策略（fallback 链）：
        1. 显式传入的下载器 → 始终使用
        2. 小红书 → XiaohongshuDownloader（Playwright 必需），失败时回退 YtDlpDownloader
        3. 抖音 → DouyinDownloader（无需 cookies），失败时回退 YtDlpDownloader
        4. YouTube → YouTubeDownloader（player_client 调优）
        5. 微信公众号 → WechatMpDownloader（文本/视频），失败时回退 YtDlpDownloader
        6. 其他 → YtDlpDownloader
        """
        if self.downloader:
            return self.downloader

        if source.kind == "xiaohongshu":
            try:
                return XiaohongshuDownloader()
            except Exception as e:
                print(f"   ⚠️  小红书下载器初始化失败: {e}")
                print("   💡 将回退到 yt-dlp")

        if source.kind == "douyin":
            # 先尝试 DouyinDownloader
            try:
                return DouyinDownloader()
            except Exception as e:
                print(f"   ⚠️  抖音专用下载器初始化失败: {e}")
                print("   💡 将回退到 yt-dlp")

        if source.kind == "youtube":
            try:
                return YouTubeDownloader()
            except Exception as e:
                print(f"   ⚠️  YouTube 下载器初始化失败: {e}")
                print("   💡 将回退到 yt-dlp")

        if source.kind == "wechat_mp":
            try:
                return WechatMpDownloader()
            except Exception as e:
                print(f"   ⚠️  微信公众号下载器初始化失败: {e}")
                print("   💡 将回退到 yt-dlp")

        # 默认使用 yt-dlp
        return YtDlpDownloader()

    def transcribe(
        self,
        source_input: str,
        *,
        prompt: Optional[str] = None,
        output: Optional[Path] = None,
        language: Optional[str] = None,
        ocr_engine: Optional[str] = None,
        ocr_lang: Optional[str] = None,
        save_images: bool = False,
        bilingual: bool = False,
    ) -> TranscriptResult:
        """
        完整的转录流程：
        1. 解析输入源
        2. 下载（如果是 URL）
        3. 提取音频
        4. 转录
        5. 保存结果

        v3.2.0b: 1-5 步被 :func:`video2text.pipeline_stages.default_chain`
        拆成 5 个 :class:`Stage` 子类顺序执行;本方法只负责组装
        :class:`PipelineContext`、跑 chain,并在微信公众号文本型文章
        这条分支上走老的 ``_handle_wechat_mp``。

        WeChat MP 专属参数（仅在 source.kind == "wechat_mp" 时生效）：
        - ``ocr_engine``：auto / paddleocr / pytesseract / easyocr
        - ``ocr_lang``：OCR 语言代码（默认 chi_sim+eng）
        - ``save_images``：是否把图片 URL 下载到本地
        - ``bilingual``：公众号视频消息是否输出双语字幕标签
        """
        self.settings.ensure_directories()
        source = parse_source(source_input)

        print(f"\n🎬 处理: {source.display_name}")
        print(f"📋 类型: {source.kind}")

        # 微信公众号文本型文章：跳过 ASR，直接落盘 markdown
        if source.kind == "wechat_mp":
            return self._handle_wechat_mp(
                source,
                output=output,
                ocr_engine=ocr_engine,
                ocr_lang=ocr_lang,
                save_images=save_images,
                bilingual=bilingual,
            )

        # ---- v3.2.0b stage chain ----
        ctx = PipelineContext(
            settings=self.settings,
            source_input=source_input,
            prompt=prompt,
            output=output,
            language=language,
            ocr_engine=ocr_engine,
            ocr_lang=ocr_lang,
            save_images=save_images,
            bilingual=bilingual,
            source=source,
        )
        return self._run_stage_chain(ctx).result

    # ------------------------------------------------------------------
    # v3.2.0b stage chain orchestrator
    # ------------------------------------------------------------------
    def _run_stage_chain(self, ctx: PipelineContext) -> PipelineContext:
        """按 :func:`default_chain` 顺序跑每个 stage,中途失败立即抛。

        chain 由 :meth:`_build_chain` 构造,默认 5 个 stage
        (parse → download → extract_audio → transcribe → assemble)。
        v3.2.0b Tier 1 会在 ``asyncio.Pipeline`` 中用
        ``asyncio.to_thread(stage.run, ctx)`` 取代本方法 — 同步版本
        必须保留以维持 :class:`Pipeline` 公共契约。

        v3.2.0c Tier 2 — 当 ``self.profile=True`` 时,每个 stage 调用
        被 :func:`video2text.performance.profile_step` 包装,记录
        wall-clock 到 :data:`STEP_TIMES` (内存) + 可选 JSONL 文件。
        """
        # Lazy import so performance.py is only touched when profile is used.
        if self.profile:
            from .performance import clear_step_times, profile_step
            clear_step_times()  # fresh slate per pipeline run
        for stage in self._build_chain():
            if not stage.should_run(ctx):
                continue
            if self.profile:
                # Wrap stage.run_with_progress with profile_step so the
                # timing label matches the stage name.  ``log_to`` is
                # passed through so the JSONL sidecar is written if the
                # user supplied ``profile_log``.
                runner = profile_step(stage.name, log_to=self.profile_log)(
                    stage.run_with_progress
                )
                ctx = runner(ctx)
            else:
                ctx = stage.run_with_progress(ctx)
            if ctx.result is not None:
                # 微信公众号文本型文章等场景:assemble 提前写入 result 后
                # chain 也应停止,避免后续 stage 在不完整 ctx 上出错
                break
        assert ctx.result is not None, "stage chain did not produce a result"
        return ctx

    def _build_chain(self) -> List[Stage]:
        """构造 :class:`Pipeline` 使用的 stage 链。"""
        return default_chain(
            transcriber=self.transcriber,
            downloader=self.downloader,
            downloader_getter=self._get_downloader,
            resolve_output_path=self._resolve_output_path,
            resolve_metadata_path=self._resolve_metadata_path,
            build_markdown=self._build_markdown_content,
        )

    def _build_markdown_content(
        self,
        title: str,
        text: str,
        transcription: dict,
        downloaded: Optional[DownloadResult],
    ) -> str:
        """构建 Markdown 格式的内容"""
        lines = []

        # 标题
        lines.append(f"# {title}")
        lines.append("")

        # 元数据信息
        lines.append("## 基本信息")
        lines.append("")

        if downloaded and downloaded.metadata:
            meta = downloaded.metadata
            if meta.get("uploader"):
                lines.append(f"- **作者**: {meta['uploader']}")
            if meta.get("duration"):
                duration_min = int(meta["duration"] // 60)
                duration_sec = int(meta["duration"] % 60)
                lines.append(f"- **时长**: {duration_min}分{duration_sec}秒")

        lines.append(f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"- **转录引擎**: {transcription.get('model', 'unknown')}")
        lines.append(f"- **识别语言**: {transcription.get('language', 'unknown')}")
        lines.append("")

        # 处理段落，自动分段
        paragraphs = self._split_into_paragraphs(text)

        # 转录内容
        lines.append("## 转录内容")
        lines.append("")

        for paragraph in paragraphs:
            if paragraph.strip():
                lines.append(paragraph)
                lines.append("")

        return "\n".join(lines)

    def _split_into_paragraphs(self, text: str) -> list[str]:
        """将文本分割成段落"""
        # 简单的分段：按常见的中文标点符号分割
        # 根据句子长度和标点符号自动分段
        sentences = []
        current = []

        # 按常见标点分割
        chars = list(text)
        i = 0
        n = len(chars)

        while i < n:
            c = chars[i]
            current.append(c)

            # 遇到句号、问号、感叹号等标点时，考虑分段
            if c in ['。', '！', '？', '!', '?'] and i + 1 < n:
                # 如果当前段落较长，或者后面是空字符，就分段
                if len(current) > 50 or chars[i + 1] in [' ', '\n', '\t']:
                    sentences.append(''.join(current).strip())
                    current = []

            i += 1

        if current:
            sentences.append(''.join(current).strip())

        # 组合成段落
        paragraphs = []
        current_paragraph = []

        for sentence in sentences:
            if sentence:
                current_paragraph.append(sentence)
                # 每 2-4 句组合成一个段落
                if len(current_paragraph) >= 3:
                    paragraphs.append(''.join(current_paragraph))
                    current_paragraph = []

        if current_paragraph:
            paragraphs.append(''.join(current_paragraph))

        return paragraphs if paragraphs else [text]

    def _resolve_output_path(self, base_name: str, output: Optional[Path] = None) -> Path:
        """解析输出路径 - 参考 bili2text"""
        if output is None:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return self.settings.transcripts_dir / f"{safe_stem(base_name)}-{timestamp}.md"

        output = output.expanduser()
        if output.suffix.lower() != ".md":
            if output.exists() and output.is_dir():
                return output / f"{safe_stem(base_name)}.md"
            return output.with_suffix(".md")
        return output

    def _resolve_metadata_path(self, transcript_path: Path) -> Path:
        """解析元数据路径"""
        if transcript_path.is_relative_to(self.settings.workspace_root):
            return self.settings.metadata_dir / f"{transcript_path.stem}.json"
        return transcript_path.with_suffix(".json")

    # ------------------------------------------------------------------
    # 微信公众号特殊处理
    # ------------------------------------------------------------------
    def _handle_wechat_mp(
        self,
        source: SourceRef,
        *,
        output: Optional[Path] = None,
        ocr_engine: Optional[str] = None,
        ocr_lang: Optional[str] = None,
        save_images: bool = False,
        bilingual: bool = False,
    ) -> TranscriptResult:
        """
        处理微信公众号来源。

        两种模式：
        - 文本文章：直接保存正文为 markdown（跳过 ASR）
        - 视频消息：走标准 ASR 流程（递归调用 transcriber）

        支持的可选参数：
        - ``ocr_engine`` / ``ocr_lang`` / ``save_images`` 透传给
          ``WechatMpDownloader.download()``，仅对图片型文章生效。
        - ``bilingual``：视频消息输出双语字幕标签（中英对照）
        """
        print("📥 下载公众号内容...")
        downloader = self._get_downloader(source)
        downloaded = downloader.download(
            source,
            self.settings,
            ocr_engine=ocr_engine,
            ocr_lang=ocr_lang,
            save_images=save_images,
        )

        meta = downloaded.metadata or {}
        base_name = downloaded.title or source.display_name

        # 视频型公众号消息：递归走 ASR 流程
        if meta.get("kind") == "wechat_mp_video":
            return self._transcribe_video_article(
                source, downloaded, base_name, output=output, bilingual=bilingual
            )

        # 文本型公众号文章：直接落盘
        text = (meta.get("text") or "").strip()
        if not text:
            raise RuntimeError("公众号文章正文为空")

        print("📝 公众号文本型文章：跳过 ASR，直接生成 markdown")
        transcript_path = self._resolve_output_path(base_name, output)
        metadata_path = self._resolve_metadata_path(transcript_path)
        transcript_path.parent.mkdir(parents=True, exist_ok=True)

        # 构建 markdown
        lines = [f"# {base_name}", "", "## 基本信息", ""]
        if meta.get("nickname"):
            lines.append(f"- **公众号**: {meta['nickname']}")
        if meta.get("author"):
            lines.append(f"- **作者**: {meta['author']}")
        if meta.get("url"):
            lines.append(f"- **原文链接**: {meta['url']}")
        lines.append("- **类型**: 微信公众号图文")
        # OCR 状态
        ocr_total = int(meta.get("ocr_total") or 0)
        ocr_success = int(meta.get("ocr_success") or 0)
        if ocr_total:
            lines.append(
                f"- **图片 OCR**: {ocr_success}/{ocr_total}（已附在文末）"
            )
        wechat_status = meta.get("wechat_mp_status") or "success"
        lines.append(f"- **状态**: {wechat_status}")
        lines.append(f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append("## 正文")
        lines.append("")
        # 段落分隔保留
        for paragraph in text.split("\n\n"):
            if paragraph.strip():
                lines.append(paragraph.strip())
                lines.append("")

        # OCR 内容追加到文末（如果有）
        ocr_texts = meta.get("ocr_texts") or []
        if ocr_texts:
            lines.append("## 图片 OCR 文本")
            lines.append("")
            for i, ocr in enumerate(ocr_texts, 1):
                lines.append(f"### 图片 {i}")
                lines.append("")
                lines.append(ocr.strip())
                lines.append("")

        _atomic_write_text(transcript_path, "\n".join(lines), encoding="utf-8")

        # metadata
        metadata = {
            "source": {
                "raw_input": source.raw_input,
                "kind": source.kind,
                "url": source.url,
            },
            "engine": "wechat_mp_text",
            "model": "n/a",
            "audio_path": str(downloaded.video_path),
            "video_path": None,
            "download_metadata": meta,
            "language": "zh",
            "speaker_diarization": False,
            "generated_at": datetime.now().isoformat(),
        }
        _atomic_write_text(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("\n✅ 完成！")
        print(f"📝 转录文件: {transcript_path}")
        print(f"📋 元数据: {metadata_path}")
        return TranscriptResult(
            source=source,
            engine="wechat_mp_text",
            model="n/a",
            text=text,
            audio_path=downloaded.video_path,
            transcript_path=transcript_path,
            video_path=None,
            metadata_path=metadata_path,
            metadata=metadata,
            segments=None,
            language="zh",
            speaker_diarization=False,
        )

    def _transcribe_video_article(
        self,
        source: SourceRef,
        downloaded: DownloadResult,
        base_name: str,
        *,
        output: Optional[Path] = None,
        bilingual: bool = False,
    ) -> TranscriptResult:
        """视频型公众号消息：提取音频 → ASR → 落盘。

        当 ``bilingual=True`` 时，markdown 中除了 ASR 原文（中文）外，
        还会附加 ``LABEL_BILINGUAL_*`` 双语标签（中英对照），让 Agent /
        编辑器能直接读出哪个平台、用什么引擎、识别语种。
        """
        video_path = downloaded.video_path
        print("🔊 提取音频...")
        audio_result = extract_audio(
            video_path, self.settings.audio_dir, safe_stem(base_name)
        )
        if audio_result is None:
            raise RuntimeError("音频提取失败")
        audio_path = audio_result

        print("🎤 开始转录...")
        transcription = self.transcriber.transcribe(
            audio_path, prompt=None, progress=True, language=self.settings.language
        )
        text = transcription.get("text", "").strip()
        if not text:
            raise RuntimeError("转录结果为空")

        transcript_path = self._resolve_output_path(base_name, output)
        metadata_path = self._resolve_metadata_path(transcript_path)
        transcript_path.parent.mkdir(parents=True, exist_ok=True)

        # 视频文章走 ASR，但元数据依然要包含"双语字幕"标签
        markdown_content = self._build_video_article_markdown(
            base_name, text, transcription, downloaded, bilingual=bilingual
        )
        _atomic_write_text(transcript_path, markdown_content, encoding="utf-8")

        meta = downloaded.metadata or {}
        # 始终在 metadata 中记录 bilingual 标签，方便 Agent 读取
        metadata = {
            "source": {
                "raw_input": source.raw_input,
                "kind": source.kind,
                "url": source.url,
            },
            "engine": self.transcriber.name,
            "model": transcription.get("model"),
            "audio_path": str(audio_path),
            "video_path": str(video_path),
            "download_metadata": meta,
            "language": transcription.get("language"),
            "speaker_diarization": transcription.get("speaker_diarization", False),
            "bilingual": bilingual,
            "generated_at": datetime.now().isoformat(),
        }
        if bilingual:
            # i18n labels for Agent / scripts that consume JSON
            try:
                from douyin_batch.i18n import (
                    Messages,
                    get_language,
                    set_language,
                )
            except Exception:
                Messages = None
            if Messages is not None:
                prev_lang = get_language()
                labels_en: dict = {}
                labels_zh: dict = {}
                for lang in ("en", "zh"):
                    set_language(lang)
                    if lang == "en":
                        labels_en["platform"] = (
                            Messages.PLATFORM_WECHAT_MP.get(lang, "")
                        )
                    else:
                        labels_zh["platform"] = (
                            Messages.PLATFORM_WECHAT_MP.get(lang, "")
                        )
                set_language(prev_lang)
                metadata["i18n_labels"] = {
                    "en": labels_en,
                    "zh": labels_zh,
                    "active_lang": prev_lang,
                }
        _atomic_write_text(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("\n✅ 完成！")
        print(f"📝 转录文件: {transcript_path}")
        print(f"📋 元数据: {metadata_path}")
        return TranscriptResult(
            source=source,
            engine=self.transcriber.name,
            model=str(transcription.get("model", "")),
            text=text,
            audio_path=audio_path,
            transcript_path=transcript_path,
            video_path=video_path,
            metadata_path=metadata_path,
            metadata=metadata,
            segments=transcription.get("segments"),
            language=transcription.get("language"),
            speaker_diarization=transcription.get("speaker_diarization", False),
        )

    def _build_video_article_markdown(
        self,
        title: str,
        text: str,
        transcription: dict,
        downloaded: DownloadResult,
        *,
        bilingual: bool = False,
    ) -> str:
        """
        构建视频型公众号消息的 markdown。

        - 基础模式：标题 + 基本信息 + 转录内容（与标准 ASR 流程一致）
        - bilingual 模式：基本信息区以「中文 / English」形式输出双语标签，
          文末追加字幕说明块（Subtitle / 字幕）。
        """
        lines: list = []
        lines.append(f"# {title}")
        lines.append("")

        lines.append("## 基本信息 / Basic Information")
        lines.append("")

        meta = downloaded.metadata or {}
        if meta.get("nickname"):
            lines.append(f"- **公众号 / WeChat Account**: {meta['nickname']}")
        if meta.get("author"):
            lines.append(f"- **作者 / Author**: {meta['author']}")
        if meta.get("url"):
            lines.append(f"- **原文链接 / Source URL**: {meta['url']}")
        lines.append("- **类型 / Type**: 微信公众号视频消息 / WeChat MP video message")
        lines.append(
            f"- **生成时间 / Generated At**: "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        lines.append(
            f"- **转录引擎 / Engine**: "
            f"{transcription.get('model', 'unknown')}"
        )
        lines.append(
            f"- **识别语言 / Detected Language**: "
            f"{transcription.get('language', 'unknown')}"
        )
        lines.append("")

        if bilingual:
            # 引入 i18n 模块失败时优雅降级到基础模式
            try:
                from douyin_batch.i18n import Messages  # noqa: F401
            except Exception:
                Messages = None

            if Messages is not None:
                diar = bool(transcription.get("speaker_diarization", False))
                enabled_zh = Messages.LABEL_BILINGUAL_ENABLED.get("zh", "已启用")
                enabled_en = Messages.LABEL_BILINGUAL_ENABLED.get("en", "Enabled")
                disabled_zh = Messages.LABEL_BILINGUAL_DISABLED.get("zh", "未启用")
                disabled_en = Messages.LABEL_BILINGUAL_DISABLED.get("en", "Disabled")
                speaker_label = (
                    f"{enabled_zh} / {enabled_en}"
                    if diar
                    else f"{disabled_zh} / {disabled_en}"
                )
                platform_zh = Messages.PLATFORM_WECHAT_MP.get("zh", "微信公众号")
                platform_en = Messages.PLATFORM_WECHAT_MP.get("en", "WeChat MP")
                platform_label_zh = Messages.LABEL_BILINGUAL_PLATFORM.get("zh", "平台")
                platform_label_en = Messages.LABEL_BILINGUAL_PLATFORM.get("en", "Platform")
                speakers_label_zh = Messages.LABEL_BILINGUAL_SPEAKERS.get("zh", "说话人分离")
                speakers_label_en = Messages.LABEL_BILINGUAL_SPEAKERS.get("en", "Speaker Diarization")
                lines.append(
                    f"- **{platform_label_zh} / {platform_label_en}**: "
                    f"{platform_zh} / {platform_en}"
                )
                lines.append(
                    f"- **{speakers_label_zh} / {speakers_label_en}**: "
                    f"{speaker_label}"
                )
                lines.append("")

                # 文末追加字幕说明块
                engine_label_zh = Messages.LABEL_BILINGUAL_ENGINE.get("zh", "转录引擎")
                engine_label_en = Messages.LABEL_BILINGUAL_ENGINE.get("en", "Transcription Engine")
                lang_label_zh = Messages.LABEL_BILINGUAL_LANG.get("zh", "检测语言")
                lang_label_en = Messages.LABEL_BILINGUAL_LANG.get("en", "Detected Language")
                subtitle_header_zh = Messages.LABEL_BILINGUAL_SUBTITLE_HEADER.get(
                    "zh", "## 字幕"
                )
                lines.append(subtitle_header_zh)
                lines.append("")
                lines.append(
                    f"- **{engine_label_zh} / {engine_label_en}**: "
                    f"{transcription.get('model', 'unknown')}"
                )
                lines.append(
                    f"- **{lang_label_zh} / {lang_label_en}**: "
                    f"{transcription.get('language', 'unknown')}"
                )
                # 段级别标签
                segments = transcription.get("segments") or []
                if segments and isinstance(segments, list):
                    lines.append("")
                    zh_prefix_template = Messages.LABEL_BILINGUAL_SEGMENT_PREFIX.get(
                        "zh", "[片段 {idx}]"
                    )
                    en_prefix_template = Messages.LABEL_BILINGUAL_SEGMENT_PREFIX.get(
                        "en", "[Segment {idx}]"
                    )
                    for idx, seg in enumerate(segments, 1):
                        if not isinstance(seg, dict):
                            continue
                        seg_text = (seg.get("text") or "").strip()
                        if not seg_text:
                            continue
                        zh_prefix = zh_prefix_template.format(idx=idx)
                        en_prefix = en_prefix_template.format(idx=idx)
                        ts = seg.get("start")
                        if ts is None:
                            ts = seg.get("t0")
                        if ts is not None:
                            ts_str = f" @ {float(ts):.1f}s"
                        else:
                            ts_str = ""
                        lines.append(
                            f"{zh_prefix} {seg_text} {en_prefix}{ts_str}"
                        )
                    lines.append("")

        # 转录内容
        lines.append("## 转录内容 / Transcript")
        lines.append("")
        for paragraph in self._split_into_paragraphs(text):
            if paragraph.strip():
                lines.append(paragraph)
                lines.append("")

        return "\n".join(lines)
