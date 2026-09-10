"""
抖音专用下载器 - 使用浏览器自动化获取真实媒体 URL
不需要 cookies！

v3.2.0e 优化：
- ``requests`` 加 ``HTTPAdapter`` 自动重试（connect/read 共 3 次，backoff 1s）
- Playwright 解析失败时自动重试 2 次（共 3 次尝试）
- 失败的部分下载文件 ``try/finally`` 清理
- 文件名用 ``uuid4().hex[:8]`` 避免同秒碰撞
- ``print`` → ``logger``，日志可分级收集

v3.2.0g: 下载逻辑收敛到 ``_http_download.stream_download``（与
wechat_mp / xiaohongshu 共享同一实现：.part 临时文件 + 失败清理 + 重试）。
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..models import DownloadResult, SourceRef
from ._http_download import build_http_session as _build_http_session
from ._http_download import stream_download
from .base import Downloader

logger = logging.getLogger(__name__)

_PLAYWRIGHT_MAX_ATTEMPTS = 3
_PLAYWRIGHT_WAIT_MS = 5000

# 兼容别名：旧代码/测试通过 ``douyin._build_session`` 构造带重试的 session
_build_session = _build_http_session


class DouyinDownloader(Downloader):
    """抖音专用下载器"""

    name = "douyin"

    def __init__(self):
        logger.info("抖音下载器初始化完成（无需 cookies）")

    def download(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress: Optional[Any] = None,
    ) -> DownloadResult:
        url = source.url
        if not url:
            raise ValueError("需要提供抖音视频 URL")

        logger.info("目标链接: %s", url)

        # 首先检查是否是直接的 douyinvod 媒体链接
        audio_url: Optional[str] = None
        if "douyinvod.com" in url:
            logger.info("检测到直接媒体链接")
            media_url = url
        else:
            # 尝试使用浏览器工具获取真实媒体 URL
            logger.info("使用浏览器工具解析抖音视频...")
            media_url, audio_url = self._extract_media_url_with_browser(url)

        if not media_url:
            logger.warning("无法自动获取媒体 URL")
            logger.warning(
                "手动方案: 浏览器 F12 → Network → 播放视频 → "
                "找 douyinvod.com 请求 → 复制 URL 直接作为输入"
            )
            raise RuntimeError("无法获取抖音视频的真实媒体 URL，请尝试手动获取")

        # 下载媒体文件
        logger.info("开始下载媒体文件...")
        video_path = self._download_media(media_url, settings.downloads_dir)

        if not video_path or not video_path.exists():
            raise RuntimeError(f"下载失败: 找不到文件 {video_path}")

        logger.info("下载成功: %s", video_path.name)

        # DASH 音视频分离：若抓到独立音频流，合并音视频
        if audio_url:
            logger.info("检测到独立音频流，下载并合并音视频...")
            merged_path = video_path.with_name(f"{video_path.stem}_merged.mp4")
            if self._merge_av(video_path, audio_url, merged_path, settings.downloads_dir):
                # 清理合并前的纯视频流中间文件
                if video_path.exists() and video_path != merged_path:
                    try:
                        video_path.unlink()
                    except OSError:
                        pass
                video_path = merged_path
                logger.info("音视频合并完成: %s", video_path.name)
            else:
                logger.warning("音视频合并失败，回退使用纯视频流（可能无声）")

        return DownloadResult(
            source=source,
            video_path=video_path,
            title=f"douyin_{video_path.stem}",
            webpage_url=url,
            metadata={
                "url": url,
                "media_url": media_url,
            },
        )

    def _extract_media_url_with_browser(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """使用 Playwright 浏览器自动化提取真实媒体 URL。

        v3.2.0e: Playwright 解析失败时重试最多 ``_PLAYWRIGHT_MAX_ATTEMPTS`` 次。
        每次失败后等待 1s 再试，避免抖音限流瞬时阻塞。

        策略:
        1. 启动 headless Chromium
        2. 拦截所有 ``douyinvod.com`` 响应 URL
        3. 等待 DOMContentLoaded + 5s 额外时间
        4. 取第一个匹配的 douyinvod 链接

        返回 ``(media_url, audio_url)``；如果 playwright 未安装或解析失败，
        返回 ``(None, None)``（调用方可回退到 yt-dlp）。
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright 未安装,无法使用浏览器自动化")
            logger.warning("安装: pip install playwright && playwright install chromium")
            return (None, None)

        last_error: Optional[Exception] = None
        for attempt in range(1, _PLAYWRIGHT_MAX_ATTEMPTS + 1):
            try:
                media_urls = self._try_playwright_capture(sync_playwright, url)
                if media_urls:
                    return self._pick_best_urls(media_urls)
                logger.warning(
                    "Playwright 尝试 %d/%d 未捕获到 douyinvod 媒体链接",
                    attempt,
                    _PLAYWRIGHT_MAX_ATTEMPTS,
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    "Playwright 尝试 %d/%d 失败: %r",
                    attempt,
                    _PLAYWRIGHT_MAX_ATTEMPTS,
                    e,
                )
            if attempt < _PLAYWRIGHT_MAX_ATTEMPTS:
                time.sleep(1.0)

        if last_error:
            logger.warning("Playwright 全部 %d 次尝试均失败", _PLAYWRIGHT_MAX_ATTEMPTS)
        return (None, None)

    @staticmethod
    def _try_playwright_capture(sync_playwright, url: str) -> list[str]:
        """单次 Playwright 抓取尝试，返回捕获的 douyinvod URL 列表。"""
        media_urls: list[str] = []

        def handle_response(response):
            rurl = response.url
            if "douyinvod.com" in rurl and (".mp4" in rurl or ".m3u8" in rurl or "video" in rurl):
                media_urls.append(rurl)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    locale="zh-CN",
                )
                page = context.new_page()
                page.on("response", handle_response)
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(_PLAYWRIGHT_WAIT_MS)
            finally:
                browser.close()

        # 保序去重
        return list(dict.fromkeys(media_urls))

    @staticmethod
    def _separate_streams(urls: list[str]) -> tuple[list[str], list[str]]:
        """把捕获到的 douyinvod URL 分为视频流与音频流。

        抖音 DASH 会把视频、音频拆成独立 CDN：音频流 URL 通常含
        ``media-audio`` / ``/audio`` / ``.m4a`` 关键字；其余视为视频流。
        """
        video_urls: list[str] = []
        audio_urls: list[str] = []
        for u in urls:
            low = u.lower().split("?")[0]
            if "media-audio" in low or "/audio" in low or low.endswith(".m4a"):
                audio_urls.append(u)
            else:
                video_urls.append(u)
        return video_urls, audio_urls

    @staticmethod
    def _pick_best_urls(urls: list[str]) -> tuple[Optional[str], Optional[str]]:
        """返回 ``(最佳视频流, 最佳音频流或 None)``。

        v3.2.0f: 区分视频/音频流，DASH 分离时返回独立音频流，
        由调用方负责合并，避免下载到无声视频。
        """
        video_urls, audio_urls = DouyinDownloader._separate_streams(urls)
        video: Optional[str] = None
        for u in video_urls:
            if ".mp4" in u:
                video = u
                break
        if video is None:
            for u in video_urls:
                if ".m3u8" in u:
                    video = u
                    break
        if video is None and video_urls:
            video = video_urls[0]
        elif video is None and not video_urls and urls:
            # 兜底：没有任何可识别视频流时退回原始列表第一条
            video = urls[0]
        audio = audio_urls[0] if audio_urls else None
        return video, audio

    def _download_media(
        self, media_url: str, save_dir: Path, suffix: str = ".mp4"
    ) -> Optional[Path]:
        """下载真实媒体文件。

        v3.2.0g: 委托公共 ``stream_download``（带 ``HTTPAdapter`` 重试的
        session、``.part`` 临时文件、失败清理），与其他下载器共享实现。
        v3.2.0f: 支持 ``suffix`` 参数以下载音频流等非 mp4 资源。
        """
        save_dir.mkdir(parents=True, exist_ok=True)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.douyin.com/",
        }

        # 用 uuid4 防止同秒下载文件名碰撞
        file_name = f"douyin_{uuid.uuid4().hex[:8]}{suffix}"
        file_path = save_dir / file_name

        try:
            return stream_download(
                media_url,
                file_path,
                session=_build_http_session(),
                headers=headers,
                timeout=(10, 60),
            )
        except Exception as e:
            logger.warning("下载媒体文件失败: %r", e)
            return None

    def _merge_av(
        self,
        video_path: Path,
        audio_url: str,
        out_path: Path,
        save_dir: Path,
    ) -> bool:
        """下载独立音频流并与视频流无损合并（ffmpeg -c copy）。

        v3.2.0f: 解决抖音 DASH 音视频分离导致的无声视频问题。
        成功返回 True 并把合并结果写到 ``out_path``；失败清理临时文件并返回 False。
        """
        import shutil
        import subprocess

        audio_path = self._download_media(audio_url, save_dir, suffix=".m4a")
        if not audio_path or not audio_path.exists():
            logger.warning("音频流下载失败，无法合并")
            return False

        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-c",
            "copy",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            str(out_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        except Exception as e:  # 合并失败不应中断主流程
            # P2-12: CalledProcessError 时输出 ffmpeg stderr 末尾，方便定位
            stderr_tail = ""
            if hasattr(e, "stderr") and e.stderr:
                stderr_tail = (
                    e.stderr.decode("utf-8", errors="replace")
                    if isinstance(e.stderr, bytes)
                    else str(e.stderr)
                )[-2000:]
            logger.warning(
                "ffmpeg 合并音视频失败: %r%s",
                e,
                f"\nffmpeg stderr 末尾:\n{stderr_tail}" if stderr_tail else "",
            )
            if out_path.exists():
                try:
                    out_path.unlink()
                except OSError:
                    pass
            return False
        finally:
            # 清理临时音频文件
            if audio_path.exists():
                try:
                    audio_path.unlink()
                except OSError:
                    pass

        if not out_path.exists() or out_path.stat().st_size == 0:
            return False
        return True
