"""双模型交叉校对 — 同一条音频用第二个模型重转, diff 两稿聚焦人工复核。

动机(2026-10-01 复盘): 叁月聚粮/徐涛两个系列的审校都是手工再跑一遍
small 模型然后逐处 grep 对比。该流程固化为 ``--cross-check <model>``:
主稿转录完成后复用同一条音频, 用对照模型独立重转一次, 字符级 diff
产出分歧清单(上下文 + 两稿片段), 人工只需要复核清单上的位置。

对照稿是独立 ASR 结果, 不一定比主稿准 — 分歧点只代表"两模型不一致",
人工裁决以听原视频为准。
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 报告最多列出的分歧点数, 超出截断(长视频口水段噪音可能很多)
MAX_DIVERGENCES = 50
_CONTEXT_CHARS = 20

_TIMESTAMP_RE = re.compile(r"\*{0,2}\[\d{1,2}:\d{2}(?::\d{2})?\]\*{0,2}")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\s,。?!;;,.;?!、;:'\"“”‘’()()《》<>·—…\-]")


@dataclass
class Divergence:
    """一处两稿分歧。"""

    main: str  # 主稿片段
    cross: str  # 对照稿片段
    context: str  # 主稿中该位置前后的上下文


def _normalize_for_diff(text: str) -> str:
    """去时间戳 + 压空白, 得到用于比对的规整文本。"""
    text = _TIMESTAMP_RE.sub("", text)
    return _WS_RE.sub("", text)


def _norm_punct(s: str) -> str:
    """抹掉标点后比较 — 仅标点/空格差异不算分歧。"""
    return _PUNCT_RE.sub("", s)


def diff_transcripts(main_text: str, cross_text: str) -> list[Divergence]:
    """字符级 diff 两稿, 返回分歧点列表(按出现顺序)。

    只保留"实质内容不一致"的块: 纯标点/空白差异跳过; <2 字的
    插入/删除跳过(ASR 分段粒度差异噪音)。
    """
    main_norm = _normalize_for_diff(main_text)
    cross_norm = _normalize_for_diff(cross_text)
    matcher = difflib.SequenceMatcher(None, cross_norm, main_norm, autojunk=False)
    out: list[Divergence] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        cross_seg, main_seg = cross_norm[i1:i2], main_norm[j1:j2]
        if tag == "replace":
            if _norm_punct(cross_seg) == _norm_punct(main_seg):
                continue
        elif tag in ("insert", "delete"):
            if len(main_seg if tag == "insert" else cross_seg) < 2:
                continue
        else:  # equal
            continue
        start = max(0, j1 - _CONTEXT_CHARS)
        context = main_norm[start : j1 + _CONTEXT_CHARS]
        out.append(Divergence(main=main_seg, cross=cross_seg, context=context))
    return out


def build_report(
    divergences: list[Divergence],
    *,
    main_model: str,
    cross_model: str,
    audio_path: Optional[Path] = None,
) -> str:
    """把分歧列表渲染成 markdown 复核清单。"""
    lines = [
        "# 交叉校对报告",
        "",
        f"- **主稿模型**: {main_model}",
        f"- **对照稿模型**: {cross_model}(同一条音频独立重转)",
    ]
    if audio_path is not None:
        lines.append(f"- **音频**: `{audio_path}`")
    lines.append(f"- **分歧点**: {len(divergences)} 处")
    lines += [
        "",
        "> 分歧 ≠ 主稿有错: 对照稿只是另一个模型的独立识别结果。",
        "> 逐条听原视频裁决; 术语/专名以可查证来源为准。",
        "",
    ]
    if not divergences:
        lines.append("两稿实质内容一致(仅标点/分段差异), 无需人工比对。")
        return "\n".join(lines) + "\n"
    shown = divergences[:MAX_DIVERGENCES]
    for i, d in enumerate(shown, 1):
        lines.append(f"### {i}. 主稿「{d.main}」↔ 对照稿「{d.cross}」")
        lines.append("")
        lines.append(f"> …{d.context}…")
        lines.append("")
    if len(divergences) > MAX_DIVERGENCES:
        lines.append(f"(其余 {len(divergences) - MAX_DIVERGENCES} 处略 — 噪音居多, 建议先处理上方)")
    return "\n".join(lines) + "\n"


def _build_prompt(source_kind: Optional[str]) -> Optional[str]:
    """复用 TranscribeStage 的领域模板 + 学习术语拼 prompt(与主稿一致)。"""
    from types import SimpleNamespace

    try:
        from .pipeline_stages import TranscribeStage
    except Exception:
        return None
    try:
        return TranscribeStage._auto_prompt(
            SimpleNamespace(
                source=None if source_kind is None else SimpleNamespace(kind=source_kind)
            )
        )
    except Exception:
        return None


def run_cross_check(pipeline: Any, result: Any, model: str) -> Optional[Path]:
    """对已完成的转录结果用 ``model`` 重转同一条音频, 写出交叉校对报告。

    Returns
    -------
    报告路径; 两模型相同时打印说明并返回 None(无对照价值)。
    失败时抛异常, 由 CLI 层捕获降级(不影响主稿交付)。
    """
    audio_path = getattr(result, "audio_path", None)
    if not audio_path:
        raise RuntimeError("交叉校对需要音频文件(纯文本源如公众号文章无音频)")

    metadata = getattr(result, "metadata", None) or {}
    main_model = str(metadata.get("model") or getattr(result, "model", "") or "unknown")
    if model == main_model:
        print(f"ℹ️ 对照模型与主稿模型相同({model}), 跳过交叉校对")
        return None

    import copy as _copy

    settings = _copy.copy(pipeline.settings)
    settings.model = model
    transcriber = pipeline._create_transcriber(settings, model=model)
    print(f"🔍 交叉校对: 用 {transcriber.name} 重转同一条音频...")
    transcription = transcriber.transcribe(
        Path(audio_path),
        prompt=_build_prompt(getattr(result.source, "kind", None)),
        progress=True,
        language=getattr(result, "language", None),
    )
    cross_text = (transcription.get("text") or "").strip()
    if not cross_text:
        raise RuntimeError("对照模型转录结果为空")

    divergences = diff_transcripts(result.text or "", cross_text)
    report = build_report(
        divergences,
        main_model=main_model,
        cross_model=model,
        audio_path=Path(audio_path),
    )
    report_path = Path(result.transcript_path).with_suffix(".crosscheck.md")
    report_path.write_text(report, encoding="utf-8")
    print(f"🔍 交叉校对完成: {len(divergences)} 处分歧 -> {report_path.name}")
    return report_path
