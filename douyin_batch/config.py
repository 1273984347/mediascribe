"""
配置管理 - 支持配置文件、环境变量、默认值
"""
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class BatchConfig:
    """批量转录配置"""

    # 浏览器配置
    headless: bool = True
    browser_timeout: int = 30

    # 下载配置
    max_retries: int = 3
    retry_delay: float = 1.0
    retry_backoff: float = 2.0
    download_timeout: int = 30

    # 抓取配置
    max_videos: int = 10
    max_scroll_rounds: int = 10
    scroll_pause: float = 2.0

    # 转录配置
    language: str = "zh"
    whisper_model: str = "small"

    # 输出配置
    output_dir: str = "output"
    keep_audio: bool = False

    # 并发配置
    workers: int = 1

    # 日志配置
    log_level: str = "INFO"
    log_to_file: bool = True

    # 高级配置
    skip_existing: bool = True  # 断点续传
    min_video_size_mb: float = 0.01  # 最小有效视频大小
    max_wait_for_media: int = 15  # 等待媒体URL超时（秒）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BatchConfig":
        # 过滤掉不在 dataclass 中的字段
        valid_fields = set(cls.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @classmethod
    def from_file(cls, config_path: Path) -> "BatchConfig":
        """从 JSON 文件加载配置"""
        if not config_path.exists():
            return cls()
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def save(self, config_path: Path):
        """保存到 JSON 文件"""
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def from_env(cls) -> "BatchConfig":
        """从环境变量加载配置（覆盖默认）"""
        config = cls()

        env_mapping = {
            "DOYIN_BATCH_HEADLESS": ("headless", bool),
            "DOYIN_BATCH_MAX_VIDEOS": ("max_videos", int),
            "DOYIN_BATCH_WORKERS": ("workers", int),
            "DOYIN_BATCH_LANGUAGE": ("language", str),
            "DOYIN_BATCH_MODEL": ("whisper_model", str),
            "DOYIN_BATCH_OUTPUT_DIR": ("output_dir", str),
            "DOYIN_BATCH_KEEP_AUDIO": ("keep_audio", bool),
            "DOYIN_BATCH_LOG_LEVEL": ("log_level", str),
            "DOYIN_BATCH_MAX_RETRIES": ("max_retries", int),
        }

        for env_key, (attr, type_) in env_mapping.items():
            env_value = os.environ.get(env_key)
            if env_value is not None:
                if type_ is bool:
                    setattr(config, attr, env_value.lower() in ("true", "1", "yes"))
                else:
                    setattr(config, attr, type_(env_value))

        return config

    def merge_cli_args(self, args) -> "BatchConfig":
        """合并 CLI 参数（CLI 参数优先级最高）"""
        # 只在 CLI 显式提供时覆盖
        if hasattr(args, "num") and args.num is not None:
            self.max_videos = args.num
        if hasattr(args, "workers") and args.workers is not None:
            self.workers = args.workers
        if hasattr(args, "no_headless"):
            self.headless = not args.no_headless
        if hasattr(args, "retries") and args.retries is not None:
            self.max_retries = args.retries
        if hasattr(args, "output_dir"):
            self.output_dir = args.output_dir
        if hasattr(args, "keep_audio"):
            self.keep_audio = args.keep_audio
        return self

    def __str__(self) -> str:
        """友好的配置显示"""
        lines = ["📋 当前配置:"]
        for k, v in self.to_dict().items():
            lines.append(f"   - {k}: {v}")
        return "\n".join(lines)
