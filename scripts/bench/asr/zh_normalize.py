"""
中文繁简归一化 —— 跨引擎中文对比的前置条件。

为什么必须有
------------
Whisper 的多语言训练数据中中文**简繁混杂**，不同推理后端的默认输出不一样：

* faster-whisper / openai-whisper（PyTorch）→ 简体
* **whisper.cpp → 繁体**（实测输出「各位觀眾大家好」「科技觀察節目」）

若不做归一化就做**字级 CER**，同一段音频会得到：

* faster-whisper（简体）：3.21%
* whisper.cpp（繁体）：**81.56%** —— 实际内容完全正确，只是字形不同

⇒ 这个差异是**度量假象**，不是引擎能力差异。任何中文字级对比都必须先归一化。

实现
----
用 Windows 内置的 ``LCMapStringW(LCMAP_SIMPLIFIED_CHINESE)`` 做繁→简转换：
* **零第三方依赖**（本机 PyPI 不可达，装不了 opencc / zhconv）
* 系统级实现，转换质量优于自制映射表
* 非 Windows 平台或调用失败时**原样返回**（不改变既有口径，只是不做归一化）
"""

from __future__ import annotations

import os
from typing import Callable

_LOCALE_ZH_CN = 0x0804
_LCMAP_SIMPLIFIED_CHINESE = 0x02000000

_converter: Callable[[str], str] | None = None
_checked = False


def _windows_to_simplified(text: str) -> str:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 1) 问需要多少个 UTF-16 码元
    need = kernel32.LCMapStringW(
        _LOCALE_ZH_CN, _LCMAP_SIMPLIFIED_CHINESE, text, len(text), None, 0
    )
    if need <= 0:
        return text
    buf = ctypes.create_unicode_buffer(need + 1)
    got = kernel32.LCMapStringW(
        _LOCALE_ZH_CN, _LCMAP_SIMPLIFIED_CHINESE, text, len(text), buf, need + 1
    )
    if got <= 0:
        return text
    return buf[:got]


def to_simplified(text: str) -> str:
    """繁体→简体。非 Windows 或调用失败时原样返回。"""
    global _converter, _checked
    if not text:
        return text
    if not _checked:
        _checked = True
        if os.name == "nt":
            try:
                # 先自检一次，确认这台机器的 LCMapStringW 行为正常
                probe = _windows_to_simplified("觀眾")
                if probe and probe != "觀眾":
                    _converter = _windows_to_simplified
            except Exception:
                _converter = None
    if _converter is None:
        return text
    try:
        return _converter(text)
    except Exception:
        return text


def normalize_cjk(text: str, language: str) -> str:
    """按语言做中文归一化。英文与其它语言原样返回。"""
    if language in ("zh", "mixed"):
        return to_simplified(text)
    return text
