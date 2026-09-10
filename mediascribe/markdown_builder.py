"""Markdown 输出构建 (v3.4.0)

把转录结果整理成可读的结构化 Markdown，解决旧版输出三大痛点：

1. **文字墙** — 旧版把整段 ASR 文本交给按句号分段的
   ``_split_into_paragraphs``，而 ASR 原文往往整段无标点，结果
   输出成一整面无分段的文字墙。新版直接按 ASR ``segments`` 分段
   （按字符数 + 停顿边界聚合），无 segments 时才回退旧逻辑。
2. **文件名当标题** — 旧版 H1 是 ``douyin_<id>.mp4`` 这类文件名；
   新版优先用平台元数据里的真实标题。
3. **元数据浪费** — 平台元数据里的作者 / 时长 / 来源链接以前
   大多丢弃，新版聚合进「基本信息」块。

公共入口是 :func:`build_markdown`；``Pipeline._build_markdown_content``
委托到这里，旧调用方契约不变。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

__all__ = [
    "build_markdown",
    "format_timestamp",
    "group_segments_into_paragraphs",
    "split_into_paragraphs",
    "resolve_display_title",
]

# 平台 kind → 展示名
_PLATFORM_LABELS: Dict[str, str] = {
    "douyin": "抖音",
    "bilibili": "哔哩哔哩",
    "youtube": "YouTube",
    "xiaohongshu": "小红书",
    "wechat_mp": "微信公众号",
    "tiktok": "TikTok",
    "video": "本地视频",
    "audio": "本地音频",
}

# 正文单段目标字符数 / 硬上限 / 长停顿阈值 / 长停顿换段的最小字数
_PARAGRAPH_TARGET_CHARS = 450
_PARAGRAPH_HARD_CAP = 900
_PARAGRAPH_PAUSE_SECONDS = 2.0
_PARAGRAPH_MIN_CHARS = 120


def format_timestamp(seconds: Optional[float]) -> str:
    """秒 → ``mm:ss``（超过 1 小时为 ``h:mm:ss``），非法输入返回空串。"""
    if seconds is None:
        return ""
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _segment_field(seg: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = seg.get(k)
        if v is not None:
            return v
    return None


def group_segments_into_paragraphs(
    segments: Optional[List[Any]],
    *,
    target_chars: int = _PARAGRAPH_TARGET_CHARS,
    hard_cap: int = _PARAGRAPH_HARD_CAP,
    pause_seconds: float = _PARAGRAPH_PAUSE_SECONDS,
    min_chars: int = _PARAGRAPH_MIN_CHARS,
) -> List[Dict[str, Any]]:
    """把 ASR segments 聚合成自然段落。

    聚合策略：顺序累加 segment 文本，满足任一条件即换段 —

    * 累计 ≥ ``target_chars`` 且与上一条之间有停顿（≥ 0.5 s）
    * 累计 ≥ ``min_chars`` 且出现长停顿（≥ ``pause_seconds``，
      说话人换气/换话题的强边界信号）
    * 累计 ≥ ``hard_cap``（无标点文本的兜底硬切）

    Returns
    -------
    list of ``{"start": float | None, "text": str}``，start 为该段
    首条 segment 的开始时间（秒），供 ``--timestamps`` 前缀使用。
    空/非法输入返回空列表。
    """
    groups: List[Dict[str, Any]] = []
    if not segments:
        return groups

    buf: List[str] = []
    buf_start: Optional[float] = None
    buf_len = 0
    prev_end: Optional[float] = None

    def flush() -> None:
        nonlocal buf, buf_start, buf_len
        text = "".join(buf).strip()
        if text:
            groups.append({"start": buf_start, "text": text})
        buf = []
        buf_start = None
        buf_len = 0

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        start = _segment_field(seg, "start", "t0")
        end = _segment_field(seg, "end", "t1")
        try:
            start_f = float(start) if start is not None else None
        except (TypeError, ValueError):
            start_f = None
        try:
            end_f = float(end) if end is not None else None
        except (TypeError, ValueError):
            end_f = None

        if buf:
            gap = start_f - prev_end if (prev_end is not None and start_f is not None) else None
            if (
                (buf_len >= target_chars and gap is not None and gap >= 0.5)
                or (buf_len >= min_chars and gap is not None and gap >= pause_seconds)
                or buf_len >= hard_cap
            ):
                flush()

        if not buf:
            buf_start = start_f
        buf.append(text)
        buf_len += len(text)
        if end_f is not None:
            prev_end = end_f

    flush()
    return groups


def split_into_paragraphs(text: str) -> List[str]:
    """旧版标点分段逻辑（无 segments 时的回退路径）。

    从 ``Pipeline._split_into_paragraphs`` 原样迁入：按中英文句末
    标点切句，每 3 句合成一段。对无标点的 ASR 原文基本退化为单段，
    只在有标点时有效。
    """
    sentences: List[str] = []
    current: List[str] = []

    chars = list(text)
    i = 0
    n = len(chars)

    while i < n:
        c = chars[i]
        current.append(c)

        if c in ["。", "！", "？", "!", "?"] and i + 1 < n:
            if len(current) > 50 or chars[i + 1] in [" ", "\n", "\t"]:
                sentences.append("".join(current).strip())
                current = []
        i += 1

    if current:
        sentences.append("".join(current).strip())

    paragraphs: List[str] = []
    current_paragraph: List[str] = []

    for sentence in sentences:
        if sentence:
            current_paragraph.append(sentence)
            if len(current_paragraph) >= 3:
                paragraphs.append("".join(current_paragraph))
                current_paragraph = []

    if current_paragraph:
        paragraphs.append("".join(current_paragraph))

    return paragraphs if paragraphs else [text]


def resolve_display_title(base_name: str, downloaded: Any = None) -> str:
    """H1 标题：优先平台元数据真实标题，回退 base_name。"""
    meta: Dict[str, Any] = {}
    if downloaded is not None:
        meta = getattr(downloaded, "metadata", None) or {}
    title = str(meta.get("title") or "").strip()
    if title:
        return title
    return str(base_name or "转录结果").strip()


def build_markdown(
    title: str,
    text: str,
    transcription: Optional[Dict[str, Any]],
    downloaded: Any = None,
    *,
    timestamps: bool = False,
    source: Any = None,
) -> str:
    """构建结构化 Markdown 转录稿。

    Parameters
    ----------
    title
        基础标题（通常是文件名 / display_name），仅当平台元数据
        缺少真实标题时使用。
    text
        转录全文（已过术语校正 / LLM 后处理）。
    transcription
        ASR 返回 dict，可含 ``segments`` / ``model`` / ``language``。
    downloaded
        :class:`~mediascribe.models.DownloadResult` 或 None。
    timestamps
        True 时每个段落前缀 ``**[mm:ss]**`` 时间戳。
    source
        :class:`~mediascribe.models.SourceRef` 或 None，用于「平台」
        标签与来源链接兜底。
    """
    meta: Dict[str, Any] = {}
    if downloaded is not None:
        meta = getattr(downloaded, "metadata", None) or {}

    display_title = resolve_display_title(title, downloaded)

    lines: List[str] = [f"# {display_title}", ""]

    # ---- 基本信息 ----
    lines.append("## 基本信息")
    lines.append("")

    kind = getattr(source, "kind", None)
    if kind and kind in _PLATFORM_LABELS:
        lines.append(f"- **平台**: {_PLATFORM_LABELS[kind]}")

    uploader = meta.get("uploader") or meta.get("nickname") or meta.get("author")
    if uploader:
        lines.append(f"- **作者**: {uploader}")

    duration = meta.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        duration_min = int(duration // 60)
        duration_sec = int(duration % 60)
        lines.append(f"- **时长**: {duration_min}分{duration_sec}秒")

    source_url = meta.get("webpage_url") or meta.get("url") or getattr(source, "url", None)
    if source_url:
        lines.append(f"- **来源**: {source_url}")

    lines.append(f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    engine = (transcription or {}).get("model") or "unknown"
    lines.append(f"- **转录引擎**: {engine}")
    language = (transcription or {}).get("language")
    if language:
        lines.append(f"- **识别语言**: {language}")
    lines.append("")

    # ---- 转录内容 ----
    lines.append("## 转录内容")
    lines.append("")

    segments = (transcription or {}).get("segments")
    groups = group_segments_into_paragraphs(segments) if segments else []
    if groups:
        for group in groups:
            prefix = ""
            if timestamps and group["start"] is not None:
                ts = format_timestamp(group["start"])
                if ts:
                    prefix = f"**[{ts}]** "
            lines.append(prefix + group["text"])
            lines.append("")
    else:
        for paragraph in split_into_paragraphs(text):
            if paragraph.strip():
                lines.append(paragraph)
                lines.append("")

    return "\n".join(lines)
