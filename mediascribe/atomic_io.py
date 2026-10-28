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

import os
import uuid
from pathlib import Path


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
