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
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

if TYPE_CHECKING:  # 仅类型检查需要, 运行时延迟导入避免循环依赖
    from .pipeline_stages import PipelineContext

logger = logging.getLogger(__name__)

# 报告最多列出的实词分歧点数, 超出截断(长视频 100+ 分歧仍可能超)
MAX_DIVERGENCES = 100
_CONTEXT_CHARS = 20

_TIMESTAMP_RE = re.compile(r"\*{0,2}\[\d{1,2}:\d{2}(?::\d{2})?\]\*{0,2}")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\s,。?!;;,.;?!、;:'\"“”‘’()()《》<>·—…\-]")

# 虚词/语气词/第三人称代词 — 单字差异大概率不改变语义(的地得/吗呢啊吧/他她它),
# 报告只计数不逐条列(2026-10-02 复盘: 188 处分歧里这类噪音约占 2/3,
# 逐条列出会淹没真正需要人工裁决的实词分歧)
_NOISE_CHARS = frozenset("的地得吗呢啊吧呀哦嘛啦咯喔噢嗯了他她它")


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


def _strip_noise_chars(s: str) -> str:
    """抹掉虚词/语气词/第三人称代词单字。"""
    return "".join(ch for ch in s if ch not in _NOISE_CHARS)


def is_noise_divergence(d: Divergence) -> bool:
    """纯虚词/语气词/代词性别差异 → True(如 的↔地、他↔它、尾缀"了"增删)。

    保守起见噪音集合只收语气词/结构助词/他她它 — 在↔再、终↔中等
    同音实字不在集合内, 仍按实词分歧逐条列出。
    """
    return _strip_noise_chars(_norm_punct(d.main)) == _strip_noise_chars(_norm_punct(d.cross))


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
    """把分歧列表渲染成 markdown 复核清单(实词详列, 虚词/语气词只计数)。"""
    content = [d for d in divergences if not is_noise_divergence(d)]
    noise_count = len(divergences) - len(content)
    lines = [
        "# 交叉校对报告",
        "",
        f"- **主稿模型**: {main_model}",
        f"- **对照稿模型**: {cross_model}(同一条音频独立重转)",
    ]
    if audio_path is not None:
        lines.append(f"- **音频**: `{audio_path}`")
    summary = f"- **分歧点**: {len(divergences)} 处(实词 {len(content)} 处"
    if noise_count:
        summary += f"; 虚词/语气词差异 {noise_count} 处只计数"
    summary += ")"
    lines += [
        summary,
        "",
        "> 分歧 ≠ 主稿有错: 对照稿只是另一个模型的独立识别结果。",
        "> 逐条听原视频裁决; 术语/专名以可查证来源为准。",
        "",
    ]
    if not content:
        if noise_count:
            lines.append(f"无实词分歧(仅虚词/语气词差异 {noise_count} 处), 无需人工比对。")
        else:
            lines.append("两稿实质内容一致(仅标点/分段差异), 无需人工比对。")
        return "\n".join(lines) + "\n"
    lines.append(f"## 实词分歧({len(content)} 处, 逐条复核)")
    lines.append("")
    shown = content[:MAX_DIVERGENCES]
    for i, d in enumerate(shown, 1):
        lines.append(f"### {i}. 主稿「{d.main}」↔ 对照稿「{d.cross}」")
        lines.append("")
        lines.append(f"> …{d.context}…")
        lines.append("")
    if len(content) > MAX_DIVERGENCES:
        lines.append(
            f"(其余 {len(content) - MAX_DIVERGENCES} 处实词分歧略 —"
            " 完整清单含逐字原貌见同名 .crosscheck.json)"
        )
        lines.append("")
    if noise_count:
        lines.append(f"## 虚词/语气词差异({noise_count} 处, 只计数不逐条)")
        lines.append("")
        lines.append(
            "的地得/吗呢啊吧/他她它 等单字差异大概率不改变语义, 无需逐条复核;"
            " 如需逐字原貌见 raw ASR 文件。"
        )
    return "\n".join(lines) + "\n"


def divergences_to_payload(
    divergences: list[Divergence],
    *,
    main_model: str,
    cross_model: str,
    audio_path: Optional[Path] = None,
) -> dict[str, Any]:
    """分歧清单的完整 JSON 载荷 — md 报告截断到 ``MAX_DIVERGENCES`` 处,
    JSON 不截断(含全部实词 + 虚词逐条), 供脚本化复核。

    (2026-10-02 复盘: 长视频 189 处实词分歧只列前 100, 后半段只能
    通读 raw 稿人工兜底 — 落一份完整机读清单。)
    """
    return {
        "main_model": main_model,
        "cross_model": cross_model,
        "audio": str(audio_path) if audio_path else None,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(divergences),
        "content_count": sum(1 for d in divergences if not is_noise_divergence(d)),
        "noise_count": sum(1 for d in divergences if is_noise_divergence(d)),
        "divergences": [
            {
                "index": i,
                "type": "noise" if is_noise_divergence(d) else "content",
                "main": d.main,
                "cross": d.cross,
                "context": d.context,
            }
            for i, d in enumerate(divergences, 1)
        ],
    }


def _build_prompt(source_kind: Optional[str]) -> Optional[str]:
    """复用 TranscribeStage 的领域模板 + 学习术语拼 prompt(与主稿一致)。"""
    from types import SimpleNamespace

    try:
        from .pipeline_stages import TranscribeStage
    except Exception:
        return None
    try:
        # _auto_prompt 只读 ctx.source.kind; 传最小 duck-typed 桩避免为拼
        # prompt 构造完整 PipelineContext, 这里用 cast 声明该意图。
        stub = cast(
            "PipelineContext",
            SimpleNamespace(
                source=None if source_kind is None else SimpleNamespace(kind=source_kind)
            ),
        )
        return TranscribeStage._auto_prompt(stub)
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
    payload = divergences_to_payload(
        divergences,
        main_model=main_model,
        cross_model=model,
        audio_path=Path(audio_path),
    )
    json_path = report_path.with_name(report_path.stem + ".json")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    content_count = payload["content_count"]
    noise_count = payload["noise_count"]
    note = f"(虚词/语气词差异 {noise_count} 处只计数)" if noise_count else ""
    print(f"🔍 交叉校对完成: 实词分歧 {content_count} 处{note} -> {report_path.name}")
    return report_path
