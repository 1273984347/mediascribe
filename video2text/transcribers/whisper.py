"""
OpenAI Whisper 转录器 - 参考 bili2text

torch 2.6+ 兼容补丁（模块级自动生效）：
1. torch.load 默认 weights_only=True，旧版模型无法反序列化
2. 模型 checkpoint 中存储标签为 "auto"，default_restore_location 无法识别

导入此模块时自动修补，使 whisper.load_model() 透明可用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import torch

# ── torch 2.6+ 兼容补丁 ──
_original_default_restore_location = getattr(
    torch.serialization, "default_restore_location", None
)

if _original_default_restore_location is not None:
    # 修补 default_restore_location：将 "auto" 标签映射到 cpu
    def _patched_default_restore_location(storage, location):
        if location in ("auto", "cpu"):
            return storage.cpu()
        if location.startswith("cuda"):
            try:
                return storage.cuda()
            except Exception:
                return storage.cpu()
        return _original_default_restore_location(storage, location)

    torch.serialization.default_restore_location = _patched_default_restore_location


def _safe_torch_load(*args, **kwargs):
    """包装 torch.load，默认禁用 weights_only"""
    kwargs.setdefault("weights_only", False)
    return torch._original_load(*args, **kwargs)


# 保存原始引用并替换
if not hasattr(torch, "_original_load"):
    torch._original_load = torch.load
    torch.load = _safe_torch_load
# ───────────────────────────

from .base import Transcriber


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

        import os
        import whisper

        if self.device is None or self.device == "auto":
            import torch as _torch
            self.device = "cuda" if _torch.cuda.is_available() else "cpu"

        print(f"加载 Whisper 模型: {self.model_name} (设备: {self.device})")

        # 兼容补丁已自动生效，直接调用 whisper.load_model
        self._model = whisper.load_model(self.model_name, device=self.device)
        return self._model
