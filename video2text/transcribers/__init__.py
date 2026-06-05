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
from .factory import (
    DEFAULT_FALLBACK_CHAIN,
    get_transcriber,
    get_transcriber_with_fallback,
    list_available_engines,
)
from .faster_whisper import FasterWhisperTranscriber
from .whisper import WhisperTranscriber
from .whisperx import WhisperXTranscriber

__all__ = [
    "Transcriber",
    "WhisperTranscriber",
    "WhisperXTranscriber",
    "FasterWhisperTranscriber",
    "ChunkedTranscriber",
    "Chunk",
    "ChunkResult",
    "DEFAULT_CHUNK_SECONDS",
    "DEFAULT_OVERLAP_SECONDS",
    "merge_segments",
    "merge_texts",
    "probe_duration",
    "split_audio",
    "DEFAULT_FALLBACK_CHAIN",
    "get_transcriber",
    "get_transcriber_with_fallback",
    "list_available_engines",
]
