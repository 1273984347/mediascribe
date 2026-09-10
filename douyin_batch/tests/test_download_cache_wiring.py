"""
P2-5 — PersistentDownloadCache 接入 DownloadStage 的最小单测。

覆盖:

1. ``MEDIASCRIBE_DOWNLOAD_CACHE`` 未设(默认)→ 行为与旧版一致,
   每次都走 downloader,不查不写缓存。
2. ``MEDIASCRIBE_DOWNLOAD_CACHE=1`` → 未命中时下载并回写缓存。
3. ``=1`` 且 URL 已缓存 → 直接用缓存文件,downloader 不被调用。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mediascribe.config import Settings
from mediascribe.models import DownloadResult, SourceRef
from mediascribe.pipeline_stages import (
    DownloadStage,
    PipelineContext,
    _get_download_cache,
    _reset_download_cache,
)


def _make_source(url: str) -> SourceRef:
    return SourceRef(raw_input=url, kind="youtube", url=url, path=None, bv=None)


class TestDownloadCacheWiring(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="v2t_dlcache_wiring_"))
        self._media = self._tmp / "media"
        self._media.mkdir(parents=True, exist_ok=True)
        self._media_file = self._media / "video.mp4"
        self._media_file.write_bytes(b"fake video bytes")
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("MEDIASCRIBE_DOWNLOAD_CACHE", "MEDIASCRIBE_CACHE_DIR")
        }
        os.environ["MEDIASCRIBE_CACHE_DIR"] = str(self._tmp / "cache")
        os.environ.pop("MEDIASCRIBE_DOWNLOAD_CACHE", None)
        _reset_download_cache()

    def tearDown(self) -> None:
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_download_cache()

    def _fake_downloader(self, calls: list):
        dl = mock.MagicMock(name="Downloader")

        def _download(source, settings, **kw):
            calls.append(source.url)
            return DownloadResult(
                source=source,
                video_path=self._media_file,
                title="T",
                metadata={},
            )

        dl.download.side_effect = _download
        return dl

    def _ctx(self) -> PipelineContext:
        ctx = PipelineContext(
            settings=Settings(workspace_root=self._tmp),
            source_input="x",
        )
        ctx.source = _make_source("https://example.com/v/1")
        return ctx

    def test_disabled_by_default_downloads_every_time(self):
        calls: list = []
        stage = DownloadStage(downloader=self._fake_downloader(calls))
        self.assertIsNone(_get_download_cache())
        ctx = stage.run(self._ctx())
        self.assertEqual(calls, ["https://example.com/v/1"])
        self.assertEqual(ctx.video_path, self._media_file)

    def test_enabled_miss_downloads_and_puts(self):
        os.environ["MEDIASCRIBE_DOWNLOAD_CACHE"] = "1"
        calls: list = []
        stage = DownloadStage(downloader=self._fake_downloader(calls))
        ctx = stage.run(self._ctx())
        # 下载被调用,ctx 仍指向原始下载文件(行为不变)
        self.assertEqual(calls, ["https://example.com/v/1"])
        self.assertEqual(ctx.video_path, self._media_file)
        # 缓存已写入
        cache = _get_download_cache()
        self.assertIsNotNone(cache)
        cached = cache.get("https://example.com/v/1")
        self.assertIsNotNone(cached)
        self.assertTrue(cached.exists())

    def test_enabled_hit_skips_download(self):
        os.environ["MEDIASCRIBE_DOWNLOAD_CACHE"] = "1"
        # 预热缓存
        first = DownloadStage(downloader=self._fake_downloader([]))
        first.run(self._ctx())
        calls: list = []
        second = DownloadStage(downloader=self._fake_downloader(calls))
        ctx = second.run(self._ctx())
        # 第二次下载不再被调用,video_path 指向缓存副本
        self.assertEqual(calls, [])
        self.assertEqual(ctx.base_name, ctx.source.display_name)
        cache = _get_download_cache()
        cached = cache.get("https://example.com/v/1")
        self.assertEqual(ctx.video_path, cached)
        self.assertIsNotNone(ctx.downloaded)
        self.assertEqual(ctx.downloaded.metadata.get("download_cache"), "hit")


if __name__ == "__main__":
    unittest.main()
