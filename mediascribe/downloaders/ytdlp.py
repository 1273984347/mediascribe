"""
yt-dlp 下载器 - 真正参考 bili2text 的实现

v3.2.0g:
- ``print`` → ``logging.getLogger(__name__)``，--json 模式下不污染 stdout
- 只调用一次 ``extract_info(download=True)``，标题从下载结果取
  （原来先 ``extract_info(download=False)`` 预取一遍，同样信息请求两次）
- 进度钩子按 5% 分桶节流，避免逐 chunk 刷屏

v3.4.1:
- B 站下载失败自动回退 playurl API 备用源（仅音轨）。B 站现在把音轨
  全量调度到 P2P CDN（``*.mcdn.bilivideo.cn:8082``），大量网络环境下
  直连超时；playurl API 响应中的 ``backup_url`` 指向常规 upos 镜像且
  各自带完整签名，可直接下载。转录只需要音轨，视频流缺失不影响。
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..models import DownloadResult, SourceRef
from .base import Downloader

logger = logging.getLogger(__name__)

_BV_RE = re.compile(r"(BV1[0-9A-Za-z]{9})", re.IGNORECASE)
_PAGE_RE = re.compile(r"[?&]p=(\d+)")
_API_TIMEOUT = 15
_BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}
# 低于该大小的音轨响应视为无效（10 分钟音频 ≈ 8 MB）
_MIN_AUDIO_BYTES = 64 * 1024


def _parse_bilibili(url: Optional[str]) -> tuple[Optional[str], int]:
    """从 URL 提取 (bvid, 页码)；非 B 站链接返回 (None, 1)。"""
    if not url:
        return None, 1
    bv_match = _BV_RE.search(url)
    if not bv_match:
        return None, 1
    page_match = _PAGE_RE.search(url)
    page = int(page_match.group(1)) if page_match else 1
    return bv_match.group(1), max(page, 1)


def _pick_best_audio(dash: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """从 playurl 的 dash 结构中选最高码率音轨；无音轨返回 None。"""
    audios = (dash or {}).get("audio") or []
    if not audios:
        return None
    return max(audios, key=lambda a: a.get("bandwidth") or 0)


def _api_get(session: Any, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = session.get(endpoint, params=params, timeout=_API_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(
            f"B站 API {endpoint} 返回 code={payload.get('code')}: {payload.get('message')}"
        )
    return payload


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

            # B 站专用兜底：音轨被调度到 P2P CDN 时 yt-dlp 直连必挂，
            # 改走 playurl API 的 backup_url（常规 upos 镜像）拉音轨。
            if _parse_bilibili(url)[0] is not None:
                logger.warning("尝试 B 站 API 备用源（仅音轨）...")
                try:
                    result = self._download_bilibili_audio(url, settings)
                except Exception as fb_err:
                    logger.error("备用源失败: %s", fb_err)
                else:
                    if result is not None:
                        logger.info("备用源下载成功: %s", result.video_path.name)
                        return result

            logger.info("可能的解决方法:")
            logger.info("  1) 检查网络连接")
            logger.info("  2) 尝试访问视频网页确认视频存在")
            logger.info("  3) 尝试使用完整 URL 而不是短链接")
            raise

    def _download_bilibili_audio(self, url: str, settings: Settings) -> Optional[DownloadResult]:
        """playurl API 兜底：从 base_url/backup_url 逐个尝试拉取音轨。

        返回 ``None`` 表示 API 结构里没有可用的音轨（如充电专属视频），
        调用方应保留原始异常；API 请求本身失败则抛出异常。
        """
        import requests

        bvid, page = _parse_bilibili(url)
        if bvid is None:
            return None

        session = requests.Session()
        session.headers.update(_BILI_HEADERS)

        pages = _api_get(session, "https://api.bilibili.com/x/player/pagelist", {"bvid": bvid})
        entries = pages.get("data") or []
        idx = page - 1
        if idx >= len(entries):
            logger.error("备用源: 页码 p%d 超出范围（共 %d 页）", page, len(entries))
            return None
        entry = entries[idx] or {}
        cid = entry.get("cid")
        if not cid:
            return None

        play = _api_get(
            session,
            "https://api.bilibili.com/x/player/playurl",
            {"bvid": bvid, "cid": cid, "fnval": 16, "qn": 64},
        )
        best = _pick_best_audio((play.get("data") or {}).get("dash"))
        if best is None:
            return None

        candidates = [best.get("base_url") or ""]
        candidates += list(best.get("backup_url") or [])
        payload: Optional[bytes] = None
        for candidate in candidates:
            if not candidate:
                continue
            try:
                resp = session.get(candidate, timeout=(10, 120))
                resp.raise_for_status()
                if len(resp.content) < _MIN_AUDIO_BYTES:
                    logger.warning("备用源: 响应过小(%d B)，跳过 %s", len(resp.content), candidate)
                    continue
                payload = resp.content
                logger.info("备用源: 从 %s 拉到 %d 字节", candidate.split("/")[2], len(payload))
                break
            except Exception as e:
                logger.warning("备用源: %s 下载失败（%s），尝试下一个", candidate.split("/")[2], e)
        if payload is None:
            return None

        settings.downloads_dir.mkdir(parents=True, exist_ok=True)
        raw_path = settings.downloads_dir / f"{bvid}_p{page}.m4s"
        raw_path.write_bytes(payload)
        out_path = self._remux_m4a(raw_path)

        part_title = entry.get("part")
        webpage = f"https://www.bilibili.com/video/{bvid}" + (f"?p={page}" if page > 1 else "")
        return DownloadResult(
            source=SourceRef(
                raw_input=url,
                kind="bilibili",
                bv=bvid,
                url=webpage,
            ),
            video_path=out_path,
            title=part_title,
            webpage_url=webpage,
            metadata={
                "title": part_title,
                "uploader": (play.get("data") or {}).get("owner", {}).get("name"),
                "duration": entry.get("duration"),
                "id": f"{bvid}_p{page}",
                "downloader": "bilibili-api-audio-fallback",
            },
        )

    def _remux_m4a(self, raw_path: Path) -> Path:
        """DASH 音轨（fMP4）重封装为 .m4a；无 ffmpeg 时直接改名（字节兼容）。"""
        out_path = raw_path.with_suffix(".m4a")
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            try:
                subprocess.run(
                    [
                        ffmpeg,
                        "-y",
                        "-loglevel",
                        "error",
                        "-i",
                        str(raw_path),
                        "-c",
                        "copy",
                        str(out_path),
                    ],
                    check=True,
                    timeout=120,
                )
                raw_path.unlink()
                return out_path
            except Exception as e:
                logger.warning("ffmpeg 重封装失败（%s），保留原始字节", e)
        raw_path.replace(out_path)
        return out_path

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
