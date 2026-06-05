"""
转录模块 - 带复用的 Whisper Pipeline
"""
import sys
from pathlib import Path
from typing import Optional


class TranscriberPool:
    """转录器池 - 复用 Whisper 模型（避免重复加载）"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from video2text import Pipeline, Settings

        self.settings = Settings()
        self.pipeline = Pipeline(self.settings)
        self._initialized = True

    def transcribe(self, audio_path: Path) -> Optional[Path]:
        """转录音频"""
        try:
            result = self.pipeline.transcribe(str(audio_path), language="zh")
            return result.transcript_path
        except Exception as e:
            print(f"      ❌ 转录失败: {e}")
            return None


def transcribe_audio(audio_path: Path) -> Optional[Path]:
    """便捷函数 - 转录音频"""
    pool = TranscriberPool()
    return pool.transcribe(audio_path)
