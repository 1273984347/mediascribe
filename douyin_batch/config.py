"""
配置管理 - 支持配置文件、环境变量、默认值

v3.2.0g:
- 新增 ``user_url`` 字段：``--config`` 纯配置文件启动终于可用
  （旧版 from_dict 会把这个未知键丢弃，导致 100% 找不到用户主页）
- 环境变量主前缀修正为 ``DOUYIN_BATCH_*``（与文档一致），
  同时兼容读取旧 ``DOYIN_BATCH_*`` 并给出弃用警告
- 删除全仓无消费者的死字段；``scroll_pause`` / ``max_scroll_rounds``
  保留并真正接线到 BrowserManager（get_user_videos 的滚动节奏）
- ``merge_cli_args`` 被 douyin_batch_v3 真正调用（不再是死代码）
"""
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("douyin_batch.config")

# 环境变量后缀 → (字段名, 类型)。主前缀 DOUYIN_BATCH_*，兼容旧 DOYIN_BATCH_*。
_ENV_FIELDS = {
    "HEADLESS": ("headless", bool),
    "MAX_VIDEOS": ("max_videos", int),
    "WORKERS": ("workers", int),
    "LANGUAGE": ("language", str),
    "MODEL": ("whisper_model", str),
    "OUTPUT_DIR": ("output_dir", str),
    "KEEP_AUDIO": ("keep_audio", bool),
    "LOG_LEVEL": ("log_level", str),
    "MAX_RETRIES": ("max_retries", int),
}


@dataclass
class BatchConfig:
    """批量转录配置"""

    # 输入配置
    user_url: Optional[str] = None  # 作者主页 URL（--config 纯配置文件启动用）

    # 浏览器配置
    headless: bool = True

    # 下载配置
    max_retries: int = 3

    # 抓取配置
    max_videos: int = 10
    max_scroll_rounds: int = 10  # 主页滚动轮数（接线到 browser.get_user_videos）
    scroll_pause: float = 2.0  # 滚动停顿秒数（接线到 browser.get_user_videos）

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
        """从环境变量加载配置（覆盖默认）

        主前缀 ``DOUYIN_BATCH_*``（与 --help 文档一致）；
        旧前缀 ``DOYIN_BATCH_*`` 仍可读取，但会输出弃用警告。
        """
        config = cls()

        for suffix, (attr, type_) in _ENV_FIELDS.items():
            env_value = os.environ.get(f"DOUYIN_BATCH_{suffix}")
            if env_value is None:
                legacy_value = os.environ.get(f"DOYIN_BATCH_{suffix}")
                if legacy_value is not None:
                    logger.warning(
                        "环境变量 DOYIN_BATCH_%s 已弃用，请改用 DOUYIN_BATCH_%s",
                        suffix, suffix,
                    )
                    env_value = legacy_value
            if env_value is not None:
                if type_ is bool:
                    setattr(config, attr, env_value.lower() in ("true", "1", "yes"))
                else:
                    setattr(config, attr, type_(env_value))

        return config

    def merge_cli_args(self, args) -> "BatchConfig":
        """合并 CLI 参数（CLI 参数优先级最高；未显式提供的参数不覆盖）"""
        # 只在 CLI 显式提供时覆盖
        if getattr(args, "num", None) is not None:
            self.max_videos = args.num
        if getattr(args, "workers", None) is not None:
            self.workers = args.workers
        if getattr(args, "no_headless", False):
            self.headless = False
        if getattr(args, "retries", None) is not None:
            self.max_retries = args.retries
        if getattr(args, "output_dir", None):
            self.output_dir = args.output_dir
        if getattr(args, "keep_audio", False):
            self.keep_audio = True
        if getattr(args, "log_level", None):
            self.log_level = args.log_level
        return self

    def __str__(self) -> str:
        """友好的配置显示"""
        lines = ["📋 当前配置:"]
        for k, v in self.to_dict().items():
            lines.append(f"   - {k}: {v}")
        return "\n".join(lines)
