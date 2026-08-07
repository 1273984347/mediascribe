"""
抖音专用下载器 - 使用浏览器自动化获取真实媒体 URL
不需要 cookies！

v3.2.0e 优化：
- ``requests`` 加 ``HTTPAdapter`` 自动重试（connect/read 共 3 次，backoff 1s）
- Playwright 解析失败时自动重试 2 次（共 3 次尝试）
- 失败的部分下载文件 ``try/finally`` 清理
- 文件名用 ``uuid4().hex[:8]`` 避免同秒碰撞
- ``print`` → ``logger``，日志可分级收集
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter

from ..config import Settings
from ..models import DownloadResult, SourceRef
from .base import Downloader

logger = logging.getLogger(__name__)

# 重试配置
_HTTP_MAX_RETRIES = 3
_HTTP_BACKOFF_FACTOR = 1.0  # 1s, 2s, 4s
_PLAYWRIGHT_MAX_ATTEMPTS = 3
_PLAYWRIGHT_WAIT_MS = 5000


def _build_session() -> requests.Session:
    """构造带重试的 ``requests.Session``。

    重试条件：
    - 连接错误（ConnectTimeout / ConnectionError）
    - 读取超时（ReadTimeout）
    - 5xx 响应
    不重试：4xx（除 429 由 urllib3 自动处理）
    """
    session = requests.Session()
    try:
        from urllib3.util.retry import Retry
        retry = Retry(
            total=_HTTP_MAX_RETRIES,
            connect=_HTTP_MAX_RETRIES,
            read=_HTTP_MAX_RETRIES,
            backoff_factor=_HTTP_BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retry)
    except ImportError:  # pragma: no cover - urllib3 总是随 requests 安装
        adapter = HTTPAdapter()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


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
        if "douyinvod.com" in url:
            logger.info("检测到直接媒体链接")
            media_url = url
        else:
            # 尝试使用浏览器工具获取真实媒体 URL
            logger.info("使用浏览器工具解析抖音视频...")
            media_url = self._extract_media_url_with_browser(url)

        if not media_url:
            logger.warning("无法自动获取媒体 URL")
            logger.warning("手动方案: 浏览器 F12 → Network → 播放视频 → "
                           "找 douyinvod.com 请求 → 复制 URL 直接作为输入")
            raise RuntimeError("无法获取抖音视频的真实媒体 URL，请尝试手动获取")

        # 下载媒体文件
        logger.info("开始下载媒体文件...")
        video_path = self._download_media(media_url, settings.downloads_dir)

        if not video_path or not video_path.exists():
            raise RuntimeError(f"下载失败: 找不到文件 {video_path}")

        logger.info("下载成功: %s", video_path.name)

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

    def _extract_media_url_with_browser(self, url: str) -> Optional[str]:
        """使用 Playwright 浏览器自动化提取真实媒体 URL。

        v3.2.0e: Playwright 解析失败时重试最多 ``_PLAYWRIGHT_MAX_ATTEMPTS`` 次。
        每次失败后等待 1s 再试，避免抖音限流瞬时阻塞。

        策略:
        1. 启动 headless Chromium
        2. 拦截所有 ``douyinvod.com`` 响应 URL
        3. 等待 DOMContentLoaded + 5s 额外时间
        4. 取第一个匹配的 douyinvod 链接

        如果 playwright 未安装或解析失败,返回 None (调用方可回退到 yt-dlp)。
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright 未安装,无法使用浏览器自动化")
            logger.warning("安装: pip install playwright && playwright install chromium")
            return None

        last_error: Optional[Exception] = None
        for attempt in range(1, _PLAYWRIGHT_MAX_ATTEMPTS + 1):
            try:
                media_urls = self._try_playwright_capture(sync_playwright, url)
                if media_urls:
                    return self._pick_best_url(media_urls)
                logger.warning(
                    "Playwright 尝试 %d/%d 未捕获到 douyinvod 媒体链接",
                    attempt, _PLAYWRIGHT_MAX_ATTEMPTS,
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    "Playwright 尝试 %d/%d 失败: %r",
                    attempt, _PLAYWRIGHT_MAX_ATTEMPTS, e,
                )
            if attempt < _PLAYWRIGHT_MAX_ATTEMPTS:
                time.sleep(1.0)

        if last_error:
            logger.warning("Playwright 全部 %d 次尝试均失败", _PLAYWRIGHT_MAX_ATTEMPTS)
        return None

    @staticmethod
    def _try_playwright_capture(sync_playwright, url: str) -> list[str]:
        """单次 Playwright 抓取尝试，返回捕获的 douyinvod URL 列表。"""
        media_urls: list[str] = []

        def handle_response(response):
            rurl = response.url
            if "douyinvod.com" in rurl and (
                ".mp4" in rurl or ".m3u8" in rurl or "video" in rurl
            ):
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
    def _pick_best_url(urls: list[str]) -> Optional[str]:
        """优先 .mp4 > .m3u8 > 第一个。"""
        for u in urls:
            if ".mp4" in u:
                return u
        for u in urls:
            if ".m3u8" in u:
                return u
        return urls[0] if urls else None

    def _download_media(self, media_url: str, save_dir: Path) -> Optional[Path]:
        """下载真实媒体文件。

        v3.2.0e: 复用 ``_build_session()`` 拿到带重试的 session，
        失败时 ``try/finally`` 清理 ``.part`` 文件。
        """
        save_dir.mkdir(parents=True, exist_ok=True)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.douyin.com/",
        }

        # 用 uuid4 防止同秒下载文件名碰撞
        file_name = f"douyin_{uuid.uuid4().hex[:8]}.mp4"
        file_path = save_dir / file_name
        session = _build_session()

        try:
            response = session.get(
                media_url, headers=headers, stream=True, timeout=(10, 60)
            )
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            logger.info(
                                "下载进度: %.1f%% (%d/%d 字节)",
                                percent, downloaded, total_size,
                            )

            return file_path

        except Exception as e:
            logger.warning("下载媒体文件失败: %r", e)
            # 清理失败的部分下载文件
            if file_path.exists():
                try:
                    file_path.unlink()
                except OSError:
                    pass
            return None
