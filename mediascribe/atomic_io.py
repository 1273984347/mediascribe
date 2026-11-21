"""Atomic file writes — the single ``tmp → os.replace`` implementation site.

Several subsystems (transcript persistence / term library / download cache
index / resumable-progress cache) historically each rolled their own
"write temp file → atomic replace → cleanup tmp on failure", and the
details drifted apart; consolidated here since 2026-10-23. Shared
constraints:

* The temp file lives in the target's directory — ``os.replace`` is only
  atomic within one volume (cross-volume it degrades to copy + delete);
* The tmp name embeds pid + uuid8, so concurrent writers of the same
  target never clobber each other;
* Uses ``Path.write_text`` rather than ``open()+write`` — tests verify the
  on-disk behavior by monkey-patching ``Path.write_text`` (see the
  pipeline_stages history comment);
* No ``fsync`` — relies on the OS page cache flush; sufficient for a
  single-machine development tool.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically write a text file: write tmp → ``os.replace``; cleanup tmp and re-raise on failure."""
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


@contextmanager
def exclusive_file_lock(
    lock_path: Path, *, timeout: float = 30.0, poll: float = 0.05
) -> Iterator[None]:
    """Cross-process advisory lock via an ``O_CREAT | O_EXCL`` lockfile.

    R-8(2026-11-21 架构评审): ``threading.Lock`` 只在进程内有效 —— CLI batch
    与 Web 同时归档同一 vault 时两进程互盲。本锁用平台无关的 lockfile 补上
    跨进程互斥,与进程内 ``threading.Lock`` 叠加使用::

        with self._lock, exclusive_file_lock(root / ".vault.lock"): ...

    语义:

    * 锁文件 mtime 超过 ``timeout`` 秒视为前任持有者崩溃遗留,改名抢走
      (现场保留为 ``<lock>.stolen.<pid>`` 供排查);
    * 等待超过 ``timeout`` 秒仍拿不到 → **fail-open 放行**并告警 —— 写路径
      自身已有原子写与进程内锁兜底,宁可退化也不卡死归档。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd: int | None = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            # 先查 deadline —— 保证任何分支都有限终止(fail-open)。
            if time.monotonic() >= deadline:
                logger.warning(
                    "exclusive_file_lock busy beyond %.1fs, proceeding unlocked: %s",
                    timeout,
                    lock_path,
                )
                break  # fail-open: 进程内锁 + 原子写兜底
            try:
                if time.time() - lock_path.stat().st_mtime > timeout:
                    # 前任持有者疑似崩溃 —— 原子改名抢锁。Windows 下活锁的
                    # fd 仍打开, rename 会 PermissionError: 不可抢,继续等。
                    try:
                        os.replace(str(lock_path), f"{lock_path}.stolen.{os.getpid()}")
                    except OSError:
                        pass
                    continue
            except FileNotFoundError:
                continue  # 刚被释放,立刻重试
            time.sleep(poll)
        except OSError:  # pragma: no cover - defensive(权限/只读盘等)
            break  # fail-open
    if fd is None:
        # fail-open 放行,但**不清理**锁文件 —— 那是当前持有者的。
        yield
        return
    try:
        yield
    finally:
        try:
            os.close(fd)
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
