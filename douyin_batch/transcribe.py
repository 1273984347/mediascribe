"""
转录模块 - 带复用的 Whisper Pipeline

v3.2.0g (P1-7): ``TranscriberPool`` 接受 ``BatchConfig``（或任何带
``whisper_model`` / ``language`` 属性的对象），把用户的
``whisper_model`` / ``language`` 配置真正映射进核心 ``Settings`` /
``Pipeline.transcribe``；不再硬编码 ``Settings()`` + ``language="zh"``。
"""

import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class TranscriberPool:
    """转录器池 - 复用 Whisper 模型（避免重复加载）

    单例语义：首次创建时固定配置；之后重复调用 ``TranscriberPool(config)``
    会返回既有实例并沿用首次的配置。
    """

    _instance = None

    def __new__(cls, config=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config=None):
        if hasattr(self, "_initialized") and self._initialized:
            return

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from mediascribe import Pipeline, Settings

        # BatchConfig → Settings 映射（缺省保持原行为：small + zh）
        whisper_model = getattr(config, "whisper_model", None) or "small"
        language = getattr(config, "language", None) or "zh"

        self.settings = Settings(model=whisper_model)
        self._language = language
        self.pipeline = Pipeline(self.settings)
        self._initialized = True

    def transcribe(self, audio_path: Path) -> Optional[Path]:
        """转录音频（语言来自初始化时传入的 config，默认 zh）"""
        try:
            language = getattr(self, "_language", None) or "zh"
            result = self.pipeline.transcribe(str(audio_path), language=language)
            return result.transcript_path
        except Exception as e:
            logger.error("转录失败: %s", e)
            return None


def transcribe_audio(audio_path: Path, config=None) -> Optional[Path]:
    """便捷函数 - 转录音频（config 透传给 TranscriberPool）"""
    pool = TranscriberPool(config=config)
    return pool.transcribe(audio_path)
