"""
转录器基类 - 参考 bili2text
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional


class Transcriber(ABC):
    """转录器基类"""

    name: str = "base"

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        *,
        prompt: Optional[str] = None,
        progress: Optional[Any] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """转录音频"""
        pass
