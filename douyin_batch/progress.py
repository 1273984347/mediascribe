"""
进度条和ETA计算
"""

import time
from typing import Optional


class ProgressTracker:
    """进度跟踪器 - 带ETA估计"""

    def __init__(self, total: int, desc: str = "处理中"):
        self.total = total
        self.desc = desc
        self.completed = 0
        self.failed = 0
        self.start_time = time.time()
        self.task_times = []  # 每个任务的耗时

    def update(self, success: bool = True, task_time: Optional[float] = None):
        """更新进度"""
        self.completed += 1
        if not success:
            self.failed += 1
        if task_time is not None:
            self.task_times.append(task_time)

    def get_eta(self) -> float:
        """估算剩余时间（秒）"""
        if not self.task_times:
            return 0.0

        avg_time = sum(self.task_times) / len(self.task_times)
        remaining = self.total - self.completed
        return avg_time * remaining

    def get_elapsed(self) -> float:
        """已用时间（秒）"""
        return time.time() - self.start_time

    def render(self, current_task: str = "") -> str:
        """渲染进度条"""
        percent = (self.completed / self.total) * 100 if self.total > 0 else 0
        filled = int(percent / 2)  # 50字符宽度
        bar = "█" * filled + "░" * (50 - filled)

        elapsed = self.get_elapsed()
        eta = self.get_eta()

        elapsed_str = format_duration(elapsed)
        eta_str = format_duration(eta) if eta > 0 else "--:--"

        current = f" | {current_task}" if current_task else ""

        return (
            f"\r{self.desc}: |{bar}| "
            f"{self.completed}/{self.total} ({percent:.0f}%) "
            f"[{elapsed_str}<{eta_str}]"
            f" ✅{self.completed - self.failed} ❌{self.failed}{current}"
        )


def format_duration(seconds: float) -> str:
    """格式化时长为 HH:MM:SS 或 MM:SS"""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds:02d}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"
    else:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


def format_size(bytes_size: int) -> str:
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_size < 1024:
            return f"{bytes_size:.1f}{unit}"
        bytes_size /= 1024
    return f"{bytes_size:.1f}TB"
