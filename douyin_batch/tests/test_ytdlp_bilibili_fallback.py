"""YtDlpDownloader B 站 API 备用源兜底测试

背景：B 站把音轨全量调度到 P2P CDN（mcdn.bilivideo.cn），大量网络
环境下 yt-dlp 直连超时。download() 失败时应自动回退 playurl API，
从带签名的 base_url / backup_url 列表逐个尝试拉取音轨（转录只需音轨）。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from mediascribe.config import Settings
from mediascribe.downloaders.ytdlp import (
    YtDlpDownloader,
    _parse_bilibili,
    _pick_best_audio,
)
from mediascribe.models import DownloadResult, SourceRef

_AUDIO_BYTES = b"\x00" * (64 * 1024 + 1)  # 恰好超过最小有效阈值


def _make_settings(tmp: str) -> Settings:
    return Settings(workspace_root=Path(tmp))


def _make_source(page: int = 1) -> SourceRef:
    url = f"https://www.bilibili.com/video/BV15ocBzQEJJ?p={page}"
    return SourceRef(raw_input=url, kind="bilibili", bv="BV15ocBzQEJJ", url=url)


def _pagelist_payload():
    return {"code": 0, "data": [{"cid": 36636723551, "part": "p01 马原", "duration": 592}]}


def _playurl_payload():
    return {
        "code": 0,
        "data": {
            "owner": {"name": "徐涛"},
            "dash": {
                "audio": [
                    {
                        "id": 30216,
                        "bandwidth": 66000,
                        "base_url": "https://mcdn-a.example/audio-30216.m4s",
                        "backup_url": [],
                    },
                    {
                        "id": 30280,
                        "bandwidth": 109311,
                        "base_url": "https://mcdn-b.example/audio-30280.m4s",
                        "backup_url": ["https://upos-sz-mirrorcoso1.example/audio-30280.m4s"],
                    },
                ]
            },
        },
    }


def _fake_session(audio_bytes: bytes = _AUDIO_BYTES):
    """构造按序响应的假 requests.Session：pagelist → playurl → 音频流。"""
    session = mock.MagicMock()
    pagelist = mock.MagicMock()
    pagelist.json.return_value = _pagelist_payload()
    playurl = mock.MagicMock()
    playurl.json.return_value = _playurl_payload()
    audio = mock.MagicMock()
    audio.content = audio_bytes
    audio.raise_for_status.return_value = None
    session.get.side_effect = [pagelist, playurl, audio]
    return session


class TestParseBilibili(unittest.TestCase):
    """_parse_bilibili: (bvid, 页码) 提取"""

    def test_full_url_with_page(self):
        bvid, page = _parse_bilibili("https://www.bilibili.com/video/BV15ocBzQEJJ?p=12")
        self.assertEqual(bvid, "BV15ocBzQEJJ")
        self.assertEqual(page, 12)

    def test_url_without_page_defaults_to_p1(self):
        bvid, page = _parse_bilibili("https://www.bilibili.com/video/BV15ocBzQEJJ/")
        self.assertEqual(bvid, "BV15ocBzQEJJ")
        self.assertEqual(page, 1)

    def test_page_zero_clamped_to_one(self):
        _, page = _parse_bilibili("https://www.bilibili.com/video/BV15ocBzQEJJ?p=0")
        self.assertEqual(page, 1)

    def test_non_bilibili_url_returns_none(self):
        bvid, page = _parse_bilibili("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIsNone(bvid)
        self.assertEqual(page, 1)

    def test_none_url_returns_none(self):
        self.assertEqual(_parse_bilibili(None), (None, 1))


class TestPickBestAudio(unittest.TestCase):
    """_pick_best_audio: 选最高码率音轨"""

    def test_picks_highest_bandwidth(self):
        best = _pick_best_audio(_playurl_payload()["data"]["dash"])
        self.assertEqual(best["id"], 30280)

    def test_missing_audio_returns_none(self):
        self.assertIsNone(_pick_best_audio({"audio": []}))
        self.assertIsNone(_pick_best_audio(None))


def _force_ytdlp_failure():
    """强制 yt-dlp 阶段失败（模拟 mcdn P2P 节点超时）。

    必须 mock 掉真实的 YoutubeDL——否则测试结果随网络状态漂移：
    网络好时 yt-dlp 真下载成功、根本走不到 fallback。
    """
    return mock.patch("yt_dlp.YoutubeDL", side_effect=RuntimeError("mcdn timed out"))


class TestBilibiliAudioFallback(unittest.TestCase):
    """download() 的 API 备用源回退链"""

    def test_fallback_downloads_from_backup_url(self):
        """主 URL 挂 → 从 backup_url 拉音轨 → 产出 .m4a + 元数据"""
        session = _fake_session()
        with TemporaryDirectory() as tmp:
            with _force_ytdlp_failure(), mock.patch(
                "requests.Session", return_value=session
            ), mock.patch("shutil.which", return_value=None):
                result = YtDlpDownloader().download(_make_source(), _make_settings(tmp))

            self.assertEqual(result.title, "p01 马原")
            self.assertEqual(result.metadata["duration"], 592)
            self.assertEqual(result.metadata["downloader"], "bilibili-api-audio-fallback")
            self.assertEqual(result.video_path.suffix, ".m4a")
            self.assertTrue(result.video_path.exists())
            self.assertEqual(result.video_path.read_bytes(), _AUDIO_BYTES)
            # base_url 成功即止——共 3 次请求（pagelist/playurl/audio）
            self.assertEqual(session.get.call_count, 3)

    def test_fallback_tries_backup_after_base_fails(self):
        """base_url 抛异常时轮换到 backup_url"""
        session = mock.MagicMock()
        pagelist = mock.MagicMock()
        pagelist.json.return_value = _pagelist_payload()
        playurl = mock.MagicMock()
        playurl.json.return_value = _playurl_payload()
        failed = mock.MagicMock()
        failed.raise_for_status.side_effect = RuntimeError("connect timeout")
        ok = mock.MagicMock()
        ok.content = _AUDIO_BYTES
        ok.raise_for_status.return_value = None
        session.get.side_effect = [pagelist, playurl, failed, ok]
        with TemporaryDirectory() as tmp:
            with _force_ytdlp_failure(), mock.patch(
                "requests.Session", return_value=session
            ), mock.patch("shutil.which", return_value=None):
                result = YtDlpDownloader().download(_make_source(), _make_settings(tmp))
            self.assertTrue(result.video_path.exists())
            self.assertEqual(session.get.call_count, 4)

    def test_audio_only_bilibili_goes_straight_to_api(self):
        """audio_only 模式下 B 站直走 API 音轨, 不再尝试 yt-dlp 下载"""
        session = _fake_session()
        with TemporaryDirectory() as tmp:
            with _force_ytdlp_failure(), mock.patch(
                "requests.Session", return_value=session
            ), mock.patch("shutil.which", return_value=None):
                tr = YtDlpDownloader()
                with mock.patch.object(
                    tr, "_download_bilibili_audio", wraps=tr._download_bilibili_audio
                ) as api:
                    result = tr.download(
                        _make_source(),
                        Settings(workspace_root=Path(tmp), audio_only=True),
                    )
            api.assert_called_once()
            self.assertEqual(result.metadata["downloader"], "bilibili-api-audio-fallback")

    def test_audio_only_api_failure_falls_back_to_ytdlp(self):
        """audio_only 下 API 失败(如充电专属)落回常规 yt-dlp 路径"""
        src = _make_source()
        with TemporaryDirectory() as tmp:
            settings = Settings(workspace_root=Path(tmp), audio_only=True)
            dl = YtDlpDownloader()
            sentinel = DownloadResult(source=src, video_path=Path(tmp) / "v.mp4")
            with mock.patch.object(dl, "_download_bilibili_audio", return_value=None), mock.patch(
                "yt_dlp.YoutubeDL"
            ) as ydl_cls:
                ydl_inst = ydl_cls.return_value.__enter__.return_value
                ydl_inst.extract_info.return_value = {
                    "id": "BV15ocBzQEJJ_p1",
                    "title": "t",
                    "duration": 1,
                    "webpage_url": src.url,
                }
                ydl_inst.sanitize_info.side_effect = lambda x: x
                with mock.patch.object(
                    dl, "_resolve_video_path", return_value=Path(tmp) / "v.mp4"
                ), mock.patch.object(Path, "exists", return_value=True):
                    result = dl.download(src, settings)
            self.assertEqual(result.metadata["id"], "BV15ocBzQEJJ_p1")

    def test_original_error_raised_when_fallback_also_fails(self):
        """API 也挂时，保留 yt-dlp 原始异常"""
        with TemporaryDirectory() as tmp:
            with mock.patch(
                "yt_dlp.YoutubeDL", side_effect=RuntimeError("mcdn timed out")
            ), mock.patch("requests.Session", side_effect=RuntimeError("api down")):
                with self.assertRaises(RuntimeError) as ctx:
                    YtDlpDownloader().download(_make_source(), _make_settings(tmp))
            self.assertIn("mcdn timed out", str(ctx.exception))

    def test_no_fallback_for_non_bilibili(self):
        """非 B 站源失败时不走 API 兜底，直接抛原始异常"""
        src = SourceRef(
            raw_input="https://www.youtube.com/watch?v=x",
            kind="youtube",
            url="https://www.youtube.com/watch?v=x",
        )
        with TemporaryDirectory() as tmp:
            with mock.patch("yt_dlp.YoutubeDL", side_effect=RuntimeError("yt fail")), mock.patch(
                "requests.Session"
            ) as sess_cls:
                with self.assertRaises(RuntimeError):
                    YtDlpDownloader().download(src, _make_settings(tmp))
            sess_cls.assert_not_called()

    def test_no_usable_audio_returns_original_error(self):
        """dash 里没有音轨（如充电专属）→ 返回 None → 抛原始异常"""
        session = mock.MagicMock()
        pagelist = mock.MagicMock()
        pagelist.json.return_value = _pagelist_payload()
        playurl = mock.MagicMock()
        playurl.json.return_value = {"code": 0, "data": {"dash": {}}}
        session.get.side_effect = [pagelist, playurl]
        with TemporaryDirectory() as tmp:
            with mock.patch("yt_dlp.YoutubeDL", side_effect=RuntimeError("net fail")), mock.patch(
                "requests.Session", return_value=session
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    YtDlpDownloader().download(_make_source(), _make_settings(tmp))
            self.assertIn("net fail", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
