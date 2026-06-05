"""
OpenAI Whisper 转录器 - 参考 bili2text
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

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

        import whisper

        if self.device is None:
            self.device = "cuda" if (hasattr(whisper, "torch") and whisper.torch.cuda.is_available()) else "cpu"

        print(f"加载 Whisper 模型: {self.model_name} (设备: {self.device})")
        self._model = whisper.load_model(self.model_name, device=self.device)
        return self._model
