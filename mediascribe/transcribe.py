"""
转录模块 - 带复用的 Whisper Pipeline

v3.2.0g (P1-7): ``TranscriberPool`` 接受 ``BatchConfig``（或任何带
``whisper_model`` / ``language`` 属性的对象），把用户的
``whisper_model`` / ``language`` 配置真正映射进核心 ``Settings`` /
``Pipeline.transcribe``；不再硬编码 ``Settings()`` + ``language="zh"``。

P2-7(2026-11-20 架构评审): 去掉进程级单例与 ``sys.path.insert`` hack。

* 单例把**首次调用**的 config 冻结,后续不同 config 被静默忽略
  (改配置不生效的隐形坑);
* 真正昂贵的资源(模型权重)本来就由各引擎 transcribers 的**模块级缓存**
  复用(见 ``faster_whisper._MODEL_CACHE`` 等) —— 每次新建 Pool/Pipeline
  只是轻量对象构造,不会重复加载模型;
* 调用方(批处理主循环)按 run 创建一次持有即可。
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class TranscriberPool:
    """转录器池 - 包装一个按 config 构建的 Pipeline。

    普通 Python 对象(无单例):每次 ``TranscriberPool(config)`` 都按传入
    的 config 新建;同 run 内由调用方持有同一实例复用。
    """

    def __init__(self, config=None):
        from mediascribe import Pipeline, Settings

        # BatchConfig → Settings 映射（缺省保持原行为：small + zh）
        whisper_model = getattr(config, "whisper_model", None)
        language = getattr(config, "language", None) or "zh"
        # None 交给 Settings 统一解析；显式 engine 仍优先。
        engine = getattr(config, "engine", None)
        allow_model_download = bool(getattr(config, "allow_model_download", False))

        self.settings = Settings(
            model=whisper_model,
            engine=engine,
            allow_model_download=allow_model_download,
        )
        self._language = language
        self.pipeline = Pipeline(self.settings)

    def transcribe(self, audio_path: Path) -> Optional[Path]:
        """转录音频（语言来自初始化时传入的 config，默认 zh）"""
        try:
            language = getattr(self, "_language", None) or "zh"
            result = self.pipeline.transcribe(str(audio_path), language=language)
            # TranscriptResult.transcript_path 为 Path; pipeline 属性经动态导入推断为 Any
            path: Path = result.transcript_path
            return path
        except Exception as e:
            logger.error("转录失败: %s", e)
            return None


def transcribe_audio(audio_path: Path, config=None) -> Optional[Path]:
    """便捷函数 - 转录音频（config 透传给 TranscriberPool）

    模型权重复用由引擎模块级缓存承担,此处每次新建 Pool 只是小对象。"""
    pool = TranscriberPool(config=config)
    return pool.transcribe(audio_path)
