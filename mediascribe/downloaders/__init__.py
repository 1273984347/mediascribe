"""
下载器模块
"""

from .base import Downloader
from .douyin import DouyinDownloader
from .wechat_mp import WechatMpDownloader
from .xiaohongshu import XiaohongshuDownloader
from .youtube import YouTubeDownloader
from .ytdlp import YtDlpDownloader

__all__ = [
    "Downloader",
    "YtDlpDownloader",
    "DouyinDownloader",
    "YouTubeDownloader",
    "XiaohongshuDownloader",
    "WechatMpDownloader",
]
