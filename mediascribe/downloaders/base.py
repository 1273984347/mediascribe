"""
下载器基类 - 参考 bili2text
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from ..config import Settings
from ..models import DownloadResult, SourceRef


class Downloader(ABC):
    """下载器基类"""

    name: str = "base"

    @abstractmethod
    def download(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress: Optional[Any] = None,
    ) -> DownloadResult:
        """下载视频"""
        pass
