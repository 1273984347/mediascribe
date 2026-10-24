"""
OpenAI Whisper 转录器 - 参考 bili2text

torch 2.6+ 兼容补丁（仅作用于模型加载窗口，不污染全局）：
1. torch.load 默认 weights_only=True，旧版 whisper checkpoint 无法反序列化
2. 模型 checkpoint 中存储标签为 "auto"，default_restore_location 无法识别

加载模型时通过 :func:`_whisper_load_patch` 上下文管理器**临时**替换
``torch.load`` / ``default_restore_location``，finally 恢复原函数，
进程内其他库不受影响。

模块级模型缓存（与 faster_whisper / whisperx 同款复用模式）：按
``(model_name, device)`` 复用已加载的 .pt 实例 — openai-whisper 的
``load_model`` 每次都从磁盘反序列化 checkpoint（几百 MB、秒级），
同进程再建同配置 Pipeline（cross-check 对照、verify 重采样、Web 管线
缓存淘汰重建）时不再重复走磁盘。
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

import torch

from ..model_weights import missing_hint
from ..zh_normalize import simplified_initial_prompt
from .base import Transcriber


@contextlib.contextmanager
def _whisper_load_patch() -> Iterator[None]:
    """临时替换 ``torch.load`` 与 ``default_restore_location``。

    whisper 的旧版 checkpoint 依赖 torch<2.6 的加载行为，这里只在
    ``whisper.load_model`` 的调用窗口内生效，退出时 finally 恢复原函数，
    不做进程级永久替换。

    - ``weights_only``：先按新版默认 ``True`` 尝试，失败回退 ``False``
      （与旧补丁的兼容行为一致，仅限本窗口内）。
    - ``default_restore_location``：将 "auto" 标签映射到 cpu。
    """
    original_load = torch.load
    original_restore = getattr(torch.serialization, "default_restore_location", None)

    def _patched_default_restore_location(storage, location):
        if location in ("auto", "cpu"):
            return storage.cpu()
        if location.startswith("cuda"):
            try:
                return storage.cuda()
            except Exception:  # noqa: BLE001
                return storage.cpu()
        if original_restore is not None:
            return original_restore(storage, location)
        return storage.cpu()

    def _safe_torch_load(*args, **kwargs):
        # 先按 torch 2.6+ 默认 weights_only=True 尝试；旧 checkpoint
        # 反序列化失败时回退 weights_only=False 再试一次。
        kwargs.setdefault("weights_only", True)
        try:
            return original_load(*args, **kwargs)
        except Exception:  # noqa: BLE001
            if not kwargs.get("weights_only", True):
                raise
            kwargs["weights_only"] = False
            return original_load(*args, **kwargs)

    torch.load = _safe_torch_load
    if original_restore is not None:
        torch.serialization.default_restore_location = _patched_default_restore_location
    try:
        yield
    finally:
        torch.load = original_load
        if original_restore is not None:
            torch.serialization.default_restore_location = original_restore


# ---------------------------------------------------------------------------
# 模块级模型缓存 — 双检锁, 与 whisperx 的 _MODEL_CACHE 同款
# ---------------------------------------------------------------------------
_MODEL_CACHE: Dict[Tuple[str, str], Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def _get_cached_model(whisper_module: Any, model_name: str, device: str) -> Any:
    """按 ``(model_name, device)`` 取或加载 whisper 模型（双检锁）。

    torch 2.6+ 兼容补丁只在真正走磁盘加载的窗口内生效, 命中缓存
    不进入补丁窗口。
    """
    key = (model_name, device)
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    with _MODEL_CACHE_LOCK:
        cached2 = _MODEL_CACHE.get(key)
        if cached2 is not None:
            return cached2
        with _whisper_load_patch():
            try:
                model = whisper_module.load_model(model_name, device=device)
            except Exception as exc:
                # openai-whisper 的权重是单个 .pt 文件；缺失时它的报错是
                # 「RuntimeError: Model {name} not found; available models = ...」
                # —— 对用户没有可操作性。换成带预置指引的中文错误。
                raise RuntimeError(
                    f"模型 {model_name!r} 的权重未预置：{exc}\n"
                    f"{missing_hint('whisper', model_name)}"
                ) from exc
        _MODEL_CACHE[key] = model
        return model


def clear_model_cache() -> None:
    """清空模型缓存，释放显存。测试与显存紧张时用。"""
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()


class WhisperTranscriber(Transcriber):
    """OpenAI Whisper 转录器"""

    name = "whisper"

    def __init__(self, model: str = "small", device: Optional[str] = None):
        self.model_name = model
        self.device = device
        self._model: Any = None

    def transcribe(
        self,
        audio_path: Path,
        *,
        prompt: Optional[str] = None,
        progress: Optional[Any] = None,
        **kwargs,
    ) -> dict[str, Any]:

        model = self._ensure_model()

        if progress:
            print("转录中...")

        options: dict[str, Any] = {
            "verbose": False,
        }
        if self.device == "cpu":
            options["fp16"] = False

        language = kwargs.get("language")
        if language:
            options["language"] = language

        # 中文默认注入简体引导 prompt（跨平台有效的「源头」防线）。
        # openai-whisper 不注入时输出**繁体**（2026-10-23 同源对比实测：
        # 同一段音频字级 CER 繁体 33.61% vs 简体 0.41%）。
        # 调用方显式传 prompt 时不覆盖（用户自定义 prompt 往往带术语表）。
        effective_prompt = simplified_initial_prompt(prompt, language)
        if effective_prompt:
            options["initial_prompt"] = effective_prompt

        result = model.transcribe(str(audio_path), **options)

        return {
            "text": (result.get("text") or "").strip(),
            "segments": result.get("segments", []),
            "language": result.get("language"),
            "model": self.model_name,
        }

    def _ensure_model(self):
        if self._model is not None:
            return self._model

        import whisper

        if self.device is None or self.device == "auto":
            import torch as _torch

            self.device = "cuda" if _torch.cuda.is_available() else "cpu"

        print(f"加载 Whisper 模型: {self.model_name} (设备: {self.device})")

        # torch 2.6+ 兼容补丁仅在加载窗口内临时生效; 实例走模块级缓存复用
        self._model = _get_cached_model(whisper, self.model_name, self.device)
        return self._model
