"""
转录器基类 - 参考 bili2text
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Optional

from ..checkpoint import CheckpointCapability, capability_for

SegmentCallback = Callable[[dict[str, Any]], None]


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
        """Transcribe audio."""
        pass

    def prepare(self, **kwargs) -> Any:
        """Load runtime resources before transcription starts."""
        return None

    def checkpoint_capability(self) -> CheckpointCapability | None:
        """Return the checkpoint mode supported by this transcriber."""
        return capability_for(self.name)

    def effective_decode_params(
        self, *, language: str | None, initial_prompt: str | None
    ) -> dict[str, Any]:
        """Return the decode inputs that identify a resumable transcription."""
        return {
            "engine": self.name,
            "model": getattr(self, "model_name", None),
            "device": getattr(self, "device", None),
            "language": language,
            "initial_prompt": initial_prompt,
        }
