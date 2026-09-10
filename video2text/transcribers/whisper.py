"""
OpenAI Whisper 转录器 - 参考 bili2text

torch 2.6+ 兼容补丁（仅作用于模型加载窗口，不污染全局）：
1. torch.load 默认 weights_only=True，旧版 whisper checkpoint 无法反序列化
2. 模型 checkpoint 中存储标签为 "auto"，default_restore_location 无法识别

加载模型时通过 :func:`_whisper_load_patch` 上下文管理器**临时**替换
``torch.load`` / ``default_restore_location``，finally 恢复原函数，
进程内其他库不受影响。
"""
from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Iterator, Optional

import torch

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
    original_restore = getattr(
        torch.serialization, "default_restore_location", None
    )

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
        torch.serialization.default_restore_location = (
            _patched_default_restore_location
        )
    try:
        yield
    finally:
        torch.load = original_load
        if original_restore is not None:
            torch.serialization.default_restore_location = original_restore


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

        options = {
            "verbose": False,
        }
        if prompt:
            options["initial_prompt"] = prompt
        if self.device == "cpu":
            options["fp16"] = False

        language = kwargs.get("language")
        if language:
            options["language"] = language

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

        # torch 2.6+ 兼容补丁仅在加载窗口内临时生效
        with _whisper_load_patch():
            self._model = whisper.load_model(self.model_name, device=self.device)
        return self._model
