"""
Example plugin: a no-op downloader used in the plugin-system tests.

This plugin is registered via the ``video2text.downloaders`` entry
point in ``pyproject.toml`` so the test suite can discover it
without manual imports.  It pretends to download a ``vimeo.com``
URL and returns a stub ``DownloadResult``.
"""
from __future__ import annotations

from typing import Any, Optional

from video2text.downloaders.base import Downloader
from video2text.models import DownloadResult, SourceRef


class VimeoDownloader(Downloader):
    """A minimal example downloader for the plugin-system test."""

    name = "vimeo"

    def supports(self, source: SourceRef) -> bool:
        if source.kind == "vimeo":
            return True
        url = (source.url or "").lower()
        return "vimeo.com" in url

    def download(
        self,
        source: SourceRef,
        settings: Any,
        *,
        progress: Optional[Any] = None,
    ) -> DownloadResult:
        out = settings.downloads_dir / f"{source.display_name}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            f"# Vimeo stub\n\nURL: {source.url}\n", encoding="utf-8"
        )
        return DownloadResult(
            source=source,
            video_path=out,
            title=f"vimeo_{source.display_name}",
            webpage_url=source.url,
            metadata={"kind": "vimeo_stub"},
        )
