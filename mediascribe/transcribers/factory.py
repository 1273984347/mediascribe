"""
Transcriber factory with automatic fallback.

Order (highest accuracy first):
    whisperx  →  faster-whisper  →  whisper

Each engine has its own strengths:
- ``whisperx``: best accuracy for Chinese / multilingual + speaker
  diarization. Requires ``pip install whisperx``.
- ``faster-whisper``: pure-Python, no PyTorch. Fast CPU inference.
- ``whisper``: the original OpenAI Whisper. Always available as a
  baseline.
"""

from __future__ import annotations

from typing import Optional

from .base import Transcriber
from .faster_whisper import FasterWhisperTranscriber
from .whisper import WhisperTranscriber
from .whisperx import WhisperXTranscriber

# Default fallback chain. Tests / users can override.
DEFAULT_FALLBACK_CHAIN = ("whisperx", "faster-whisper", "whisper")


# Engines that need an explicit import. When the optional dependency
# is missing, we skip them in the fallback chain.
_OPTIONAL_ENGINES = {
    "whisperx": "whisperx",
    "faster-whisper": "faster_whisper",
}


def _engine_available(name: str) -> bool:
    """Return True iff the optional package for ``name`` is importable."""
    import importlib

    module = _OPTIONAL_ENGINES.get(name)
    if module is None:
        # whisper is a hard dep; always available
        return True
    try:
        importlib.import_module(module)
        return True
    except ImportError:
        return False


def get_transcriber(
    name: str = "whisperx",
    *,
    model: str = "small",
    device: Optional[str] = None,
    hf_token: Optional[str] = None,
    diarization: bool = False,
) -> Transcriber:
    """Return a single transcriber instance by name.

    The caller is responsible for picking the right one. If unsure,
    use ``get_transcriber_with_fallback()`` instead.
    """
    if name == "whisperx":
        return WhisperXTranscriber(
            model=model, device=device, hf_token=hf_token, diarization=diarization
        )
    if name == "faster-whisper":
        return FasterWhisperTranscriber(model=model, device=device)
    if name == "whisper":
        return WhisperTranscriber(model=model)
    raise ValueError(f"Unknown transcriber {name!r}. Allowed: whisper, faster-whisper, whisperx.")


def get_transcriber_with_fallback(
    preferred: str = "whisperx",
    *,
    model: str = "small",
    device: Optional[str] = None,
    hf_token: Optional[str] = None,
    diarization: bool = False,
    chain: tuple = DEFAULT_FALLBACK_CHAIN,
) -> Transcriber:
    """Return the first available transcriber from the fallback chain,
    starting with ``preferred``.

    Example:
        >>> t = get_transcriber_with_fallback("whisperx", model="small")
        >>> # if whisperx is not installed, falls back to faster-whisper
        >>> # or whisper, whichever is available first.
    """
    if preferred not in chain:
        chain = (preferred,) + chain

    for name in chain:
        if _engine_available(name):
            return get_transcriber(
                name,
                model=model,
                device=device,
                hf_token=hf_token,
                diarization=diarization,
            )

    # Last resort: whisper. If even that fails, raise clearly.
    raise RuntimeError(
        "No transcriber is available. Please install at least one of: " + ", ".join(chain)
    )


def list_available_engines() -> list:
    """Return the list of engines whose optional dependency is installed."""
    return [name for name in DEFAULT_FALLBACK_CHAIN if _engine_available(name)]
