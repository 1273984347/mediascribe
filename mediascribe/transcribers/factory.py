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
from .whisper_cpp import WhisperCppTranscriber
from .whisper_cpp import is_available as _whisper_cpp_available
from .whisperx import WhisperXTranscriber

# Default fallback chain. Tests / users can override.
DEFAULT_FALLBACK_CHAIN = ("whisperx", "faster-whisper", "whisper", "whisper-cpp")

#: 可用内存低于该值时**优先**用 whisper.cpp。
#: 依据（2026-10-23 本机实测，CPU 档峰值内存）：whisper.cpp tiny **291 MiB**
#: vs faster-whisper small 952 MiB vs faster-whisper large-v3 3613 MiB。
#: ⇒ 内存紧张时用 whisper.cpp 才不会把机器拖垮。
_MEMORY_PRESSURE_MB = 4096


# Engines that need an explicit import. When the optional dependency
# is missing, we skip them in the fallback chain.
_OPTIONAL_ENGINES = {
    "whisperx": "whisperx",
    "faster-whisper": "faster_whisper",
}


def _memory_pressure() -> bool:
    """当前可用内存是否低于压力线（探测失败时返回 False，不阻断）。"""
    try:
        import psutil  # type: ignore

        available_mb = float(psutil.virtual_memory().available) / 1024 / 1024
        return bool(available_mb < _MEMORY_PRESSURE_MB)
    except Exception:
        return False


def _chain_for_device(device: Optional[str]) -> tuple:
    """按设备与内存水位调整回退链。

    * CPU / auto 且内存紧张 ⇒ whisper.cpp 提到 faster-whisper 之前
      （它最省内存，见 :data:`_MEMORY_PRESSURE_MB` 的实测依据）
    * 其余情况 ⇒ 默认链，whisper.cpp 垫底作最后回退
    """
    chain = list(DEFAULT_FALLBACK_CHAIN)
    if device in (None, "auto", "cpu") and _memory_pressure():
        if "whisper-cpp" in chain:
            chain.remove("whisper-cpp")
            chain.insert(chain.index("faster-whisper"), "whisper-cpp")
    return tuple(chain)


def _engine_available(name: str) -> bool:
    """Return True iff the optional package for ``name`` is importable."""
    import importlib

    if name == "whisper-cpp":
        # whisper.cpp 是外部二进制（不是 Python 包）⇒ 检查二进制是否存在
        return _whisper_cpp_available()

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
    if name == "whisper-cpp":
        return WhisperCppTranscriber(model=model, device=device)
    raise ValueError(
        f"Unknown transcriber {name!r}. Allowed: whisper, faster-whisper, whisperx, whisper-cpp."
    )


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
    if chain is DEFAULT_FALLBACK_CHAIN:
        # 用默认链时按设备与内存水位调整（内存紧张时 whisper.cpp 优先）
        chain = _chain_for_device(device)
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
