from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mediascribe.config import Settings
from mediascribe.downloaders.ytdlp import YtDlpDownloader
from mediascribe.pipeline_stages import DownloadStage, PipelineContext


def test_ytdlp_progress_hook_calls_normalized_callback():
    downloader = object.__new__(YtDlpDownloader)
    source = SimpleNamespace(
        raw_input="https://example.test/v", kind="youtube", url="https://example.test/v"
    )
    settings = SimpleNamespace(audio_only=False)
    seen = []
    downloader._build_ydl_opts = lambda source, settings: {}
    options = downloader._build_ydl_opts_with_progress(
        source, settings, lambda done, total: seen.append((done, total))
    )
    options["progress_hooks"][0](
        {"status": "downloading", "downloaded_bytes": 25, "total_bytes": 100}
    )
    assert seen == [(25, 100)]


def test_ytdlp_progress_hook_ignores_estimated_total():
    downloader = object.__new__(YtDlpDownloader)
    source = SimpleNamespace(
        raw_input="https://example.test/v", kind="youtube", url="https://example.test/v"
    )
    settings = SimpleNamespace(audio_only=False)
    seen = []
    downloader._build_ydl_opts = lambda source, settings: {}
    options = downloader._build_ydl_opts_with_progress(
        source, settings, lambda done, total: seen.append((done, total))
    )
    options["progress_hooks"][0](
        {"status": "downloading", "downloaded_bytes": 25, "total_bytes_estimate": 100}
    )
    assert seen == []


def test_download_stage_emits_measured_percent(tmp_path: Path):
    settings = Settings(workspace_root=tmp_path)
    seen = []
    media = tmp_path / "media.mp4"
    media.write_bytes(b"x")

    class Downloader:
        def download(self, source, settings, *, progress=None):
            progress(25, 100)
            return SimpleNamespace(source=source, video_path=media, title="t", metadata={})

    source = SimpleNamespace(
        kind="youtube", url="https://example.test/v", path=None, display_name="v"
    )
    ctx = PipelineContext(
        settings=settings,
        source_input="u",
        progress_cb=lambda stage, pct: seen.append((stage, pct)),
    )
    ctx.source = source
    DownloadStage(Downloader()).run(ctx)
    assert ("download", 25.0) in seen
