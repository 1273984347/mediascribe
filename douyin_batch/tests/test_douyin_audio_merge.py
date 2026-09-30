"""Regression tests for Douyin DASH audio/video separation + merge.

Covers the v3.2.0f fix for silent downloads: Douyin serves video and
audio as separate CDN streams, so the downloader must merge them.
"""

import subprocess
from pathlib import Path
from unittest import mock

from mediascribe.downloaders.douyin import DouyinDownloader


def test_separate_streams_splits_video_and_audio():
    urls = [
        "https://v3-dy.douyinvod.com/x/video/v.mp4?a=1",
        "https://v3-dy.douyinvod.com/x/media-audio/a.m4a?a=1",
        "https://v3-dy.douyinvod.com/x/audio/y.m4a",
    ]
    videos, audios = DouyinDownloader._separate_streams(urls)
    assert len(videos) == 1
    assert len(audios) == 2


def test_pick_best_urls_returns_video_and_audio():
    urls = [
        "https://x.douyinvod.com/v.mp4",
        "https://x.douyinvod.com/media-audio/a.m4a",
    ]
    video, audio = DouyinDownloader._pick_best_urls(urls)
    assert video.endswith(".mp4")
    assert audio is not None and "media-audio" in audio


def test_pick_best_urls_no_audio_returns_none():
    urls = ["https://x.douyinvod.com/v.mp4"]
    video, audio = DouyinDownloader._pick_best_urls(urls)
    assert video.endswith(".mp4")
    assert audio is None


def test_derive_title_prefers_video_id_from_url():
    # 回归(2026-10-01): 旧实现 title=f"douyin_{stem}" 在下载文件名已带
    # 前缀时产出 douyin_douyin_<hash>_merged; 现优先取 URL 数字 ID。
    title = DouyinDownloader._derive_title(
        "https://www.douyin.com/video/7691255314397646080?previous_page=web_code_link",
        Path("downloads/douyin_90635b90_merged.mp4"),
    )
    assert title == "douyin_7691255314397646080"


def test_derive_title_falls_back_to_download_stem():
    # 无 ID 的直链: 复用已带前缀的文件名, 不再叠加 douyin_
    assert (
        DouyinDownloader._derive_title(
            "https://v3-dy.douyinvod.com/x/v.mp4?a=1", Path("downloads/douyin_90635b90_merged.mp4")
        )
        == "douyin_90635b90_merged"
    )
    assert (
        DouyinDownloader._derive_title(
            "https://x.douyinvod.com/v.mp4", Path("downloads/raw_media.mp4")
        )
        == "douyin_raw_media"
    )


class _StubDownloader(DouyinDownloader):
    """Avoid real network: drop a fake audio file on disk."""

    def _download_media(self, media_url, save_dir, suffix=".mp4"):
        path = Path(save_dir) / f"stub_audio{suffix}"
        path.write_bytes(b"\x00\x00")
        return path


def test_merge_av_success(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"VID")
    out = tmp_path / "merged.mp4"
    dl = _StubDownloader()

    def _fake_run(cmd, *a, **k):
        # ffmpeg 合并成功 → 写出合并文件（真实 ffmpeg 的行为）。
        Path(cmd[-1]).write_bytes(b"MERGED")
        return subprocess.CompletedProcess(cmd, 0)

    with mock.patch("subprocess.run", side_effect=_fake_run), mock.patch(
        "shutil.which", return_value="ffmpeg"
    ):
        ok = dl._merge_av(video, "http://audio", out, tmp_path)
    assert ok is True
    assert out.exists()


def test_merge_av_ffmpeg_failure_returns_false(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"VID")
    out = tmp_path / "merged.mp4"
    dl = _StubDownloader()
    with mock.patch(
        "subprocess.run", side_effect=subprocess.CalledProcessError(1, "ffmpeg")
    ), mock.patch("shutil.which", return_value="ffmpeg"):
        ok = dl._merge_av(video, "http://audio", out, tmp_path)
    assert ok is False
    assert not out.exists()
