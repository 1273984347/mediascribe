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
- 仅支持"视频笔记"和"图文笔记"（图文会取第一张图作为封面）
- 需要登录的笔记会失败，并打印人工 fallback 提示
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Optional

import requests

from ..config import Settings
from ..models import DownloadResult, SourceRef
from .base import Downloader


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
        url = (source.url or "").lower()
        return any(host in url for host in self.XHS_HOST_PATTERNS)

    def download(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress: Optional[Any] = None,
    ) -> DownloadResult:
        url = source.url
        if not url:
            raise ValueError("需要提供小红书视频 URL")

        print(f"   🔗 目标链接: {url}")

        # 1) 如果是 xhslink 短链接，先展开
        if "xhslink.com" in url:
            expanded = self._resolve_short_url(url)
            if expanded:
                print(f"   ↪️  短链接展开: {expanded}")
                url = expanded
                source = SourceRef(
                    raw_input=source.raw_input,
                    kind="xiaohongshu",
                    url=url,
                )

        # 2) 提取真实媒体 URL
        media_url, title, note_type = self._extract_media_and_meta(url)

        if not media_url:
            raise RuntimeError(
                "无法获取小红书视频的真实媒体 URL。"
                "可能原因：需要登录、地区受限、或笔记是纯图文。"
            )

        # 3) 下载媒体
        print("   📥 开始下载媒体文件...")
        video_path = self._download_media(media_url, settings.downloads_dir)

        if not video_path or not video_path.exists():
            raise RuntimeError(f"下载失败: 找不到文件 {video_path}")

        print(f"   ✅ 下载成功: {video_path.name}")

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
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                },
            )
            if resp.url and resp.url != url:
                return resp.url
        except Exception as e:  # pragma: no cover
            print(f"   ⚠️  短链接展开失败: {e}")
        return None

    def _extract_media_and_meta(
        self, url: str
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        返回 (media_url, title, note_type)；
        note_type: "video" | "image" | "unknown"
        """
        if not self._has_playwright:
            print(
                "   ⚠️  未安装 playwright，无法自动解析小红书媒体 URL。"
                "请运行: pip install playwright && python -m playwright install chromium"
            )
            return None, None, None

        try:
            return self._extract_with_playwright(url)
        except Exception as e:
            print(f"   ⚠️  Playwright 解析失败: {e}")
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

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
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
        """下载真实媒体文件。"""
        save_dir.mkdir(parents=True, exist_ok=True)

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.xiaohongshu.com/",
            "Accept": "*/*",
        }

        try:
            resp = requests.get(
                media_url, headers=headers, stream=True, timeout=60
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"\n   ❌ 下载媒体文件失败: {e}")
            return None

        # 文件后缀
        ext = ".mp4"
        if "sns-img" in media_url or "image" in media_url:
            ext = ".jpg"
        elif ".webm" in media_url:
            ext = ".webm"

        file_name = f"xhs_{int(time.time())}{ext}"
        file_path = save_dir / file_name

        total_size = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        print(
                            f"\r   📥 下载进度: {percent:.1f}% "
                            f"({downloaded}/{total_size} 字节)",
                            end="",
                        )
        print()
        return file_path
