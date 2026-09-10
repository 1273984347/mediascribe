"""
yt-dlp 下载器 - 真正参考 bili2text 的实现

v3.2.0g:
- ``print`` → ``logging.getLogger(__name__)``，--json 模式下不污染 stdout
- 只调用一次 ``extract_info(download=True)``，标题从下载结果取
  （原来先 ``extract_info(download=False)`` 预取一遍，同样信息请求两次）
- 进度钩子按 5% 分桶节流，避免逐 chunk 刷屏
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..models import DownloadResult, SourceRef
from .base import Downloader

logger = logging.getLogger(__name__)


class YtDlpDownloader(Downloader):
    """yt-dlp 下载器"""

    name = "yt-dlp"

    def __init__(self):
        self._ydl = None

    def download(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress: Optional[Any] = None,
    ) -> DownloadResult:
        try:
            import yt_dlp
        except ImportError as e:
            raise RuntimeError("yt-dlp 未安装，请运行: pip install yt-dlp") from e

        # 构建 URL
        url = source.url or (f"https://www.bilibili.com/video/{source.bv}" if source.bv else None)
        if not url:
            raise ValueError("需要提供 URL 或 BV 号")

        logger.info("使用链接: %s", url)

        ydl_opts = self._build_ydl_opts(source, settings)

        # 进度钩子 - 按 5% 分桶节流（与 douyin 下载进度同一策略）
        if progress:
            last_bucket = [-1]

            def progress_hook(data: dict[str, Any]) -> None:
                status = data.get("status")
                if status != "downloading":
                    return
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                downloaded = data.get("downloaded_bytes") or 0
                if total > 0:
                    bucket = int(downloaded / total * 100 // 5)
                    if bucket != last_bucket[0]:
                        last_bucket[0] = bucket
                        logger.info(
                            "下载进度: %.0f%% (%d / %d 字节)",
                            downloaded / total * 100,
                            downloaded,
                            total,
                        )
                else:
                    logger.info("下载进度: %d 字节", downloaded)

            ydl_opts["progress_hooks"] = [progress_hook]

        # 执行下载（一次 extract_info 拿到信息 + 文件）
        logger.info("开始下载...")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                # 处理播放列表
                if "entries" in info and info["entries"]:
                    info = info["entries"][0]

                title = info.get("title")
                duration = info.get("duration")
                uploader = info.get("uploader")
                if title:
                    logger.info("找到视频: %s（%s 秒）", title, duration)

                info = ydl.sanitize_info(info)
                video_path = self._resolve_video_path(ydl, info)

                if not video_path or not Path(video_path).exists():
                    raise RuntimeError(f"下载失败: 找不到视频文件 {video_path}")

                logger.info("下载成功: %s", Path(video_path).name)

                return DownloadResult(
                    source=source,
                    video_path=Path(video_path),
                    title=title,
                    webpage_url=info.get("webpage_url") or source.url,
                    metadata={
                        "title": title,
                        "uploader": uploader,
                        "duration": duration,
                        "id": info.get("id"),
                    },
                )
        except Exception as e:
            logger.error("下载失败: %s", e)
            logger.info("可能的解决方法:")
            logger.info("  1) 检查网络连接")
            logger.info("  2) 尝试访问视频网页确认视频存在")
            logger.info("  3) 尝试使用完整 URL 而不是短链接")
            raise

    def _build_ydl_opts(self, source: SourceRef, settings: Settings) -> dict[str, Any]:
        """构建 yt-dlp 选项 - 来自 bili2text"""
        opts = {
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "outtmpl": str(settings.downloads_dir / "%(id)s.%(ext)s"),
            "noprogress": False,
            "quiet": False,
            "no_warnings": False,
            # 添加 headers 绕过 Bilibili 412 错误
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        }

        # 检查 cookies.txt 文件
        cookies_file = settings.workspace_root / "cookies.txt"
        if cookies_file.exists():
            logger.info("使用 cookies 文件: %s", cookies_file)
            opts["cookiefile"] = str(cookies_file)
        else:
            logger.info("提示: 如果需要 cookies，请在项目目录下放置 cookies.txt")

        return opts

    def _resolve_video_path(self, ydl: Any, info: dict[str, Any]) -> Optional[Path]:
        """解析视频路径 - 来自 bili2text"""
        requested_downloads = info.get("requested_downloads") or []
        for requested in requested_downloads:
            filepath = requested.get("filepath")
            if filepath:
                return Path(filepath)

        prepared = Path(ydl.prepare_filename(info))
        if prepared.exists():
            return prepared

        merged_mp4 = prepared.with_suffix(".mp4")
        if merged_mp4.exists():
            return merged_mp4

        return None
