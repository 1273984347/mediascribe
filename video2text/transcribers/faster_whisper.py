"""
faster-whisper 转录器（whisper.cpp 的 Python 版本）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .base import Transcriber


class FasterWhisperTranscriber(Transcriber):
    """faster-whisper 转录器"""
    name = "faster-whisper"

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
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError("faster-whisper 未安装，请运行: pip install faster-whisper") from e

        if self._model is None:
            print(f"加载 faster-whisper 模型: {self.model_name}")
            self._model = WhisperModel(
                self.model_name,
                device=self.device or "cpu",
                compute_type="int8",
            )

        if progress:
            print("转录中...")

        language = kwargs.get("language")
        segments, info = self._model.transcribe(str(audio_path), language=language)

        seg_list = []
        text_parts = []
        for seg in segments:
            seg_dict = {
                "text": seg.text,
                "start": seg.start,
                "end": seg.end,
            }
            seg_list.append(seg_dict)
            text_parts.append(seg.text)

        return {
            "text": " ".join(text_parts).strip(),
            "segments": seg_list,
            "language": info.language,
            "model": self.model_name,
        }
