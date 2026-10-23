"""原子文件写入 — 全仓库唯一的 ``tmp → os.replace`` 实现点。

多个子系统(转录稿落盘 / 术语库 / 下载缓存索引 / 断点续传缓存)
历史上各自实现过"写临时文件 → 原子替换 → 失败清理 tmp", 细节渐渐
漂移; 2026-10-23 起收敛到这里。共同约束:

* 临时文件与目标同目录 — 同卷 ``os.replace`` 才是原子操作(跨卷会
  退化为 copy + delete, 失去原子性);
* tmp 名含 pid + uuid8, 多进程同时写同一目标也不会互相覆盖;
* 走 ``Path.write_text`` 而非 ``open()+write`` — 测试通过 monkey-patch
  ``Path.write_text`` 验证落盘行为(见 pipeline_stages 历史注释);
* 不 ``fsync``, 依赖 OS page cache flush — 单机开发态足够。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """原子写文本文件: 写 tmp → ``os.replace`` 替换; 失败清理 tmp 并重抛。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(content, encoding=encoding)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise
