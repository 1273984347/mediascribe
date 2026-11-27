"""
WhisperX transcription with alignment and optional speaker diarization.

The module-level ASR cache is keyed by model, device, compute type, language,
and a SHA-256 prompt fingerprint so different prompts never share decode state.
Alignment and diarization pipelines are cached separately. Use
``clear_model_cache()`` to release all resident models.
"""

from __future__ import annotations

import gc
import hashlib
import threading
from collections import OrderedDict
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    # P2-4: torch 是可选依赖(whisperx extra 会带上) —— 守卫导入,
    # 理由同 whisper.py(懒导入在 sys.modules 操纵下触发二次执行)
    import torch
except ImportError:  # pragma: no cover - bare orchestration install
    torch = None  # type: ignore[assignment]

from ..checkpoint import CheckpointSession, run_windowed_transcription
from ..gpu_budget import (
    ensure_resident_capacity,
    estimate_model_mb,
    register_family_clear,
    register_resident,
    unregister_family,
    unregister_resident,
)
from ..i18n import t
from ..model_weights import (
    ModelDownloadError,
    ModelLoadError,
    ModelNotReadyError,
    WeightStatus,
    check_weights,
    missing_hint,
)
from ..zh_normalize import simplified_initial_prompt
from .base import Transcriber

# ---------------------------------------------------------------------------
# 模型缓存 — 主模型与对齐模型分别缓存，避免每次转录都重新加载
# ---------------------------------------------------------------------------
_MODEL_CACHE: "OrderedDict[Tuple[str, str, str, Optional[str], str], Any]" = OrderedDict()
_ALIGN_MODEL_CACHE: Dict[Tuple[str, str], Any] = {}
_DIARIZE_CACHE: Dict[Tuple[str, str], Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()
# 与 faster_whisper 的 _MODEL_CACHE_MAX 同值: 多档位轮换时自动淘汰最久
# 未用的实例交还 GC, 否则长驻 Web 进程换一档就涨一档显存(8GB 卡第三档
# 即 OOM 风险, 2026-10-29 审查 P2)。
_MODEL_CACHE_MAX = 2


def _get_cached_model(
    whisperx: Any,
    model_name: str,
    device: str,
    language: Optional[str],
    initial_prompt: Optional[str] = None,
) -> Any:
    """Return a prompt-aware ASR model from the thread-safe two-entry LRU cache.

    ``language`` and ``initial_prompt`` are fixed into the WhisperX pipeline, so
    both participate in cache identity. Prompts are hashed rather than retained
    in the key.
    """
    compute_type = "int8" if device == "cpu" else "float16"
    prompt_key = hashlib.sha256((initial_prompt or "").encode("utf-8")).hexdigest()
    key = (model_name, device, compute_type, language, prompt_key)
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            _MODEL_CACHE.move_to_end(key)
            return cached
        # P1-4(2026-11-20 评审): 装新模型前走跨引擎驻留预算闸 —— 超预算
        # 先清**其它**引擎家族缓存;本族(含无上限的 align/diarize)由
        # clear_model_cache 整体收敛。
        register_family_clear("whisperx", clear_model_cache)
        estimate_mb = estimate_model_mb("whisperx", model_name, device)
        ensure_resident_capacity("whisperx", estimate_mb)
        try:
            model = whisperx.load_model(
                model_name,
                device,
                compute_type=compute_type,
                language=language,
                asr_options={"initial_prompt": initial_prompt},
            )
        except Exception as exc:
            message = t(
                "WX_WEIGHTS_MISSING",
                model=model_name,
                exc=exc,
                hint=missing_hint("whisperx", model_name),
            )
            if check_weights("whisperx", model_name).ok:
                raise ModelLoadError(
                    t(
                        "MODEL_LOAD_FAILED",
                        engine="whisperx",
                        model=model_name,
                        exc=exc,
                    )
                ) from exc
            raise ModelDownloadError(message) from exc
        register_resident("whisperx", ("main", key), estimate_mb)
        _MODEL_CACHE[key] = model
        while len(_MODEL_CACHE) > _MODEL_CACHE_MAX:
            _evicted_key, evicted_model = _MODEL_CACHE.popitem(last=False)
            del evicted_model
            unregister_resident("whisperx", ("main", _evicted_key))
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
        return model


def _missing_auxiliary_weight(model: str, hint_key: str) -> ModelNotReadyError:
    return ModelNotReadyError(
        WeightStatus(
            engine="whisperx",
            model=model,
            ok=False,
            location="WhisperX auxiliary model cache",
            hint=t(hint_key),
        )
    )


def _get_cached_align_model(
    whisperx: Any,
    language_code: str,
    device: str,
    *,
    allow_model_download: bool = False,
) -> Tuple[Any, Any]:
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
        if not allow_model_download:
            raise _missing_auxiliary_weight(f"align-{language_code}", "WEIGHTS_HINT_WHISPERX_ALIGN")
        align_model, meta = whisperx.load_align_model(
            language_code=language_code,
            device=device,
        )
        # P1-4: align 模型入跨引擎驻留账(此前该缓存无上限、不入账)
        register_resident(
            "whisperx", ("align", key), estimate_model_mb("whisperx", "small", device)
        )
        _ALIGN_MODEL_CACHE[key] = (align_model, meta)
        return align_model, meta


def _get_cached_diarize_pipeline(
    whisperx: Any,
    token: str,
    device: str,
    *,
    allow_model_download: bool = False,
) -> Any:
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
        if not allow_model_download:
            raise _missing_auxiliary_weight("diarization", "WEIGHTS_HINT_WHISPERX_DIARIZE")
        pipeline = whisperx.DiarizationPipeline(token=token, device=device)
        # P1-4: diarize 管线入跨引擎驻留账(此前该缓存无上限、不入账)
        register_resident(
            "whisperx", ("diarize", key), estimate_model_mb("whisperx", "medium", device)
        )
        _DIARIZE_CACHE[key] = pipeline
        return pipeline


def clear_model_cache() -> None:
    """清空模型缓存（含对齐模型与分离管线），释放显存。测试与显存紧张时用。"""
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()
        _ALIGN_MODEL_CACHE.clear()
        _DIARIZE_CACHE.clear()
    unregister_family("whisperx")
    gc.collect()
    if torch is not None and torch.cuda.is_available():
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
        allow_model_download: bool = False,
    ):
        self.model_name = model
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.hf_token = hf_token
        self.diarization = diarization
        self.allow_model_download = allow_model_download
        self._model: Any = None

    def prepare(self, **kwargs) -> Any:
        import whisperx

        return _get_cached_model(
            whisperx,
            self.model_name,
            self.device,
            kwargs.get("language"),
            kwargs.get("initial_prompt"),
        )

    def effective_decode_params(
        self, *, language: str | None, initial_prompt: str | None
    ) -> dict[str, Any]:
        """Return runtime and decode inputs that identify a resumable run."""
        try:
            package_version = metadata.version("whisperx")
        except metadata.PackageNotFoundError:
            package_version = "unknown"
        capability = self.checkpoint_capability()
        if capability is None or capability.mode != "window":
            raise ValueError("whisperx requires a window checkpoint capability")
        return {
            "engine": self.name,
            "package_version": package_version,
            "model": self.model_name,
            "device": self.device,
            "compute_type": "int8" if self.device == "cpu" else "float16",
            "language": language,
            "initial_prompt": simplified_initial_prompt(initial_prompt, language),
            "batch_size": 16,
            "vad_method": "pyannote",
            "vad_options": {
                "chunk_size": 30,
                "vad_onset": 0.500,
                "vad_offset": 0.363,
            },
            "diarization": bool(self.diarization and self.hf_token),
            "window_seconds": capability.window_seconds,
            "overlap_seconds": capability.overlap_seconds,
        }

    def transcribe(
        self,
        audio_path: Path,
        *,
        prompt: Optional[str] = None,
        progress: Optional[Any] = None,
        checkpoint_session: Optional[CheckpointSession] = None,
        **kwargs,
    ) -> dict[str, Any]:
        try:
            import whisperx
        except ImportError as e:
            raise RuntimeError(t("WX_NOT_INSTALLED")) from e

        language = kwargs.get("language")
        effective_prompt = simplified_initial_prompt(prompt, language)

        if progress:
            print(t("WX_LOADING_MODEL"))

        # WhisperX 3.8.6 accepts initial_prompt through load_model(asr_options=...),
        # not through FasterWhisperPipeline.transcribe().
        model = _get_cached_model(
            whisperx,
            self.model_name,
            self.device,
            language,
            effective_prompt,
        )

        if checkpoint_session is not None:
            if checkpoint_session.capability.mode != "window":
                raise ValueError("whisperx requires a window checkpoint session")

            detected_language = (
                checkpoint_session.cursor.detected_language if checkpoint_session.cursor else None
            )

            def transcribe_window(window_path: Path, _window_duration: float) -> dict[str, Any]:
                nonlocal detected_language
                audio = whisperx.load_audio(str(window_path))
                result: dict[str, Any] = model.transcribe(
                    audio,
                    batch_size=16,
                    language=detected_language or language,
                )
                asr_language = result["language"]
                detected_language = detected_language or asr_language
                if result["segments"]:
                    if progress:
                        print(t("WX_ALIGNING"))

                    align_model, meta = _get_cached_align_model(
                        whisperx,
                        asr_language,
                        self.device,
                        allow_model_download=self.allow_model_download,
                    )
                    result = whisperx.align(
                        result["segments"], align_model, meta, audio, self.device
                    )
                    # WhisperX align() drops language; retain the ASR identity for
                    # checkpoint commits and the final transcription result.
                    result["language"] = asr_language
                return result

            window_result = run_windowed_transcription(
                audio_path,
                capability=checkpoint_session.capability,
                session=checkpoint_session,
                transcribe_window=transcribe_window,
                cancel_event=kwargs.get("cancel_event"),
            )
            result = {
                "segments": window_result["segments"],
                "language": window_result["language"],
                "word_segments": [
                    word
                    for segment in window_result["segments"]
                    for word in segment.get("words", [])
                ],
            }
        else:
            audio = whisperx.load_audio(str(audio_path))
            result = model.transcribe(audio, batch_size=16)

            # Word-level alignment uses its own language/device cache.
            if result["segments"]:
                if progress:
                    print(t("WX_ALIGNING"))

                align_model, meta = _get_cached_align_model(
                    whisperx,
                    result["language"],
                    self.device,
                    allow_model_download=self.allow_model_download,
                )

                result = whisperx.align(
                    result["segments"],
                    align_model,
                    meta,
                    audio,
                    self.device,
                )

        # Diarization always runs once over the complete result and original audio.
        speaker_diarization = False
        if self.diarization and self.hf_token:
            if progress:
                print(t("WX_DIARIZING"))

            try:
                diarize_model = _get_cached_diarize_pipeline(
                    whisperx,
                    self.hf_token,
                    self.device,
                    allow_model_download=self.allow_model_download,
                )
                diarize_segments = diarize_model(str(audio_path))
                result = whisperx.assign_word_speakers(diarize_segments, result)
                speaker_diarization = True
            except ModelNotReadyError:
                raise
            except Exception as e:
                print(t("WX_DIARIZE_FAILED", e=e))

        return {
            "text": " ".join([seg.get("text", "") for seg in result.get("segments", [])]).strip(),
            "segments": result.get("segments", []),
            "language": result.get("language"),
            "model": self.model_name,
            "speaker_diarization": speaker_diarization,
        }
