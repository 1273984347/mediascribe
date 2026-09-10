"""
日志系统 - 彩色输出、文件日志、统一的日志接口
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


class ColoredFormatter(logging.Formatter):
    """彩色日志格式化器（仅在支持的终端上）"""

    # ANSI 颜色代码
    COLORS = {
        "DEBUG": "\033[36m",  # 青色
        "INFO": "\033[32m",  # 绿色
        "WARNING": "\033[33m",  # 黄色
        "ERROR": "\033[31m",  # 红色
        "CRITICAL": "\033[35m",  # 紫色
        "RESET": "\033[0m",
    }

    SYMBOLS = {
        "DEBUG": "🔍",
        "INFO": "ℹ️ ",
        "WARNING": "⚠️ ",
        "ERROR": "❌",
        "CRITICAL": "🚨",
    }

    def format(self, record):
        # 尝试添加颜色（Windows 可能不支持）
        try:
            color = self.COLORS.get(record.levelname, "")
            reset = self.COLORS["RESET"]
            symbol = self.SYMBOLS.get(record.levelname, "")

            # 简化时间戳
            timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            levelname_colored = f"{color}{record.levelname:<7}{reset}"

            msg = f"{timestamp} | {levelname_colored} {symbol} {record.getMessage()}"
            return msg
        except Exception:
            return super().format(record)


class Logger:
    """统一的日志接口"""

    _instance: Optional["Logger"] = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if Logger._initialized:
            return

        self.logger = logging.getLogger("douyin_batch")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()

        # 控制台 handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(ColoredFormatter())
        self.logger.addHandler(console_handler)

        Logger._initialized = True

    def add_file_handler(self, log_file: Path, level: int = logging.DEBUG):
        """添加文件 handler"""
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s"))
        self.logger.addHandler(file_handler)

    def set_level(self, level: str):
        """设置日志级别"""
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
        }
        self.logger.handlers[0].setLevel(level_map.get(level.upper(), logging.INFO))

    def set_quiet(self, quiet: bool = True):
        """Enable / disable console output (for --json mode)."""
        if quiet:
            # Disable all console handlers but keep file handlers intact.
            for h in self.logger.handlers:
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                    h.setLevel(logging.CRITICAL + 1)
        else:
            for h in self.logger.handlers:
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                    h.setLevel(logging.INFO)

    def debug(self, msg: str):
        self.logger.debug(msg)

    def info(self, msg: str):
        self.logger.info(msg)

    def warning(self, msg: str):
        self.logger.warning(msg)

    def error(self, msg: str):
        self.logger.error(msg)

    def success(self, msg: str):
        """成功消息（INFO 级别 + ✅ 图标）"""
        self.logger.info(f"✅ {msg}")

    def progress(self, msg: str):
        """进度消息（INFO 级别 + ⏳ 图标）"""
        self.logger.info(f"⏳ {msg}")


# 全局 logger 实例
log = Logger()


def get_logger() -> Logger:
    """获取全局 logger"""
    return log
