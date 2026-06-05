"""
WhisperX 转录器 - 真正参考 WhisperX 的实现流程
"""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Optional

import torch

from .base import Transcriber


class WhisperXTranscriber(Transcriber):
    """WhisperX 转录器（支持说话人分离）"""
    name = "whisperx"

    def __init__(
        self,
        model: str = "small",
        device: Optional[str] = None,
        hf_token: Optional[str] = None,
        diarization: bool = False,
    ):
        self.model_name = model
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.hf_token = hf_token
        self.diarization = diarization
        self._model: Any = None

    def transcribe(
        self,
        audio_path: Path,
        *,
        prompt: Optional[str] = None,
        progress: Optional[Any] = None,
        **kwargs,
    ) -> dict[str, Any]:
        try:
            import whisperx
        except ImportError as e:
            raise RuntimeError("WhisperX 未安装，请运行: pip install whisperx") from e

        language = kwargs.get("language")

        if progress:
            print("加载 WhisperX 模型...")

        # 第 1 步：转录 + VAD
        model = whisperx.load_model(
            self.model_name,
            self.device,
            compute_type="int8" if self.device == "cpu" else "float16",
            language=language,
        )

        audio = whisperx.load_audio(str(audio_path))
        result = model.transcribe(audio, batch_size=16)

        # 清理
        del model
        gc.collect()
        if self.device == "cuda":
            torch.cuda.empty_cache()

        # 第 2 步：Word-level 对齐
        if result["segments"]:
            if progress:
                print("执行 Word-level 对齐...")

            align_model, meta = whisperx.load_align_model(
                language_code=result["language"],
                device=self.device,
            )

            result = whisperx.align(
                result["segments"],
                align_model,
                meta,
                audio,
                self.device,
            )

            del align_model
            gc.collect()
            if self.device == "cuda":
                torch.cuda.empty_cache()

        # 第 3 步：说话人分离（可选）
        speaker_diarization = False
        if self.diarization and self.hf_token:
            if progress:
                print("执行说话人分离...")

            try:
                diarize_model = whisperx.DiarizationPipeline(
                    token=self.hf_token,
                    device=self.device,
                )
                diarize_segments = diarize_model(str(audio_path))
                result = whisperx.assign_word_speakers(diarize_segments, result)
                speaker_diarization = True
            except Exception as e:
                print(f"说话人分离失败: {e}")

        return {
            "text": " ".join([seg.get("text", "") for seg in result.get("segments", [])]).strip(),
            "segments": result.get("segments", []),
            "language": result.get("language"),
            "model": self.model_name,
            "speaker_diarization": speaker_diarization,
        }
