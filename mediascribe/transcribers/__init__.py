"""
转录器模块
"""

from .base import Transcriber
from .chunked import (
    DEFAULT_CHUNK_SECONDS,
    DEFAULT_OVERLAP_SECONDS,
    Chunk,
    ChunkedTranscriber,
    ChunkResult,
    merge_segments,
    merge_texts,
    probe_duration,
    split_audio,
)
from .factory import get_transcriber
from .faster_whisper import FasterWhisperTranscriber
from .whisper import WhisperTranscriber
from .whisper_cpp import WhisperCppTranscriber
from .whisperx import WhisperXTranscriber

__all__ = [
    "Transcriber",
    "WhisperTranscriber",
    "WhisperXTranscriber",
    "FasterWhisperTranscriber",
    "WhisperCppTranscriber",
    "ChunkedTranscriber",
    "Chunk",
    "ChunkResult",
    "DEFAULT_CHUNK_SECONDS",
    "DEFAULT_OVERLAP_SECONDS",
    "merge_segments",
    "merge_texts",
    "probe_duration",
    "split_audio",
    "get_transcriber",
]
