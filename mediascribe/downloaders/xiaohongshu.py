"""
小红书专用下载器 - 使用 Playwright 提取真实媒体 URL

为什么是独立类？
- 小红书视频链接（xhslink 短链接 / explore / discovery/item），
  在服务器直连时只能拿到登录墙，无法直接拿视频。
- Playwright 渲染页面后，从 `window.__INITIAL_STATE__` 或
  `<video src>` / `<source src>` 提取真实媒体 URL。
- 视频托管在 `sns-video-bd.xhscdn.com` / `sns-video-hw.xhscdn.com`。

注意事项
- 必须带 User-Agent、XHS 常见 header（Referer 必须为 xiaohongshu.com）
- 仅支持"视频笔记"；图文笔记（note_type == "image"）无法产出音频，
  会给出明确错误提示（不再把 .jpg 封面误当 video_path 进 ffmpeg）
- 需要登录的笔记会失败，并记录人工 fallback 提示

v3.2.0g:
- ``print`` → ``logging.getLogger(__name__)``，--json 模式下不污染 stdout
- 下载收敛到 ``_http_download.stream_download``（.part 临时文件 + 失败清理）
- 文件名用 ``uuid4().hex[:8]`` 避免同秒碰撞
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import requests

from ..config import Settings
from ..i18n import t
from ..models import DownloadResult, SourceRef
from ..url_utils import host_matches
from ._http_download import CHROME_UA, stream_download
from .base import Downloader

logger = logging.getLogger(__name__)


class XiaohongshuDownloader(Downloader):
    """小红书专用下载器（Playwright 提取真实媒体 URL）"""

    name = "xiaohongshu"

    XHS_HOST_PATTERNS = (
        "xiaohongshu.com",
        "xhslink.com",
    )

    def __init__(self) -> None:
        # Playwright 是可选依赖；缺失时不要让 import 失败
        try:
            import playwright  # noqa: F401

            self._has_playwright = True
        except Exception:  # pragma: no cover - 允许 import 期跳过
            self._has_playwright = False

    # ------------------------------------------------------------------
    # Downloader interface
    # ------------------------------------------------------------------
    def supports(self, source: SourceRef) -> bool:
        if source.kind == "xiaohongshu":
            return True
        return host_matches(source.url or "", self.XHS_HOST_PATTERNS)

    def download(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress: Optional[Any] = None,
    ) -> DownloadResult:
        url = source.url
        if not url:
            raise ValueError(t("XHS_NO_URL"))

        # 入口断言：主机名必须属于小红书系域名，防止子串伪装 URL 把
        # headless 浏览器/重定向跟随引向任意主机（2026-10-29 审查 SEC-003）
        if not host_matches(url, self.XHS_HOST_PATTERNS):
            raise ValueError(
                t(
                    "XHS_FOREIGN_HOST",
                    hosts=" / ".join(self.XHS_HOST_PATTERNS),
                )
            )

        logger.info("目标链接: %s", url)

        # 1) 如果是 xhslink 短链接，先展开
        if host_matches(url, ("xhslink.com",)):
            expanded = self._resolve_short_url(url)
            if expanded:
                logger.info("短链接展开: %s", expanded)
                url = expanded
                source = SourceRef(
                    raw_input=source.raw_input,
                    kind="xiaohongshu",
                    url=url,
                )

        # 2) 提取真实媒体 URL
        media_url, title, note_type = self._extract_media_and_meta(url)

        if not media_url:
            raise RuntimeError(t("XHS_NO_MEDIA"))

        # 3) 图文笔记明确报错（P1-5）：封面图不是视频，继续下载只会把
        #    .jpg 塞给 ffmpeg 报晦涩错误；这里给出可操作的信息。
        if note_type == "image":
            raise RuntimeError(t("XHS_IMAGE_NOTE"))

        # 4) 下载媒体
        logger.info("开始下载媒体文件...")
        video_path = self._download_media(media_url, settings.downloads_dir)

        if not video_path or not video_path.exists():
            raise RuntimeError(t("XHS_DOWNLOAD_MISSING", path=video_path))

        logger.info("下载成功: %s", video_path.name)

        return DownloadResult(
            source=source,
            video_path=video_path,
            title=title or f"xhs_{video_path.stem}",
            webpage_url=url,
            metadata={
                "url": url,
                "media_url": media_url,
                "note_type": note_type,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _resolve_short_url(self, url: str) -> Optional[str]:
        """解析 xhslink.com 短链接到真实笔记 URL"""
        try:
            resp = requests.head(
                url,
                allow_redirects=True,
                timeout=15,
                headers={
                    "User-Agent": CHROME_UA,
                },
            )
            if resp.url and resp.url != url:
                # requests 无类型存根（未装 types-requests），resp.url 运行时为 str
                real: str = resp.url
                return real
        except Exception as e:  # pragma: no cover
            logger.warning("短链接展开失败: %s (%s)", url, e)
        return None

    def _extract_media_and_meta(
        self, url: str
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        返回 (media_url, title, note_type)；
        note_type: "video" | "image" | "unknown"
        """
        if not self._has_playwright:
            logger.warning(
                "未安装 playwright，无法自动解析小红书媒体 URL。"
                "请运行: pip install playwright && python -m playwright install chromium"
            )
            return None, None, None

        try:
            return self._extract_with_playwright(url)
        except Exception as e:
            logger.warning("Playwright 解析失败: %s (%s)", url, e)
            return None, None, None

    def _extract_with_playwright(
        self, url: str
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        使用 Playwright 启动 headless Chromium，访问笔记页面并提取媒体 URL。
        """
        from playwright.sync_api import sync_playwright  # type: ignore

        media_url: Optional[str] = None
        title: Optional[str] = None
        note_type: Optional[str] = None

        # 短链展开后的最终地址仍须落在小红书系域名上才允许导航
        if not host_matches(url, self.XHS_HOST_PATTERNS):
            logger.warning("展开后的地址不在小红书系域名，拒绝导航: %s", url)
            return None, None, None

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=CHROME_UA,
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # 等待可能的视频元素出现
                try:
                    page.wait_for_selector(
                        "video source, video[src], video",
                        timeout=8000,
                    )
                except Exception:
                    pass
                # 抓取 HTML 以便离线分析
                html = page.content()
                title = page.title() or None

                # 1) <video src> / <source src>
                media_url = page.evaluate(
                    """
                    () => {
                        const v = document.querySelector('video');
                        if (!v) return null;
                        if (v.currentSrc) return v.currentSrc;
                        if (v.src) return v.src;
                        const s = v.querySelector('source');
                        if (s && s.src) return s.src;
                        return null;
                    }
                    """
                )
                if media_url:
                    note_type = "video"

                # 2) window.__INITIAL_STATE__ 兜底
                if not media_url:
                    state_json = page.evaluate(
                        """
                        () => {
                            try {
                                return JSON.stringify(window.__INITIAL_STATE__);
                            } catch (e) { return null; }
                        }
                        """
                    )
                    if state_json:
                        media_url = self._grep_video_url(state_json)
                        if media_url:
                            note_type = "video"

                # 3) HTML 正则兜底（覆盖 sns-video-*.xhscdn.com）
                if not media_url:
                    media_url = self._grep_video_url(html)
                    if media_url:
                        note_type = "video"

                # 4) 图文笔记：取首张 sns-img
                if not media_url:
                    img = page.evaluate(
                        """
                        () => {
                            const i = document.querySelector('img');
                            return i ? (i.currentSrc || i.src) : null;
                        }
                        """
                    )
                    if img and "xhscdn" in img:
                        media_url = img
                        note_type = "image"
            finally:
                context.close()
                browser.close()

        return media_url, title, note_type

    @staticmethod
    def _grep_video_url(text: str) -> Optional[str]:
        """从文本中匹配小红书视频/图片 CDN 真实 URL。"""
        if not text:
            return None
        # 视频 CDN 域名
        patterns = [
            r"https?://sns-video-[a-z]+\.xhscdn\.com/[^'\"\s]+",
            r"https?://sns-img-[a-z]+\.xhscdn\.com/[^'\"\s]+",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                # 去掉尾部转义符
                return m.group(0).rstrip("\\").rstrip('"').rstrip("'")
        return None

    def _download_media(self, media_url: str, save_dir: Path) -> Optional[Path]:
        """下载真实媒体文件。

        v3.2.0g: 委托公共 ``stream_download``（``.part`` 临时文件 +
        失败清理），文件名用 uuid 短后缀避免同秒碰撞。
        """
        headers = {
            "User-Agent": CHROME_UA,
            "Referer": "https://www.xiaohongshu.com/",
            "Accept": "*/*",
        }

        # 文件后缀
        ext = ".mp4"
        if "sns-img" in media_url or "image" in media_url:
            ext = ".jpg"
        elif ".webm" in media_url:
            ext = ".webm"

        save_dir.mkdir(parents=True, exist_ok=True)
        file_path = save_dir / f"xhs_{uuid.uuid4().hex[:8]}{ext}"
        try:
            return stream_download(media_url, file_path, headers=headers, timeout=60)
        except Exception as e:
            logger.warning("下载媒体文件失败: %s (%s)", media_url, e)
            return None
