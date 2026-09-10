"""
YouTube 下载器 - 复用 yt-dlp，针对 YouTube 做参数调优

为什么是独立类？
- 显式注册为"youtube"下载器（路由更清晰）
- 覆盖 Bilibili 412 头为 YouTube 推荐头（部分 PoToken/Player 客户端策略）
- 自动合并 video+audio 编码
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..models import SourceRef
from .ytdlp import YtDlpDownloader


class YouTubeDownloader(YtDlpDownloader):
    """YouTube 专用下载器（继承自 YtDlpDownloader）"""

    name = "youtube"

    # YouTube 推荐客户端：避免 403、SABR 流、登录要求
    YOUTUBE_PLAYER_CLIENTS = [
        "web_safari",
        "ios",
        "android",
        "web_embedded",
    ]

    def __init__(self) -> None:
        # 故意不打印"YtDlp 初始化"，避免误导
        pass

    def _build_ydl_opts(self, source: SourceRef, settings: Settings) -> dict[str, Any]:
        opts = super()._build_ydl_opts(source, settings)
        # 用 YouTube 头替换默认头（不同 Referer/UA）
        opts["http_headers"] = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            # 不带 Referer 走 web_safari / ios 客户端
        }
        # 优先合并为 mp4；失败回退到任意格式
        opts["format"] = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b"
        opts["merge_output_format"] = "mp4"
        # 多个 player_client 轮换，提高成功率
        opts["extractor_args"] = {
            "youtube": {
                "player_client": self.YOUTUBE_PLAYER_CLIENTS,
                # 不要求 PO token
                "skip": ["translated_subs", "dash"],
            }
        }
        return opts

    def supports(self, source: SourceRef) -> bool:
        """判断是否支持该 source。"""
        if source.kind == "youtube":
            return True
        url = (source.url or "").lower()
        return (
            "youtube.com" in url
            or "youtu.be" in url
            or "youtube-nocookie.com" in url
            or "m.youtube.com" in url
        )
