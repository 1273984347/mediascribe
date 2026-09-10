"""
WhisperX 转录器 - 真正参考 WhisperX 的实现流程

模块级模型缓存：主模型按 ``(model_name, device, compute_type)``、对齐模型按
``(language_code, device)`` 复用实例，同一转录器连转 N 个音频不再重复加载
（模型加载约 5-10s/次）。``clear_model_cache()`` 可手动释放显存（含对齐模型）。
"""
from __future__ import annotations

import gc
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import torch

from .base import Transcriber

# ---------------------------------------------------------------------------
# 模型缓存 — 主模型与对齐模型分别缓存，避免每次转录都重新加载
# ---------------------------------------------------------------------------
_MODEL_CACHE: Dict[Tuple[str, str, str], Any] = {}
_ALIGN_MODEL_CACHE: Dict[Tuple[str, str], Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def _get_cached_model(
    whisperx: Any, model_name: str, device: str, language: Optional[str]
) -> Any:
    """取或加载 WhisperX 主模型（按 ``(model_name, device, compute_type)`` 缓存）。

    线程安全：双检锁，并发加载同一模型时只有一个会真正加载。
    """
    compute_type = "int8" if device == "cpu" else "float16"
    key = (model_name, device, compute_type)
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    with _MODEL_CACHE_LOCK:
        # 双检锁 - 避免两个线程同时通过了上面的 None 检查
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            return cached
        model = whisperx.load_model(
            model_name, device, compute_type=compute_type, language=language,
        )
        _MODEL_CACHE[key] = model
        return model


def _get_cached_align_model(
    whisperx: Any, language_code: str, device: str
) -> Tuple[Any, Any]:
    """取或加载 word-level 对齐模型（按 ``(language_code, device)`` 缓存）。

    返回 ``(align_model, meta)``。
    """
    key = (language_code, device)
    cached = _ALIGN_MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    with _MODEL_CACHE_LOCK:
        # 双检锁
        cached = _ALIGN_MODEL_CACHE.get(key)
        if cached is not None:
            return cached
        align_model, meta = whisperx.load_align_model(
            language_code=language_code, device=device,
        )
        _ALIGN_MODEL_CACHE[key] = (align_model, meta)
        return align_model, meta


def clear_model_cache() -> None:
    """清空模型缓存（含对齐模型），释放显存。测试与显存紧张时用。"""
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()
        _ALIGN_MODEL_CACHE.clear()
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


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
            print("加载 WhisperX 模型（优先取缓存）...")

        # 第 1 步：转录 + VAD（模型走模块级缓存，连转 N 个音频只加载一次）
        model = _get_cached_model(whisperx, self.model_name, self.device, language)

        audio = whisperx.load_audio(str(audio_path))
        result = model.transcribe(audio, batch_size=16)

        # 第 2 步：Word-level 对齐（对齐模型同样走缓存）
        if result["segments"]:
            if progress:
                print("执行 Word-level 对齐...")

            align_model, meta = _get_cached_align_model(
                whisperx, result["language"], self.device,
            )

            result = whisperx.align(
                result["segments"],
                align_model,
                meta,
                audio,
                self.device,
            )

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
