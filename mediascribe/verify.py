"""Third-sample verification — adjudicate doubtful spots in delivered
transcripts with an independent third decoding.

Background (2026-10-08 retro, see retro-2026-10-08): ``--cleanup-media``
deletes intermediate media once cross-checking completes, yet residual
doubts are exactly the "both models misheard" spots — after deletion the
re-listen / re-check path is gone. This module supplies the
"verify before delete" step of the review workflow::

    python -m mediascribe verify output/transcripts/douyin_xxx.md
    python -m mediascribe verify output/transcripts/douyin_xxx.md --model small

Flow:
1. Locate the same-stem metadata (audio path + source URL);
2. Reuse the audio if still on disk, otherwise re-download from the source
   URL and extract audio (managed dirs);
3. Independent re-sampling with **no term injection + multi-temperature**
   (default large-v3, ``--model`` to change) — no initial_prompt removes
   term-injection bias, multiple temperatures let low-confidence spans
   trigger fallback sampling, forming a decode-level independent sample
   versus the main transcript (greedy);
4. Write the timestamped sample transcript ``third-sample-<stem>-<model>.md``;
5. If ``<stem>.crosscheck.json`` exists, produce third-sample approximate
   evidence per content-word divergence (difflib anchor alignment, labeled
   "matches main / matches cross / third reading / not located") into
   ``<stem>.verify.md`` for human adjudication.

Alignment is approximate by design: the sample and main transcripts
tokenize/segment differently, evidence windows support human review, and
the transcript is never auto-edited.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .audio_utils import extract_audio
from .i18n import t
from .inputs import parse_source, safe_stem
from .pipeline_stages import _smart_pick_downloader

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class VerifyArtifacts:
    """Artifact paths and metadata involved in one verification run."""

    transcript_path: Path
    metadata_path: Path
    metadata: Dict[str, Any]
    audio_path: Optional[Path]
    source_url: Optional[str]


@dataclass
class DivergenceEvidence:
    """Approximate third-sample evidence for one divergence entry."""

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
    """Locate the same-stem metadata JSON from a transcript path; extract
    the audio path and source URL.

    Both sides are resolved: the CLI is often handed relative paths (e.g.
    ``output/transcripts/x.md``); without resolving, ``is_relative_to``
    against an absolute workspace_root is always False and wrongly takes
    the side-by-side branch (first caught in practice 2026-10-08).
    """
    transcript_path = Path(transcript_path).resolve()
    root = Path(workspace_root).resolve()
    if transcript_path.is_relative_to(root):
        metadata_path = root / "metadata" / f"{transcript_path.stem}.json"
    else:
        metadata_path = transcript_path.with_suffix(".json")
    if not metadata_path.exists():
        raise FileNotFoundError(t("VERIFY_NO_METADATA", path=metadata_path))
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
    """Extract the transcript body (strip HTML comments/headers/basic-info,
    keep the 「转录内容」 section)."""
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
    """Reuse the on-disk audio; otherwise re-download from the source URL
    and extract audio.

    Returns
    -------
    ``(audio_path, reused)`` — ``reused=False`` means freshly downloaded.
    """
    if artifacts.audio_path is not None and artifacts.audio_path.is_file():
        return artifacts.audio_path, True
    if not artifacts.source_url:
        raise RuntimeError(t("VERIFY_NO_AUDIO_NO_URL"))
    source = parse_source(artifacts.source_url)
    downloader = _smart_pick_downloader(source)
    downloaded = downloader.download(source, settings)
    stem = safe_stem(artifacts.transcript_path.stem)
    audio = extract_audio(Path(downloaded.video_path), settings.audio_dir, stem)
    if audio is None:
        raise RuntimeError(t("VERIFY_EXTRACT_FAILED"))
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
    """Re-sample with no term injection + multi-temperature. A fake
    ``transcriber`` may be injected for tests."""
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
    """Write the timestamped sample transcript (same format as the manual
    verification round's artifact)."""
    segs = transcription.get("segments") or []
    provenance = t("VERIFY_PROV_REUSE") if reused else t("VERIFY_PROV_FRESH")
    lines = [
        t(
            "VERIFY_SAMPLE_HEADER",
            model=model,
            audio=audio_path.name,
            provenance=provenance,
            source=audio_source,
        ),
        t("VERIFY_SAMPLE_PURPOSE"),
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
    """Third-sample full text + char position → timestamp mapping."""

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
    """Approximately locate one divergence within the third-sample text;
    return ``(verdict, evidence window, timestamp)``.

    Alignment strategy: run ``SequenceMatcher.get_matching_blocks`` once
    between the context (main-transcript, ~60 chars) and the third-sample
    full text; map the divergence word's position through the anchor block
    covering it and take a ±radius char window. Approximate positioning,
    for human adjudication.
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
    """Produce third-sample evidence for each crosscheck content-word divergence."""
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


def _verdict_counts(evidences: Sequence[DivergenceEvidence]) -> dict:
    """Counts across the four verdicts (同主稿/同对照/第三读/未定位).

    Shared by build_verify_report and run_verify; unknown verdicts do not
    crash — they are counted under their own key (matching the report
    renderer's ``counts.get`` semantics).
    """
    counts = {"同主稿": 0, "同对照": 0, "第三读": 0, "未定位": 0}
    for e in evidences:
        counts[e.verdict] = counts.get(e.verdict, 0) + 1
    return counts


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
    """Render the .verify.md — per-divergence entries + third-sample
    approximate evidence."""
    counts = _verdict_counts(evidences)
    total = len(evidences)
    audio_note = t("VERIFY_PROV_REUSE_SHORT") if audio_reused else t("VERIFY_PROV_FRESH_SHORT")
    lines = [
        t("VERIFY_REPORT_TITLE"),
        "",
        t("VERIFY_REPORT_MAIN", name=transcript_path.name, model=main_model),
        t("VERIFY_REPORT_CROSS", model=cross_model) if cross_model else t("VERIFY_REPORT_NO_CROSS"),
        t("VERIFY_REPORT_SAMPLE", model=model),
        t("VERIFY_REPORT_AUDIO", name=audio_path.name, note=audio_note),
        t("VERIFY_REPORT_SAMPLE_FILE", name=sample_path.name),
        t(
            "VERIFY_REPORT_DIVERGENCE",
            total=total,
            a=counts["同主稿"],
            b=counts["同对照"],
            c=counts["第三读"],
            d=counts["未定位"],
        ),
        "",
        t("VERIFY_REPORT_NOTE"),
        "",
    ]
    if not evidences:
        lines.append(t("VERIFY_REPORT_NONE"))
    for e in evidences:
        cross_txt = e.cross if e.cross is not None else t("VERIFY_CROSS_MISSING")
        head = t("VERIFY_ENTRY", index=e.index, main=e.main, cross=cross_txt)
        if e.timestamp:
            head += f"  [{e.timestamp}]"
        lines.append(head)
        lines.append(f"> …{e.context}…")
        if e.window is not None:
            lines.append(t("VERIFY_VERDICT_LINE", verdict=e.verdict))
            lines.append(t("VERIFY_WINDOW_LINE", window=e.window))
        else:
            lines.append(t("VERIFY_VERDICT_NO_ANCHOR", verdict=e.verdict))
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
    """Run the third-sample verification; return the .verify.md report path.

    A fake ``transcriber`` may be injected (tests); by default builds a
    :class:`FasterWhisperTranscriber` from ``model``/``device``.
    """
    from .transcribers.faster_whisper import FasterWhisperTranscriber

    transcript_path = Path(transcript_path)
    if not transcript_path.exists():
        raise FileNotFoundError(t("VERIFY_NO_TRANSCRIPT", path=transcript_path))

    artifacts = locate_artifacts(transcript_path, settings.workspace_root)
    audio_path, reused = ensure_audio(artifacts, settings)
    print(
        t(
            "VERIFY_PRINT_AUDIO",
            path=audio_path,
            status=t("VERIFY_AUDIO_REUSE") if reused else t("VERIFY_AUDIO_FRESH"),
        )
    )

    if transcriber is None:
        transcriber = FasterWhisperTranscriber(model, device=device)
    print(t("VERIFY_PRINT_SAMPLING", name=transcriber.name, model=model))
    transcription = transcribe_third_sample(audio_path, transcriber, language=language)
    segs = transcription.get("segments") or []
    if not segs:
        raise RuntimeError(t("VERIFY_EMPTY"))

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
    print(t("VERIFY_PRINT_SAMPLE", path=sample_path))

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
        cross_model = str(artifacts.metadata.get("cross_check_model") or t("VERIFY_CROSS_FALLBACK"))

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

    counts = _verdict_counts(evidences)
    print(
        t(
            "VERIFY_PRINT_DONE",
            total=len(evidences),
            a=counts["同主稿"],
            b=counts["同对照"],
            c=counts["第三读"],
            d=counts["未定位"],
        )
    )
    print(t("VERIFY_PRINT_REPORT", path=report_path))
    return report_path
