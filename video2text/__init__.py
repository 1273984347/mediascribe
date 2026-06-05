"""
Video2Text - 视频转文字工具
深度整合 yt-dlp、bili2text、WhisperX、whisper.cpp
"""
from .config import Settings
from .downloaders import DouyinDownloader, Downloader, YtDlpDownloader
from .models import DownloadResult, SourceRef, TranscriptResult
from .pipeline import Pipeline
from .transcribers import (
    FasterWhisperTranscriber,
    Transcriber,
    WhisperTranscriber,
    WhisperXTranscriber,
)

__version__ = "3.1.0"
__all__ = [
    "Settings",
    "SourceRef",
    "DownloadResult",
    "TranscriptResult",
    "Pipeline",
    "Downloader",
    "YtDlpDownloader",
    "DouyinDownloader",
    "Transcriber",
    "WhisperTranscriber",
    "WhisperXTranscriber",
    "FasterWhisperTranscriber",
]
