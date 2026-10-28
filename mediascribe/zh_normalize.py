"""
中文繁简归一化 —— 让不同引擎的转写稿字形一致。

问题
----
Whisper 的多语言训练语料里中文**简繁混杂**，不同推理后端的默认输出不同。
2026-10-23 的同源对比实测（同一段音频、同一模型档）：

===================  ==================  ====================
引擎                 输出字形            字级 CER（未归一化 / 归一化）
===================  ==================  ====================
faster-whisper       简体                3.21% / 3.21%
openai-whisper       **繁体**            33.61% / **0.41%**
whisper.cpp          **繁体**            81.56% / **1.19%**
===================  ==================  ====================

差异**不是识别能力差异，是字形差异**。跨引擎比较必须先归一化，否则会得出
「openai-whisper 差 10 倍」「whisper.cpp 中文几乎不可用」两个完全错误的结论。

同理，对产品而言：**换引擎会直接改变用户看到的转写稿字形**。

双层防御
--------
1. **源头（跨平台有效，主力）**：各引擎在中文场景注入简体 ``initial_prompt``，
   让模型**直接生成简体** —— 比事后转换自然得多（不会出现转换带来的用词差异）。
2. **兜底（仅 Windows 有效）**：输出层做繁→简转换，用 Windows 内置
   ``LCMapStringW(LCMAP_SIMPLIFIED_CHINESE)``，**零第三方依赖**
   （本项目要求不引入新依赖：离线产品 + PyPI 常不可达，装不了 opencc/zhconv）。

非 Windows 平台（macOS / Linux）没有可用的零依赖转换器，兜底层退化为
**原样返回**；此时字形一致性完全依赖第 1 层。:func:`conversion_available`
可查询当前平台是否具备兜底能力，``mediascribe doctor`` 会显示该状态。
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: 注入给模型的简体引导语。Whisper 会把它当作「前文语境」，从而倾向输出简体。
SIMPLIFIED_ZH_PROMPT = "以下是简体中文的句子。"

#: 判断「这是中文转写」的粗粒度判定：语言码以 zh 开头，或文本含中文且无其他语言码。
_ZH_LANGUAGES = ("zh", "yue", "cmn")

_converter: Optional[Callable[[str], str]] = None
_checked = False
_warned_unavailable = False


def _windows_to_simplified(text: str) -> str:
    """用 Windows 的 ``LCMapStringW`` 把繁体转成简体。"""
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
    # ctypes 的 unicode buffer 切片在类型上是 list[str] —— join 成 str
    return "".join(buf[:got])


def _resolve_converter() -> Optional[Callable[[str], str]]:
    """探测当前平台是否具备零依赖的繁→简转换能力。"""
    global _converter, _checked
    if _checked:
        return _converter
    _checked = True
    if os.name == "nt":
        try:
            # 自检一次，确认这台机器的 LCMapStringW 行为正常
            probe = _windows_to_simplified("觀眾")
            if probe and probe != "觀眾":
                _converter = _windows_to_simplified
        except Exception as exc:  # pragma: no cover - 平台相关
            logger.debug("Windows 繁简转换探测失败: %s", exc)
            _converter = None
    return _converter


def conversion_available() -> bool:
    """当前平台是否支持零依赖的繁→简转换（兜底层是否生效）。"""
    return _resolve_converter() is not None


def to_simplified(text: str) -> str:
    """繁体→简体。平台不支持时**原样返回**（不改变既有文本）。"""
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
    except Exception:  # pragma: no cover - 转换失败不应破坏转写结果
        return text


def is_zh_language(language: Optional[str]) -> bool:
    """判断语言码是否属于「需要简繁统一」的中文族。"""
    if not language:
        return False
    lang = language.strip().lower()
    return any(lang.startswith(prefix) for prefix in _ZH_LANGUAGES)


def is_yue_language(language: Optional[str]) -> bool:
    """判断语言码是否为粤语（``yue``）。

    粤语在 tiny/base/small/medium 档位上识别质量很差（官方对 yue 的
    训练数据有限，小模型常按普通话音素解码），auto 模型推荐与弱模型
    警告都以本判定为准。大小写不敏感，``yue-HK`` 这类带区域后缀的
    写法同样命中。
    """
    if not language:
        return False
    return language.strip().lower().startswith("yue")


def simplified_initial_prompt(prompt: Optional[str], language: Optional[str]) -> Optional[str]:
    """中文场景下补一个简体引导 prompt。

    调用方**显式传了** prompt 时不覆盖 —— 用户自定义的 prompt 往往包含
    专有名词/术语表，优先级高于通用引导语。

    Parameters
    ----------
    prompt
        调用方传入的 initial_prompt（可能为 ``None``）。
    language
        语言码（如 ``"zh"``）。非中文语言返回原值。
    """
    if prompt is not None:
        return prompt
    if is_zh_language(language):
        return SIMPLIFIED_ZH_PROMPT
    return None


def normalize_text(text: str, language: Optional[str] = None, enabled: bool = True) -> str:
    """输出层兜底归一化。

    Parameters
    ----------
    text
        原始文本。
    language
        语言码。``None`` 时按「含中文即处理」的宽松策略。
    enabled
        总开关（``Settings.zh_simplify``）。关闭时原样返回。
    """
    if not enabled or not text:
        return text
    if language is not None and not is_zh_language(language):
        return text
    return to_simplified(text)
