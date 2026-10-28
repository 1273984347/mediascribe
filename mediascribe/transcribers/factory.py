"""Transcriber factory — build a transcriber by engine name.

- ``whisperx``: best accuracy for Chinese / multilingual + speaker
  diarization. Requires ``pip install whisperx``.
- ``faster-whisper``: pure-Python, no PyTorch. Fast CPU inference.
- ``whisper``: the original OpenAI Whisper. Always available as a
  baseline.
- ``whisper-cpp``: the C++/GGML engine — no Python deps at all, and the
  **lowest memory** of the four (measured peak RSS: 291 MiB for tiny vs
  952 MiB for faster-whisper small). Needs an external binary + GGML
  weights.

Engine **selection** (degrade / switch by hardware) lives in
:mod:`mediascribe.runtime_guard` and is applied by the pipeline; this
module only constructs instances. A previous memory-pressure fallback
chain here (~2026-10-23) was removed on 2026-10-29: it was dead in the
product, and its available-RAM threshold disagreed with runtime_guard's
total-RAM rule — two decision points would pick different engines on the
same machine.
"""

from __future__ import annotations

from typing import Optional

from .base import Transcriber
from .faster_whisper import FasterWhisperTranscriber
from .whisper import WhisperTranscriber
from .whisper_cpp import WhisperCppTranscriber
from .whisperx import WhisperXTranscriber


def get_transcriber(
    name: str = "whisperx",
    *,
    model: str = "small",
    device: Optional[str] = None,
    hf_token: Optional[str] = None,
    diarization: bool = False,
) -> Transcriber:
    """Return a single transcriber instance by name."""
    if name == "whisperx":
        return WhisperXTranscriber(
            model=model, device=device, hf_token=hf_token, diarization=diarization
        )
    if name == "faster-whisper":
        return FasterWhisperTranscriber(model=model, device=device)
    if name == "whisper":
        return WhisperTranscriber(model=model)
    if name == "whisper-cpp":
        return WhisperCppTranscriber(model=model, device=device)
    raise ValueError(
        f"Unknown transcriber {name!r}. Allowed: whisper, faster-whisper, whisperx, whisper-cpp."
    )
