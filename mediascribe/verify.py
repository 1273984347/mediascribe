"""第三采样验证 — 对已交付转录稿的存疑项做第三方重采样裁决。

背景（2026-10-08 复盘，见 retro-2026-10-08）: ``--cleanup-media`` 在
交叉校对完成后即删中间媒体，而残余存疑恰是"两模型同误"处，删除后失去
回听/复核路径。本模块为审校工作流补上"证实之后再删"的一环::

    python -m mediascribe verify output/transcripts/douyin_xxx.md
    python -m mediascribe verify output/transcripts/douyin_xxx.md --model small

流程:
1. 定位同名 metadata（音频路径 + 来源 URL）；
2. 音频在盘则复用，否则按来源 URL 重新下载并抽音频（managed 目录）；
3. **无术语注入 + 多档温度**独立重采样（默认 large-v3，``--model`` 可换）
   —— 无 initial_prompt 消除术语注入偏置，多档温度让低置信段触发回退
   采样，与主稿（贪心）形成解码级独立样本；
4. 写时间戳采样稿 ``third-sample-<stem>-<model>.md``；
5. 若存在 ``<stem>.crosscheck.json``，逐条实词分歧给出第三采样近似证据
   （difflib 锚点对齐，标注"同主稿/同对照/第三读/未定位"），写
   ``<stem>.verify.md`` 供人工裁决。

对齐为近似定位：采样稿与主稿分词/断句不同，证据窗口供人工复核，
不自动改稿。
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .audio_utils import extract_audio
from .inputs import parse_source, safe_stem
from .pipeline_stages import _smart_pick_downloader

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class VerifyArtifacts:
    """一次验证涉及的工件路径与元数据。"""

    transcript_path: Path
    metadata_path: Path
    metadata: Dict[str, Any]
    audio_path: Optional[Path]
    source_url: Optional[str]


@dataclass
class DivergenceEvidence:
    """单条分歧在第三采样中的近似证据。"""

    index: int
    main: str
    cross: Optional[str]
    context: str
    verdict: str  # 同主稿 / 同对照 / 第三读 / 未定位
    window: Optional[str]  # 第三采样证据窗口(原样)
    timestamp: Optional[str]  # 窗口起点 [mm:ss]


# ---------------------------------------------------------------------------
# 工件定位与音频获取
# ---------------------------------------------------------------------------


def locate_artifacts(transcript_path: Path, workspace_root: Path) -> VerifyArtifacts:
    """由转录稿路径定位同名 metadata JSON, 取音频路径与来源 URL。

    两侧均 resolve: CLI 常传相对路径(如 ``output/transcripts/x.md``),
    不 resolve 时 ``is_relative_to`` 对绝对 workspace_root 恒为 False,
    会错走同目录分支(2026-10-08 首次实测抓到)。
    """
    transcript_path = Path(transcript_path).resolve()
    root = Path(workspace_root).resolve()
    if transcript_path.is_relative_to(root):
        metadata_path = root / "metadata" / f"{transcript_path.stem}.json"
    else:
        metadata_path = transcript_path.with_suffix(".json")
    if not metadata_path.exists():
        raise FileNotFoundError(f"未找到元数据文件: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    audio_path: Optional[Path] = None
    raw_audio = metadata.get("audio_path")
    if raw_audio:
        audio_path = Path(str(raw_audio))

    source = metadata.get("source") or {}
    source_url = source.get("url") or source.get("raw_input")
    return VerifyArtifacts(
        transcript_path=transcript_path,
        metadata_path=metadata_path,
        metadata=metadata,
        audio_path=audio_path,
        source_url=str(source_url) if source_url else None,
    )


def extract_transcript_body(text: str) -> str:
    """取转录稿正文(剥离 HTML 注释/标题/基本信息, 保留「转录内容」节)。"""
    if "## 转录内容" in text:
        text = text.split("## 转录内容", 1)[1]
    # 截掉正文之后的审校说明等附节(raw 稿通常没有; 审校稿容错)
    for marker in ("### 审校说明", "## 核心内容蒸馏"):
        if marker in text:
            text = text.split(marker, 1)[0]
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("<!--")
            or stripped.startswith("#")
            or stripped.startswith(">")
        ):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def ensure_audio(artifacts: VerifyArtifacts, settings: Any) -> Tuple[Path, bool]:
    """音频在盘则复用; 否则按来源 URL 重新下载并抽音频。

    Returns
    -------
    ``(audio_path, reused)`` — ``reused=False`` 表示本轮重新下载。
    """
    if artifacts.audio_path is not None and artifacts.audio_path.is_file():
        return artifacts.audio_path, True
    if not artifacts.source_url:
        raise RuntimeError("音频不存在且 metadata 无来源 URL, 无法重新下载; 请手动提供音频")
    source = parse_source(artifacts.source_url)
    downloader = _smart_pick_downloader(source)
    downloaded = downloader.download(source, settings)
    stem = safe_stem(artifacts.transcript_path.stem)
    audio = extract_audio(Path(downloaded.video_path), settings.audio_dir, stem)
    if audio is None:
        raise RuntimeError("音频提取失败")
    return audio, False


# ---------------------------------------------------------------------------
# 第三采样
# ---------------------------------------------------------------------------


def transcribe_third_sample(
    audio_path: Path,
    transcriber: Any,
    *,
    language: Optional[str] = "zh",
) -> Dict[str, Any]:
    """无术语注入 + 多档温度重采样。``transcriber`` 可注入 fake 供测试。"""
    result: Dict[str, Any] = transcriber.transcribe(
        Path(audio_path),
        prompt=None,  # 关键: 不注入术语 prompt, 保持对主稿的独立性
        progress=True,
        language=language,
        temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
    )
    return result


def _format_ts(seconds: float) -> str:
    mm, ss = int(seconds // 60), int(seconds % 60)
    return f"{mm:02d}:{ss:02d}"


def write_sample_md(
    out_path: Path,
    transcription: Dict[str, Any],
    *,
    model: str,
    audio_path: Path,
    reused: bool,
    audio_source: str,
) -> None:
    """写时间戳采样稿(与人工验证轮产物同格式)。"""
    segs = transcription.get("segments") or []
    provenance = "复用磁盘上残留的音频" if reused else "验证轮重新下载(时长未另核)"
    lines = [
        f"<!-- 第三采样(mediascribe verify): {model}, 无 initial_prompt(术语库不注入), "
        f"多档温度。音频: {audio_path.name}({provenance}; 来源: {audio_source}) -->",
        "<!-- 用途: 存疑项第三方裁决, 配合同名 .verify.md 阅读 -->",
        "",
    ]
    for seg in segs:
        start = float(seg.get("start", 0.0) or 0.0)
        text = str(seg.get("text", "")).strip()
        if text:
            lines.append(f"[{_format_ts(start)}] {text}")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 分歧证据对齐
# ---------------------------------------------------------------------------

_NOISE_RE = re.compile(r"[\s，。,.!?;；:：\"'“”‘’()（）\[\]【】、\n\r]")


def _norm(s: str) -> str:
    return _NOISE_RE.sub("", s or "")


class _ThirdTextIndex:
    """第三采样全文 + 字符位置 → 时间戳映射。"""

    def __init__(self, segments: Sequence[Dict[str, Any]]):
        parts: List[str] = []
        self.spans: List[Tuple[int, int, float]] = []
        pos = 0
        for seg in segments:
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            start = float(seg.get("start", 0.0) or 0.0)
            parts.append(text)
            self.spans.append((pos, pos + len(text), start))
            pos += len(text)
        self.text = "".join(parts)

    def timestamp_at(self, char_pos: int) -> Optional[str]:
        for lo, hi, start in self.spans:
            if lo <= char_pos < hi:
                return _format_ts(start)
        return None


def evidence_for(
    context: str,
    main_word: str,
    cross_word: Optional[str],
    third: _ThirdTextIndex,
    *,
    radius: int = 30,
) -> Tuple[str, Optional[str], Optional[str]]:
    """在第三采样全文中近似定位一条分歧, 返回 ``(判定, 证据窗口, 时间戳)``。

    对齐策略: 对 context(主稿上下文 ~60 字) 与第三全文做一次
    ``SequenceMatcher.get_matching_blocks``, 用覆盖分歧词的锚点块把分歧词
    位置映射到第三全文, 取 ±radius 字符窗口。近似定位, 供人工裁决。
    """
    ctx = context or ""
    if not ctx or not third.text:
        return "未定位", None, None
    blocks = [
        b
        for b in difflib.SequenceMatcher(
            None, ctx, third.text, autojunk=False
        ).get_matching_blocks()
        if b.size > 0
    ]
    wpos = ctx.find(main_word) if main_word else -1
    if wpos < 0:
        wpos = max(0, len(ctx) // 2)  # 主词缺失(如对照稿漏字)时取上下文中点
    anchor = None
    if blocks:
        for b in blocks:
            if b.a <= wpos < b.a + b.size:
                anchor = b
                break
        if anchor is None:
            anchor = min(blocks, key=lambda b: min(abs(b.a - wpos), abs(b.a + b.size - wpos)))
    if anchor is None:
        return "未定位", None, None
    tpos = anchor.b + max(0, wpos - anchor.a)
    lo = max(0, tpos - radius)
    window = third.text[lo : min(len(third.text), tpos + len(main_word or "") + radius)]

    norm_main, norm_cross = _norm(main_word), _norm(cross_word or "")
    norm_win = _norm(window)
    if norm_main and norm_main in norm_win:
        verdict = "同主稿"
    elif norm_cross and norm_cross in norm_win:
        verdict = "同对照"
    else:
        verdict = "第三读"
    return verdict, window, third.timestamp_at(max(0, tpos - 1))


def collect_evidence(
    divergences: Sequence[Dict[str, Any]],
    segments: Sequence[Dict[str, Any]],
) -> List[DivergenceEvidence]:
    """对 crosscheck 实词分歧逐条产出第三采样证据。"""
    third = _ThirdTextIndex(segments)
    out: List[DivergenceEvidence] = []
    for i, d in enumerate(divergences, start=1):
        main = str(d.get("main") or "")
        cross_raw = d.get("cross")
        cross = str(cross_raw) if cross_raw is not None else None
        context = str(d.get("context") or "")
        verdict, window, ts = evidence_for(context, main, cross, third)
        out.append(
            DivergenceEvidence(
                index=int(d.get("index", i)),
                main=main,
                cross=cross,
                context=context,
                verdict=verdict,
                window=window,
                timestamp=ts,
            )
        )
    return out


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def build_verify_report(
    evidences: Sequence[DivergenceEvidence],
    *,
    transcript_path: Path,
    sample_path: Path,
    audio_path: Path,
    audio_reused: bool,
    model: str,
    main_model: str,
    cross_model: Optional[str],
) -> str:
    """渲染 .verify.md — 逐条分歧 + 第三采样近似证据。"""
    counts = {"同主稿": 0, "同对照": 0, "第三读": 0, "未定位": 0}
    for e in evidences:
        counts[e.verdict] = counts.get(e.verdict, 0) + 1
    total = len(evidences)
    audio_note = "复用磁盘音频" if audio_reused else "验证轮重新下载"
    lines = [
        "# 第三采样验证报告",
        "",
        f"- **主稿**: `{transcript_path.name}`（{main_model}）",
        f"- **对照**: {'`' + cross_model + '`' if cross_model else '无 crosscheck 清单'}",
        f"- **第三采样**: `{model}`（无术语注入 + 多档温度, 解码级独立样本）",
        f"- **音频**: `{audio_path.name}`（{audio_note}）",
        f"- **采样稿**: `{sample_path.name}`",
        f"- **分歧**: {total} 处 = 同主稿 {counts['同主稿']} / 同对照 {counts['同对照']}"
        f" / 第三读 {counts['第三读']} / 未定位 {counts['未定位']}",
        "",
        "（对齐为近似定位, 证据窗口供人工裁决; 判定按窗口内是否出现主稿词/"
        "对照词（去标点归一）, 第三读≠主稿错。复核后请把结论回写审校说明,"
        "再清理中间媒体。）",
        "",
    ]
    if not evidences:
        lines.append("无实词分歧清单(无 .crosscheck.json 或分歧为空), 仅产出采样稿。")
    for e in evidences:
        cross_txt = e.cross if e.cross is not None else "（漏）"
        head = f"### {e.index}. 主「{e.main}」↔ 照「{cross_txt}」"
        if e.timestamp:
            head += f"  [{e.timestamp}]"
        lines.append(head)
        lines.append(f"> …{e.context}…")
        if e.window is not None:
            lines.append(f"判定: **{e.verdict}**")
            lines.append(f"第三采样: 「{e.window}」")
        else:
            lines.append(f"判定: **{e.verdict}**（第三采样中未找到近似锚点）")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def run_verify(
    transcript_path: Path,
    settings: Any,
    *,
    model: str = "large-v3",
    device: Optional[str] = None,
    language: Optional[str] = "zh",
    transcriber: Any = None,
) -> Path:
    """执行第三采样验证, 返回 .verify.md 报告路径。

    ``transcriber`` 可注入假转录器（测试用）; 默认按 ``model``/``device``
    构建 :class:`FasterWhisperTranscriber`。
    """
    from .transcribers.faster_whisper import FasterWhisperTranscriber

    transcript_path = Path(transcript_path)
    if not transcript_path.exists():
        raise FileNotFoundError(f"转录稿不存在: {transcript_path}")

    artifacts = locate_artifacts(transcript_path, settings.workspace_root)
    audio_path, reused = ensure_audio(artifacts, settings)
    print(f"🎵 音频: {audio_path}({'复用' if reused else '重新下载'})")

    if transcriber is None:
        transcriber = FasterWhisperTranscriber(model, device=device)
    print(f"🔎 第三采样: {transcriber.name}/{model}, 无术语注入 + 多档温度...")
    transcription = transcribe_third_sample(audio_path, transcriber, language=language)
    segs = transcription.get("segments") or []
    if not segs:
        raise RuntimeError("第三采样结果为空")

    model_tag = re.sub(r"[^0-9A-Za-z]+", "", model) or "model"
    sample_path = transcript_path.with_name(f"third-sample-{transcript_path.stem}-{model_tag}.md")
    source_desc = artifacts.source_url or "unknown"
    write_sample_md(
        sample_path,
        transcription,
        model=model,
        audio_path=Path(audio_path),
        reused=reused,
        audio_source=source_desc,
    )
    print(f"📝 采样稿: {sample_path}")

    crosscheck_json = transcript_path.with_suffix(".crosscheck.json")
    divergences: List[Dict[str, Any]] = []
    cross_model: Optional[str] = None
    if crosscheck_json.exists():
        payload = json.loads(crosscheck_json.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            divergences = [d for d in payload if isinstance(d, dict)]
        else:
            divergences = list(payload.get("divergences") or [])
    if divergences:
        # crosscheck.json 不记录对照模型名, 报告里只标注"对照稿存在"
        cross_model = str(artifacts.metadata.get("cross_check_model") or "crosscheck 对照稿")

    evidences = collect_evidence(divergences, segs)
    main_model = str(artifacts.metadata.get("model") or "unknown")
    report = build_verify_report(
        evidences,
        transcript_path=transcript_path,
        sample_path=sample_path,
        audio_path=Path(audio_path),
        audio_reused=reused,
        model=model,
        main_model=main_model,
        cross_model=cross_model if divergences else None,
    )
    report_path = transcript_path.with_suffix(".verify.md")
    report_path.write_text(report, encoding="utf-8")

    counts = {"同主稿": 0, "同对照": 0, "第三读": 0, "未定位": 0}
    for e in evidences:
        counts[e.verdict] += 1
    print(
        f"✅ 验证完成: {len(evidences)} 处分歧 = "
        f"同主稿 {counts['同主稿']} / 同对照 {counts['同对照']} / "
        f"第三读 {counts['第三读']} / 未定位 {counts['未定位']}"
    )
    print(f"📋 验证报告: {report_path}")
    return report_path
