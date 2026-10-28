"""
Chinese script normalization (traditional → simplified) — keeps transcript
glyphs consistent across engines.

Problem
-------
Whisper's multilingual training corpus mixes simplified and traditional
Chinese, and different inference backends default to different scripts.
Same-source comparison measured on 2026-10-23 (same audio, same model tier):

===================  ==================  ====================
Engine               Output script       CER (raw / normalized)
===================  ==================  ====================
faster-whisper       simplified          3.21% / 3.21%
openai-whisper       **traditional**     33.61% / **0.41%**
whisper.cpp          **traditional**     81.56% / **1.19%**
===================  ==================  ====================

The gap is **a glyph difference, not a recognition-quality difference**.
Cross-engine comparison must normalize first, otherwise you draw two
entirely wrong conclusions ("openai-whisper is 10x worse", "whisper.cpp
is unusable for Chinese").

Likewise for the product: **switching engines changes the script users
see in their transcripts.**

Two-layer defense
-----------------
1. **At the source (cross-platform, the primary layer)**: engines inject a
   simplified-Chinese ``initial_prompt`` for Chinese tasks so the model
   **generates simplified directly** — far more natural than after-the-fact
   conversion (no conversion-induced wording drift).
2. **Fallback (Windows only)**: the output layer converts traditional →
   simplified via the built-in ``LCMapStringW(LCMAP_SIMPLIFIED_CHINESE)``,
   with **zero third-party dependencies** (this project forbids new
   dependencies: offline product + PyPI often unreachable, so opencc /
   zhconv are not installable).

Non-Windows platforms (macOS / Linux) have no zero-dependency converter,
so the fallback layer degrades to **returning text unchanged**; script
consistency then rests entirely on layer 1. Use
:func:`conversion_available` to query whether the fallback works on the
current platform; ``mediascribe doctor`` reports this state.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: Simplified-Chinese prompt injected into the model. Whisper treats it as
#: preceding context and tends to continue in simplified script.
SIMPLIFIED_ZH_PROMPT = "以下是简体中文的句子。"

#: Coarse "this is a Chinese transcription" test: language code starts with
#: one of these prefixes.
_ZH_LANGUAGES = ("zh", "yue", "cmn")

_converter: Optional[Callable[[str], str]] = None
_checked = False
_warned_unavailable = False


def _windows_to_simplified(text: str) -> str:
    """Convert traditional to simplified via Windows ``LCMapStringW``."""
    import ctypes

    locale_zh_cn = 0x0804
    lcmap_simplified_chinese = 0x02000000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    need = kernel32.LCMapStringW(locale_zh_cn, lcmap_simplified_chinese, text, len(text), None, 0)
    if need <= 0:
        return text
    buf = ctypes.create_unicode_buffer(need + 1)
    got = kernel32.LCMapStringW(
        locale_zh_cn, lcmap_simplified_chinese, text, len(text), buf, need + 1
    )
    if got <= 0:
        return text
    # ctypes unicode buffer slices are list[str] — join into str
    return "".join(buf[:got])


def _resolve_converter() -> Optional[Callable[[str], str]]:
    """Detect whether this platform has a zero-dependency traditional→simplified converter."""
    global _converter, _checked
    if _checked:
        return _converter
    _checked = True
    if os.name == "nt":
        try:
            # Self-probe once to confirm LCMapStringW behaves on this machine
            probe = _windows_to_simplified("觀眾")
            if probe and probe != "觀眾":
                _converter = _windows_to_simplified
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.debug("Windows script conversion probe failed: %s", exc)
            _converter = None
    return _converter


def conversion_available() -> bool:
    """Whether this platform supports the zero-dependency traditional→simplified fallback."""
    return _resolve_converter() is not None


def to_simplified(text: str) -> str:
    """Traditional → simplified. Returns text **unchanged** on unsupported platforms."""
    if not text:
        return text
    converter = _resolve_converter()
    if converter is None:
        global _warned_unavailable
        if not _warned_unavailable:
            _warned_unavailable = True
            logger.info(
                "当前平台无零依赖繁简转换能力（%s），繁简一致性仅依赖引擎侧的简体 prompt 注入",
                os.name,
            )
        return text
    try:
        return converter(text)
    except Exception:  # pragma: no cover - conversion failure must not corrupt transcripts
        return text


def is_zh_language(language: Optional[str]) -> bool:
    """Whether the language code belongs to the Chinese family needing script normalization."""
    if not language:
        return False
    lang = language.strip().lower()
    return any(lang.startswith(prefix) for prefix in _ZH_LANGUAGES)


def is_yue_language(language: Optional[str]) -> bool:
    """Whether the language code is Cantonese (``yue``).

    Cantonese recognition quality is poor on tiny/base/small/medium (the
    official yue training data is limited and small models often decode with
    Mandarin phonemes); both the ``auto`` model recommendation and the weak
    model warning key off this predicate. Case-insensitive; regional
    suffixes like ``yue-HK`` match too.
    """
    if not language:
        return False
    return language.strip().lower().startswith("yue")


def simplified_initial_prompt(prompt: Optional[str], language: Optional[str]) -> Optional[str]:
    """Fill in a simplified-Chinese initial prompt for Chinese tasks.

    Never overrides a caller-supplied prompt — user prompts usually carry
    proper nouns / glossaries and outrank the generic hint.

    Parameters
    ----------
    prompt
        Caller-provided initial_prompt (may be ``None``).
    language
        Language code (e.g. ``"zh"``). Non-Chinese languages return the
        original value.
    """
    if prompt is not None:
        return prompt
    if is_zh_language(language):
        return SIMPLIFIED_ZH_PROMPT
    return None


def normalize_text(text: str, language: Optional[str] = None, enabled: bool = True) -> str:
    """Output-layer fallback normalization.

    Parameters
    ----------
    text
        Raw text.
    language
        Language code. ``None`` applies the loose "contains Chinese →
        process" policy.
    enabled
        Master switch (``Settings.zh_simplify``). Returns text unchanged
        when off.
    """
    if not enabled or not text:
        return text
    if language is not None and not is_zh_language(language):
        return text
    return to_simplified(text)
