"""
核心 Pipeline - 真正参考 bili2text 的实现
这是整个系统的核心工作流
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

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
from .transcribers import (
    FasterWhisperTranscriber,
    Transcriber,
    WhisperTranscriber,
    WhisperXTranscriber,
)


class Pipeline:
    """核心工作流 Pipeline"""

    def __init__(
        self,
        settings: Settings,
        downloader: Optional[Downloader] = None,
        transcriber: Optional[Transcriber] = None,
    ):
        self.settings = settings
        self.downloader = downloader
        self.transcriber = transcriber or self._create_transcriber(settings)

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

        downloaded: Optional[DownloadResult] = None
        audio_path: Path

        if source.kind in ["bilibili", "video", "douyin", "tiktok", "youtube", "xiaohongshu"]:
            if source.kind in ["bilibili", "douyin", "tiktok", "youtube", "xiaohongshu"] or (source.url and not source.path):
                # 需要下载
                print("📥 下载视频...")
                # 智能选择下载器
                current_downloader = self._get_downloader(source)
                downloaded = current_downloader.download(source, self.settings)
                video_path = downloaded.video_path
                base_name = downloaded.title or source.display_name
            else:
                # 本地视频文件
                assert source.path is not None
                video_path = source.path
                base_name = source.display_name

            # 提取音频
            print("🔊 提取音频...")
            audio_result = extract_audio(
                video_path,
                self.settings.audio_dir,
                safe_stem(base_name),
            )
            if audio_result is None:
                raise RuntimeError("音频提取失败")
            audio_path = audio_result

        elif source.kind == "audio":
            # 直接是音频文件
            assert source.path is not None
            audio_path = source.path
            base_name = source.display_name
            video_path = None

        else:
            raise ValueError(f"不支持的源类型: {source.kind}")

        # 转录
        print("🎤 开始转录...")
        # 使用传入的语言，或配置中的语言
        lang = language or self.settings.language
        transcription = self.transcriber.transcribe(
            audio_path,
            prompt=prompt,
            progress=True,
            language=lang,
        )

        text = transcription.get("text", "").strip()
        if not text:
            raise RuntimeError("转录结果为空")

        # 保存结果
        print("💾 保存结果...")
        transcript_path = self._resolve_output_path(base_name, output)
        metadata_path = self._resolve_metadata_path(transcript_path)

        transcript_path.parent.mkdir(parents=True, exist_ok=True)

        # 构建 Markdown 格式内容
        markdown_content = self._build_markdown_content(
            base_name,
            text,
            transcription,
            downloaded,
        )
        transcript_path.write_text(markdown_content, encoding="utf-8")

        # 保存元数据
        metadata = {
            "source": {
                "raw_input": source.raw_input,
                "kind": source.kind,
                "bv": source.bv,
                "url": source.url,
                "path": str(source.path) if source.path else None,
            },
            "engine": self.transcriber.name,
            "model": transcription.get("model"),
            "audio_path": str(audio_path),
            "video_path": str(video_path) if video_path else None,
            "download_metadata": downloaded.metadata if downloaded else None,
            "language": transcription.get("language"),
            "speaker_diarization": transcription.get("speaker_diarization", False),
            "generated_at": datetime.now().isoformat(),
        }
        metadata_path.write_text(
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

        transcript_path.write_text("\n".join(lines), encoding="utf-8")

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
        metadata_path.write_text(
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
        transcript_path.write_text(markdown_content, encoding="utf-8")

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
        metadata_path.write_text(
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
