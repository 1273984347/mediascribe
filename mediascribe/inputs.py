"""
输入源解析 - 参考 bili2text
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import SourceRef
from .url_utils import normalize_bilibili_url, resolve_short_url


def parse_source(raw_input: str) -> SourceRef:
    """解析输入源"""
    raw_input = raw_input.strip()

    # 检查本地文件
    path = Path(raw_input)
    if path.exists():
        if is_audio_file(path):
            return SourceRef(raw_input=raw_input, kind="audio", path=path)
        if is_video_file(path):
            return SourceRef(raw_input=raw_input, kind="video", path=path)

    # 处理 URL
    if raw_input.startswith('http://') or raw_input.startswith('https://'):
        # 先解析短链接
        real_url = resolve_short_url(raw_input)
        if real_url:
            raw_url = real_url
        else:
            raw_url = raw_input

        # 检查是否是 Bilibili 相关 URL
        if 'bilibili.com' in raw_url or 'b23.tv' in raw_url:
            standard_url, bvid = normalize_bilibili_url(raw_url)
            if bvid:
                return SourceRef(raw_input=raw_input, kind="bilibili", bv=bvid, url=standard_url)
            return SourceRef(raw_input=raw_input, kind="bilibili", url=standard_url or raw_url)

        # 检查是否是抖音相关 URL（包括 douyinvod 媒体链接）
        if 'douyin.com' in raw_url or 'v.douyin.com' in raw_url or 'douyinvod.com' in raw_url:
            return SourceRef(raw_input=raw_input, kind="douyin", url=raw_url)

        # 检查是否是 YouTube 相关 URL
        if (
            'youtube.com' in raw_url
            or 'youtu.be' in raw_url
            or 'youtube-nocookie.com' in raw_url
            or 'm.youtube.com' in raw_url
        ):
            return SourceRef(raw_input=raw_input, kind="youtube", url=raw_url)

        # 检查是否是 TikTok 相关 URL
        if 'tiktok.com' in raw_url:
            return SourceRef(raw_input=raw_input, kind="tiktok", url=raw_url)

        # 检查是否是小红书相关 URL
        if (
            'xiaohongshu.com' in raw_url
            or 'xhslink.com' in raw_url
        ):
            return SourceRef(raw_input=raw_input, kind="xiaohongshu", url=raw_url)

        # 检查是否是微信公众号文章
        # 真实 URL 形如 `mp.weixin.qq.com/s?__biz=...` 或 `mp.weixin.qq.com/s/abc?__biz=...`
        if 'mp.weixin.qq.com' in raw_url and ('/s?' in raw_url or '/s/' in raw_url):
            return SourceRef(raw_input=raw_input, kind="wechat_mp", url=raw_url)

        # 其他 URL（用 yt-dlp 处理）
        return SourceRef(raw_input=raw_input, kind="video", url=raw_url)

    # 检查是否只输入了 BV 号
    bv_match = re.search(r'^BV[a-zA-Z0-9]{10}$', raw_input)
    if bv_match:
        bv = bv_match.group(0)
        return SourceRef(raw_input=raw_input, kind="bilibili", bv=bv, url=f"https://www.bilibili.com/video/{bv}")

    # 检查是否包含 BV 号
    bv_match = re.search(r'BV[a-zA-Z0-9]{10}', raw_input)
    if bv_match:
        bv = bv_match.group(0)
        return SourceRef(raw_input=raw_input, kind="bilibili", bv=bv, url=f"https://www.bilibili.com/video/{bv}")

    # 假设是本地文件（即使不存在）
    return SourceRef(raw_input=raw_input, kind="video", path=path)


def is_audio_file(path: Path) -> bool:
    audio_exts = {'.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg'}
    return path.suffix.lower() in audio_exts


def is_video_file(path: Path) -> bool:
    video_exts = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.m4v'}
    return path.suffix.lower() in video_exts


def safe_stem(name: str) -> str:
    """Sanitize a string for safe use as a file stem on all platforms.

    Delegates to :func:`douyin_batch.platform_compat.safe_filename`, which
    handles Windows reserved names (``CON``, ``PRN``…), control characters
    and the full set of OS-invalid characters. The shared helper guarantees
    identical behavior between the CLI/UI and the MCP ``safe_filename``
    tool.
    """
    # Local import to avoid pulling douyin_batch at module import time
    # (some embedders install ``mediascribe`` without the full batch tool).
    from douyin_batch.platform_compat import safe_filename

    return safe_filename(str(name))
