"""
缓存管理 - 支持断点续传

v3.2.0g:
- ``_save`` 改为 tmp + ``os.replace`` 原子写（进程/线程中断不再留下半截 JSON）
- ``_load`` 遇到损坏文件：logger.warning + 把坏文件改名 ``.bak`` 保留，
  不再静默清空丢失历史
- ``is_processed`` / ``filter_unprocessed`` 要求记录 ``success`` 为真：
  失败的视频下一轮可以重试，不会被永久跳过（P1-2）
- ``mark_processed`` 加线程锁（workers>1 并发安全）
"""

import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ProcessCache:
    """处理缓存 - 记录已处理的视频，支持断点续传"""

    def __init__(self, cache_dir: Path = Path("output/cache")):
        self.cache_dir = cache_dir
        self.cache_file = cache_dir / "processed_videos.json"
        self._lock = threading.Lock()
        self._cache_data = self._load()

    def _load(self) -> dict:
        """加载缓存；损坏时保留 .bak 并返回空结构"""
        if not self.cache_file.exists():
            return {"videos": {}, "users": {}}
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(
                "缓存文件损坏（%s），将改名保留为 .bak 并重新开始: %s",
                self.cache_file,
                e,
            )
            try:
                bak = self.cache_file.with_name(self.cache_file.name + ".bak")
                os.replace(self.cache_file, bak)
            except OSError:
                pass
            return {"videos": {}, "users": {}}

    def _save(self):
        """原子保存缓存（tmp + os.replace，中断不会损坏既有缓存）"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_file.with_name(
            f"{self.cache_file.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        )
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache_data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.cache_file)
        finally:
            if tmp.exists():  # replace 失败时清理临时文件
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def is_processed(self, video_id: str) -> bool:
        """检查视频是否已成功处理（失败记录不算，下一轮可重试）"""
        record = self._cache_data["videos"].get(video_id)
        return bool(record and record.get("success"))

    def get_processed(self, video_id: str) -> Optional[dict]:
        """获取已处理的记录"""
        return self._cache_data["videos"].get(video_id)

    def mark_processed(
        self,
        video_id: str,
        video_url: str,
        transcript_path: Optional[str] = None,
        audio_path: Optional[str] = None,
        success: bool = True,
    ):
        """标记为已处理（含失败记录；失败不算已处理，用于重试）"""
        with self._lock:
            self._cache_data["videos"][video_id] = {
                "url": video_url,
                "transcript": transcript_path,
                "audio": audio_path,
                "success": success,
                "processed_at": datetime.now().isoformat(),
            }
            self._save()

    def filter_unprocessed(self, videos: list) -> list:
        """过滤出未处理（或上次失败待重试）的视频"""
        return [v for v in videos if not self.is_processed(v["video_id"])]

    def get_user_videos(self, user_url: str) -> list:
        """获取缓存的某用户的所有视频"""
        # 仅作缓存键去重用，非安全敏感哈希（nosec 须与代码同行，见下）
        user_hash = hashlib.md5(user_url.encode()).hexdigest()[:12]  # nosec B324
        return self._cache_data["users"].get(user_hash, {}).get("videos", [])

    def save_user_videos(self, user_url: str, videos: list):
        """保存用户视频列表到缓存"""
        # 仅作缓存键去重用，非安全敏感哈希（nosec 须与代码同行，见下）
        user_hash = hashlib.md5(user_url.encode()).hexdigest()[:12]  # nosec B324
        with self._lock:
            self._cache_data["users"][user_hash] = {
                "user_url": user_url,
                "videos": videos,
                "cached_at": datetime.now().isoformat(),
            }
            self._save()

    def clear(self):
        """清空缓存"""
        with self._lock:
            self._cache_data = {"videos": {}, "users": {}}
            self._save()

    def get_stats(self) -> dict:
        """获取统计信息"""
        videos = self._cache_data["videos"]
        return {
            "total": len(videos),
            "success": sum(1 for v in videos.values() if v.get("success")),
            "failed": sum(1 for v in videos.values() if not v.get("success")),
        }
