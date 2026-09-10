"""
数据模型 - 参考 bili2text 的设计
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional

# A literal type for the *known* platform kinds.  We still keep ``kind``
# declared as ``str`` on the dataclass so the runtime doesn't crash on
# unknown kinds (defensive parsing of untrusted URLs), but the type
# alias is the single source of truth for code that wants strict checks.
SourceKind = Literal[
    "bilibili",
    "youtube",
    "douyin",
    "xiaohongshu",
    "wechat_mp",
    "tiktok",
    "video",  # local video file
    "audio",  # local audio file
    "unknown",  # parse_source() could not classify
]
KNOWN_SOURCE_KINDS: tuple[str, ...] = (
    "bilibili",
    "youtube",
    "douyin",
    "xiaohongshu",
    "wechat_mp",
    "tiktok",
    "video",
    "audio",
    "unknown",
)


@dataclass
class SourceRef:
    """输入源引用"""

    raw_input: str
    # ``kind`` is intentionally a plain ``str`` so unknown URLs (e.g.
    # a brand-new platform we haven't taught the parser about) don't
    # crash construction.  Use :data:`SourceKind` for type hints in
    # new code; use ``is_known_kind()`` at runtime if you need a
    # hard check.
    kind: str = "unknown"
    bv: Optional[str] = None
    url: Optional[str] = None
    path: Optional[Path] = None

    @property
    def display_name(self) -> str:
        if self.kind == "bilibili" and self.bv:
            return self.bv
        if self.path:
            return self.path.name
        if self.url:
            return self.url
        return self.raw_input

    @property
    def is_known_kind(self) -> bool:
        """True iff ``self.kind`` is one of the supported platforms.

        Useful for downloader dispatch tables: ``unknown`` is the
        catch-all bucket from :mod:`mediascribe.inputs` and typically
        means we should fall back to the generic ``yt-dlp`` path.
        """
        return self.kind in KNOWN_SOURCE_KINDS


@dataclass
class DownloadResult:
    """下载结果"""

    source: SourceRef
    video_path: Path
    title: Optional[str] = None
    webpage_url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class TranscriptResult:
    """转录结果"""

    source: SourceRef
    engine: str
    model: str
    text: str
    audio_path: Path
    transcript_path: Path
    video_path: Optional[Path] = None
    metadata_path: Optional[Path] = None
    metadata: Optional[Dict[str, Any]] = None
    segments: Optional[list] = None
    language: Optional[str] = None
    speaker_diarization: bool = False
