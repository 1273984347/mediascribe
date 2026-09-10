"""
转录后处理 (v3.2.0b)

1. 术语校正 (ASR 常把专有名词听错)
2. Prompt 模板系统
3. 智能模型推荐

用法
----

```python
from video2text.post_process import post_process_transcript
from pathlib import Path

raw = Path("output/transcripts/article.md").read_text(encoding="utf-8")
fixed = post_process_transcript(raw)
Path("output/transcripts/article_corrected.md").write_text(fixed, encoding="utf-8")
```

术语表
------
默认术语表覆盖教育/历史/文学领域的常见 ASR 错误。
调用方可通过 ``custom_terms`` 追加,或通过
``VIDEO2TEXT_CUSTOM_TERMS`` 环境变量 (JSON dict)。

模型推荐
--------
``auto_select_model(duration_seconds)`` 按视频时长推荐
模型,平衡速度与精度。

Prompt 模板
----------
``get_prompt_template(domain)`` 返回领域专属 prompt,
喂给 ``Pipeline.transcribe(..., prompt=...)``。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

_logger = logging.getLogger(__name__)

__all__ = [
    "post_process_transcript",
    "auto_select_model",
    "get_prompt_template",
    "setup_hf_mirror",
    "DEFAULT_TERMS",
    "PROMPT_TEMPLATES",
]

# ---------------------------------------------------------------------------
# 术语校正
# ---------------------------------------------------------------------------
DEFAULT_TERMS: dict[str, str] = {
    # 教育类
    "获取病": "霍去病",
    "下水温": "下水文",
    "矛盾而分法": "矛盾二分法",
    "注价老师": "助教老师",
    "月经老师": "岳江老师",
    "某篇布局": "谋篇布局",
    "下水温章": "下水文章",
}


def post_process_transcript(
    text: str,
    custom_terms: Optional[dict[str, str]] = None,
    *,
    merge_env: bool = True,
    merge_learned: bool = True,
) -> str:
    """转录后术语校正。

    术语合并优先级 (低 → 高)::

        DEFAULT_TERMS < learned_terms < env VIDEO2TEXT_CUSTOM_TERMS < custom_terms

    Parameters
    ----------
    text
        原始转录文本(Markdown 或纯文本均可)。
    custom_terms
        额外术语字典;key 为错误词,value 为正确词。
    merge_env
        如果为 True,从 ``VIDEO2TEXT_CUSTOM_TERMS`` 环境变量读取
        JSON dict 并合并。
    merge_learned
        如果为 True,从 :func:`video2text.learn.get_learned_terms`
        加载自动学习的术语并合并。

    Returns
    -------
    替换后的文本。
    """
    terms = dict(DEFAULT_TERMS)

    # 自动学习术语 (优先级高于默认,低于用户自定义)
    if merge_learned:
        try:
            from .learn import get_learned_terms
            terms.update(get_learned_terms())
        except Exception:
            pass  # 学习模块不可用时不阻塞

    if merge_env:
        env_raw = os.environ.get("VIDEO2TEXT_CUSTOM_TERMS")
        if env_raw:
            try:
                terms.update(json.loads(env_raw))
            except json.JSONDecodeError as exc:
                # P2-13 / DRL R2 F-7 对齐: 静默忽略会让用户以为 env
                # 已生效。log warning 带异常与原文摘要,方便定位拼写
                # / 引号错误,且不阻塞主流程。
                _logger.warning(
                    "VIDEO2TEXT_CUSTOM_TERMS JSON 解析失败(%s),"
                    "已忽略该环境变量;内容摘要: %.120s",
                    exc,
                    env_raw,
                )
    if custom_terms:
        terms.update(custom_terms)

    for wrong, right in terms.items():
        text = text.replace(wrong, right)
    return text


# ---------------------------------------------------------------------------
# 智能模型推荐
# ---------------------------------------------------------------------------
def auto_select_model(duration_seconds: int, *, prefer_quality: bool = False) -> str:
    """按视频时长自动推荐模型。

    Parameters
    ----------
    duration_seconds
        视频总秒数。
    prefer_quality
        如果为 True,优先精度(大模型)而非速度。

    Returns
    -------
    模型名 (``"small"`` / ``"medium"`` / ``"large-v3"``)。
    """
    if prefer_quality:
        if duration_seconds < 600:
            return "large-v3"
        if duration_seconds < 3600:
            return "medium"
        return "small"
    # 默认:平衡速度优先
    if duration_seconds < 300:
        return "medium"
    if duration_seconds < 1800:
        return "small"
    return "tiny"


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------
PROMPT_TEMPLATES: dict[str, str] = {
    "education": (
        "这是一段关于教育的视频，涉及高考、作文、教学等概念。"
    ),
    "tech": (
        "这是一段技术分享视频，涉及编程、算法、架构等术语。"
    ),
    "literature": (
        "这是一段文学/人文类视频，涉及古诗词、作家、历史人物等。"
    ),
    "general": "",
}


def get_prompt_template(
    domain: str = "general",
    *,
    custom_prompt: Optional[str] = None,
) -> str:
    """获取领域专属 prompt,可与 custom_prompt 拼接。

    Parameters
    ----------
    domain
        领域名 (``education`` / ``tech`` / ``literature`` / ``general``)。
    custom_prompt
        用户额外 prompt,会拼接到模板后面。

    Returns
    -------
    完整 prompt 字符串。
    """
    base = PROMPT_TEMPLATES.get(domain, PROMPT_TEMPLATES["general"])
    if custom_prompt:
        if base:
            return base + " " + custom_prompt
        return custom_prompt
    return base


# ---------------------------------------------------------------------------
# HuggingFace 镜像
# ---------------------------------------------------------------------------
def setup_hf_mirror() -> str:
    """设置 HuggingFace 国内镜像。

    优先级: ``HF_ENDPOINT`` 环境变量 > ``https://hf-mirror.com``。
    返回值: 实际生效的镜像 URL。

    注意: 必须在 ``import transformers`` / ``import huggingface_hub``
    之前调用,否则镜像 URL 已被旧值缓存。
    """
    mirror = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_ENDPOINT", mirror)
    return mirror
