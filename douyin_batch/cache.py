"""
缓存管理 - 支持断点续传
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Optional


class ProcessCache:
    """处理缓存 - 记录已处理的视频，支持断点续传"""

    def __init__(self, cache_dir: Path = Path("output/cache")):
        self.cache_dir = cache_dir
        self.cache_file = cache_dir / "processed_videos.json"
        self._cache_data = self._load()

    def _load(self) -> dict:
        """加载缓存"""
        if not self.cache_file.exists():
            return {"videos": {}, "users": {}}
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"videos": {}, "users": {}}

    def _save(self):
        """保存缓存"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(self._cache_data, f, ensure_ascii=False, indent=2)

    def is_processed(self, video_id: str) -> bool:
        """检查视频是否已处理"""
        return video_id in self._cache_data["videos"]

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
        """标记为已处理"""
        self._cache_data["videos"][video_id] = {
            "url": video_url,
            "transcript": transcript_path,
            "audio": audio_path,
            "success": success,
            "processed_at": datetime.now().isoformat(),
        }
        self._save()

    def filter_unprocessed(self, videos: list) -> list:
        """过滤出未处理的视频"""
        return [v for v in videos if not self.is_processed(v["video_id"])]

    def get_user_videos(self, user_url: str) -> list:
        """获取缓存的某用户的所有视频"""
        # nosec B324 - 仅作缓存键去重用，非安全敏感哈希
        user_hash = hashlib.md5(user_url.encode()).hexdigest()[:12]
        return self._cache_data["users"].get(user_hash, {}).get("videos", [])

    def save_user_videos(self, user_url: str, videos: list):
        """保存用户视频列表到缓存"""
        # nosec B324 - 仅作缓存键去重用，非安全敏感哈希
        user_hash = hashlib.md5(user_url.encode()).hexdigest()[:12]
        self._cache_data["users"][user_hash] = {
            "user_url": user_url,
            "videos": videos,
            "cached_at": datetime.now().isoformat(),
        }
        self._save()

    def clear(self):
        """清空缓存"""
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
