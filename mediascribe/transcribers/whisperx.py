"""
WhisperX 转录器 - 真正参考 WhisperX 的实现流程

模块级模型缓存：主模型按 ``(model_name, device, compute_type)``、对齐模型按
``(language_code, device)``、说话人分离管线按 ``(hf_token, device)`` 复用实例，
同一转录器连转 N 个音频不再重复加载（模型加载约 5-10s/次）。
``clear_model_cache()`` 可手动释放显存（含对齐模型与分离管线）。
"""

from __future__ import annotations

import gc
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import torch

from ..i18n import t
from ..zh_normalize import simplified_initial_prompt
from .base import Transcriber

# ---------------------------------------------------------------------------
# 模型缓存 — 主模型与对齐模型分别缓存，避免每次转录都重新加载
# ---------------------------------------------------------------------------
_MODEL_CACHE: "OrderedDict[Tuple[str, str, str, Optional[str]], Any]" = OrderedDict()
_ALIGN_MODEL_CACHE: Dict[Tuple[str, str], Any] = {}
_DIARIZE_CACHE: Dict[Tuple[str, str], Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()
# 与 faster_whisper 的 _MODEL_CACHE_MAX 同值: 多档位轮换时自动淘汰最久
# 未用的实例交还 GC, 否则长驻 Web 进程换一档就涨一档显存(8GB 卡第三档
# 即 OOM 风险, 2026-10-29 审查 P2)。
_MODEL_CACHE_MAX = 2


def _get_cached_model(whisperx: Any, model_name: str, device: str, language: Optional[str]) -> Any:
    """取或加载 WhisperX 主模型（LRU，容量 2，键含 language）。

    线程安全：双检锁，并发加载同一模型时只有一个会真正加载。

    ``language`` 必须参与缓存键：它会被固定进 WhisperX 管线实例
    （``load_model(language=...)`` 后 transcribe 不再自动检测），键里漏掉
    会让后提交的语言静默复用先提交语言的模型（2026-10-29 审查 P1-3，
    Web 混语言场景产出垃圾转写稿）。
    """
    compute_type = "int8" if device == "cpu" else "float16"
    key = (model_name, device, compute_type, language)
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            _MODEL_CACHE.move_to_end(key)
            return cached
        model = whisperx.load_model(
            model_name,
            device,
            compute_type=compute_type,
            language=language,
        )
        _MODEL_CACHE[key] = model
        while len(_MODEL_CACHE) > _MODEL_CACHE_MAX:
            _evicted_key, evicted_model = _MODEL_CACHE.popitem(last=False)
            del evicted_model
        return model


def _get_cached_align_model(whisperx: Any, language_code: str, device: str) -> Tuple[Any, Any]:
    """取或加载 word-level 对齐模型（按 ``(language_code, device)`` 缓存）。

    返回 ``(align_model, meta)``。
    """
    key = (language_code, device)
    cached: Optional[Tuple[Any, Any]] = _ALIGN_MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    with _MODEL_CACHE_LOCK:
        # 双检锁
        cached2: Optional[Tuple[Any, Any]] = _ALIGN_MODEL_CACHE.get(key)
        if cached2 is not None:
            return cached2
        align_model, meta = whisperx.load_align_model(
            language_code=language_code,
            device=device,
        )
        _ALIGN_MODEL_CACHE[key] = (align_model, meta)
        return align_model, meta


def _get_cached_diarize_pipeline(whisperx: Any, token: str, device: str) -> Any:
    """取或加载说话人分离管线（按 ``(token, device)`` 缓存, 双检锁）。

    pyannote 管线构建（权重加载 + HF 授权）为秒级开销, 此前每次
    ``transcribe()`` 都重建一个 — 连转 N 个音频复用同一实例。
    仅成功构建的管线进缓存; 构建失败照旧抛给调用方的 except,
    失败路径行为不变。
    """
    key = (token, device)
    cached = _DIARIZE_CACHE.get(key)
    if cached is not None:
        return cached
    with _MODEL_CACHE_LOCK:
        # 双检锁
        cached2 = _DIARIZE_CACHE.get(key)
        if cached2 is not None:
            return cached2
        pipeline = whisperx.DiarizationPipeline(token=token, device=device)
        _DIARIZE_CACHE[key] = pipeline
        return pipeline


def clear_model_cache() -> None:
    """清空模型缓存（含对齐模型与分离管线），释放显存。测试与显存紧张时用。"""
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()
        _ALIGN_MODEL_CACHE.clear()
        _DIARIZE_CACHE.clear()
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
            raise RuntimeError(t("WX_NOT_INSTALLED")) from e

        language = kwargs.get("language")

        if progress:
            print(t("WX_LOADING_MODEL"))

        # 第 1 步：转录 + VAD（模型走模块级缓存，连转 N 个音频只加载一次）
        model = _get_cached_model(whisperx, self.model_name, self.device, language)

        audio = whisperx.load_audio(str(audio_path))
        # 中文默认注入简体引导 prompt（跨平台有效的「源头」防线）。
        # whisperx 未注入时输出**繁体**（2026-10-23 同源对比实测）。
        transcribe_kwargs: dict[str, Any] = {"batch_size": 16}
        effective_prompt = simplified_initial_prompt(prompt, language)
        if effective_prompt:
            transcribe_kwargs["initial_prompt"] = effective_prompt
        result = model.transcribe(audio, **transcribe_kwargs)

        # 第 2 步：Word-level 对齐（对齐模型同样走缓存）
        if result["segments"]:
            if progress:
                print(t("WX_ALIGNING"))

            align_model, meta = _get_cached_align_model(
                whisperx,
                result["language"],
                self.device,
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
                print(t("WX_DIARIZING"))

            try:
                diarize_model = _get_cached_diarize_pipeline(whisperx, self.hf_token, self.device)
                diarize_segments = diarize_model(str(audio_path))
                result = whisperx.assign_word_speakers(diarize_segments, result)
                speaker_diarization = True
            except Exception as e:
                print(t("WX_DIARIZE_FAILED", e=e))

        return {
            "text": " ".join([seg.get("text", "") for seg in result.get("segments", [])]).strip(),
            "segments": result.get("segments", []),
            "language": result.get("language"),
            "model": self.model_name,
            "speaker_diarization": speaker_diarization,
        }
