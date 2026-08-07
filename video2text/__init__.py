"""
Video2Text - 视频转文字工具
深度整合 yt-dlp、bili2text、WhisperX、whisper.cpp
"""
from .config import Settings
from .downloaders import DouyinDownloader, Downloader, YtDlpDownloader
from .learn import (
    clear_learned_terms,
    compare,
    confirm_term,
    export_terms,
    get_learned_terms,
    get_prompt_terms,
    import_terms,
    learn,
    learn_from_edit,
    remove_term,
)
from .models import DownloadResult, SourceRef, TranscriptResult
from .pipeline import Pipeline, gpu_health, resolve_device
from .pipeline_async import AsyncPipeline, from_sync
from .post_process import (
    auto_select_model,
    get_prompt_template,
    post_process_transcript,
    setup_hf_mirror,
)
from .transcribers import (
    FasterWhisperTranscriber,
    Transcriber,
    WhisperTranscriber,
    WhisperXTranscriber,
)

__version__ = "3.2.0a"
__all__ = [
    "Settings",
    "SourceRef",
    "DownloadResult",
    "TranscriptResult",
    "Pipeline",
    "AsyncPipeline",
    "from_sync",
    "resolve_device",
    "gpu_health",
    "Downloader",
    "YtDlpDownloader",
    "DouyinDownloader",
    "Transcriber",
    "WhisperTranscriber",
    "WhisperXTranscriber",
    "FasterWhisperTranscriber",
    "post_process_transcript",
    "auto_select_model",
    "get_prompt_template",
    "setup_hf_mirror",
    "compare",
    "learn",
    "learn_from_edit",
    "get_learned_terms",
    "get_prompt_terms",
    "confirm_term",
    "remove_term",
    "export_terms",
    "import_terms",
    "clear_learned_terms",
]
