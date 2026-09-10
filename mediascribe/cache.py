"""
Persistent on-disk cache for downloaded media and processed chunks.

The ``DownloadCache`` in :mod:`mediascribe.performance` is a *single-run*
cache: each pipeline run gets a fresh ``tempfile.mkdtemp()`` directory
and the cache is gone when the process exits.  The classes in this
module are their *persistent* counterparts:

* :class:`PersistentDownloadCache` — content-addressed (SHA-256)
  storage of downloaded media files.  Re-runs on the same URL hit
  the cache and skip the network entirely.
* :class:`PersistentChunkCache` — mtime + size keyed storage of
  audio chunks.  When the same source audio is split a second time
  with the same parameters, the chunks are re-used.

Both honour ``$XDG_CACHE_HOME`` (default ``~/.cache``) and are
bounded by a TTL (default 30 days) plus a max-size cap (LRU eviction
by mtime).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Default cache directory
# ---------------------------------------------------------------------------


_DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # 30 days
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB
_DEFAULT_MAX_CHUNK_ENTRIES = 1024
_DEFAULT_APP_NAME = "mediascribe"


def _atomic_copy(src: Path, dst: Path) -> None:
    """P2-3: 原子拷贝 — 先写同目录唯一 tmp 再 ``os.replace``。

    直接 ``shutil.copy2(src, dst)`` 时,进程在拷贝中途崩溃会永久
    留下半写文件顶替真缓存(之后每次命中都读到坏文件)。tmp 名含
    PID + UUID8,并发 put 同一 key 也不会互相覆盖。
    """
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _flush_index_finalizer(cache_dir: Path, name: str, index: dict, dirty: list) -> None:
    """atexit / GC 兜底 — 把仅存于内存的 index 变更落盘一次。

    通过 :func:`weakref.finalize` 注册(不持有 cache 实例本身,
    不阻碍 GC;finalize 默认同时注册 atexit)。
    """
    if not dirty[0]:
        return
    try:
        _save_index(cache_dir, name, index)
    except Exception:
        # 进程退出路径 — 尽力而为,绝不抛
        pass


def persistent_cache_dir(
    app_name: str = _DEFAULT_APP_NAME,
    *,
    override: Optional[Path] = None,
) -> Path:
    """Return the persistent cache directory for ``app_name``.

    Honours, in order of precedence:
    1. ``override`` (typically from ``Settings.cache_dir``)
    2. ``$MEDIASCRIBE_CACHE_DIR``
    3. ``$XDG_CACHE_HOME/<app_name>`` on Linux/macOS
    4. ``%LOCALAPPDATA%\\<app_name>\\Cache`` on Windows
    5. ``~/.cache/<app_name>`` as a last resort

    The directory is created lazily on first use; this function only
    returns the path.
    """
    if override is not None:
        return Path(override)
    env = os.environ.get("MEDIASCRIBE_CACHE_DIR")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / app_name
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if local:
            return Path(local) / app_name / "Cache"
    home = Path.home()
    return home / ".cache" / app_name


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------


def _index_path(cache_dir: Path, name: str) -> Path:
    return cache_dir / f"{name}.index.json"


def _load_index(cache_dir: Path, name: str) -> dict:
    p = _index_path(cache_dir, name)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt index — start fresh rather than crash.
        return {}


def _save_index(cache_dir: Path, name: str, index: dict) -> None:
    """原子写 index — tmp 名含 PID + UUID8 避免并发碰撞，
    写失败时清理 tmp（原 ``.tmp`` 名固定，多进程同时写会互相覆盖）。"""
    p = _index_path(cache_dir, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# PersistentDownloadCache
# ---------------------------------------------------------------------------


@dataclass
class CacheStats:
    """Snapshot of cache hit/miss/eviction counters."""

    hits: int
    misses: int
    stores: int
    evictions: int
    bytes_in_use: int


class PersistentDownloadCache:
    """A content-addressed URL→path cache that survives across runs.

    Parameters
    ----------
    base
        Directory where cached files live.  ``None`` means "use
        :func:`persistent_cache_dir`".
    ttl_seconds
        Entries older than this are evicted on :meth:`prune`.
    max_bytes
        After the cap is exceeded, the least-recently-accessed
        entries are evicted until under the cap.

    Notes
    -----
    The cache key is the SHA-256 of the URL (not the file contents)
    because we want to look up "have I downloaded this URL before?",
    not "have I seen this audio before?".  If the same URL points
    to a different file across runs, the first download wins; this
    matches user expectations.
    """

    def __init__(
        self,
        base: Optional[Path] = None,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> None:
        self._base = Path(base) if base else persistent_cache_dir()
        self._base.mkdir(parents=True, exist_ok=True)
        self._ttl = int(ttl_seconds)
        self._max_bytes = int(max_bytes)
        self._name = "downloads"
        self._index = _load_index(self._base, self._name)
        self._hits = 0
        self._misses = 0
        self._lru_seq = 0  # LRU 单调序号 — time.time() 在 Windows ~15ms 粒度下会打平
        self._stores = 0
        self._evictions = 0
        # P2-2: get() 命中只更新内存 last_access(消灭每次命中的
        # 全量 index 重写);脏标记 + finalize 兜底保证 LRU 顺序
        # 在 put/_evict/close 之外的路径也能落盘。
        self._dirty = [False]
        self._finalizer = weakref.finalize(
            self,
            _flush_index_finalizer,
            self._base,
            self._name,
            self._index,
            self._dirty,
        )

    @property
    def base(self) -> Path:
        return self._base

    def _key_for(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _persist_index(self) -> None:
        """立即落盘 index 并清除脏标记(put/_evict/close 路径)。"""
        _save_index(self._base, self._name, self._index)
        self._dirty[0] = False

    def _flush_dirty_index(self) -> None:
        if self._dirty[0]:
            self._dirty[0] = False
            _save_index(self._base, self._name, self._index)

    def close(self) -> None:
        """落盘内存中的 index 变更并解除 finalize 兜底。"""
        self._flush_dirty_index()
        self._finalizer.detach()

    def get(self, url: str) -> Optional[Path]:
        """Return the cached path for ``url`` or None.

        Bumps the entry's ``last_access`` so it survives LRU eviction.
        P2-2: ``last_access`` 更新只写内存,不再每次命中全量重写
        index(写放大) — 落盘时机收敛到 put/_evict/close/atexit。
        """
        key = self._key_for(url)
        entry = self._index.get(key)
        if entry is None:
            self._misses += 1
            return None
        path = self._base / entry["filename"]
        if not path.exists():
            # Stale index — entry vanished.  Clean up (deferred flush).
            self._index.pop(key, None)
            self._dirty[0] = True
            self._misses += 1
            return None
        self._lru_seq += 1
        entry["last_access"] = time.time()
        entry["lru_seq"] = self._lru_seq  # 同毫秒触点用序号决胜
        self._dirty[0] = True
        self._hits += 1
        return path

    def put(self, url: str, src: Path, *, suffix: str = "") -> Path:
        """Copy ``src`` into the cache and remember the URL.

        ``suffix`` is appended to the stored filename (e.g. ``.m4a``)
        so downstream tools can pick the right decoder without
        inspecting content.

        P2-3: 拷贝改原子写(同目录 tmp + ``os.replace``),进程崩溃
        不会留下半写文件顶替真缓存;index 在 put 时落盘。
        """
        key = self._key_for(url)
        filename = f"{key}{suffix}"
        dst = self._base / filename
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            _atomic_copy(src, dst)
        self._index[key] = {
            "url": url,
            "filename": filename,
            "size": dst.stat().st_size,
            "created": time.time(),
            "last_access": time.time(),
            "lru_seq": self._lru_seq + 1,
        }
        self._lru_seq += 1
        self._persist_index()
        self._stores += 1
        self._enforce_cap()
        return dst

    def contains(self, url: str) -> bool:
        return self.get(url) is not None

    def clear(self) -> int:
        """Remove every entry.  Returns the count of files deleted."""
        n = 0
        for entry in list(self._index.values()):
            p = self._base / entry["filename"]
            try:
                if p.exists():
                    p.unlink()
                    n += 1
            except OSError:
                pass
        self._index.clear()
        self._persist_index()
        return n

    def prune(self) -> int:
        """Evict entries older than ``ttl_seconds`` and return the count."""
        if self._ttl <= 0:
            return 0
        cutoff = time.time() - self._ttl
        victims = [k for k, v in self._index.items() if v.get("created", 0) < cutoff]
        return self._evict(victims)

    def _enforce_cap(self) -> None:
        """LRU-evict until ``bytes_in_use <= max_bytes``."""
        if self._max_bytes <= 0:
            return
        # Sort by last_access ascending; oldest first.
        ordered = sorted(
            self._index.items(),
            key=lambda kv: (
                kv[1].get("last_access", 0),
                kv[1].get("lru_seq", 0),
            ),
        )
        total = sum(v["size"] for v in self._index.values())
        victims: list[str] = []
        for k, v in ordered:
            if total <= self._max_bytes:
                break
            total -= v["size"]
            victims.append(k)
        self._evict(victims)

    def _evict(self, keys: list[str]) -> int:
        n = 0
        for k in keys:
            entry = self._index.pop(k, None)
            if entry is None:
                continue
            p = self._base / entry["filename"]
            try:
                if p.exists():
                    p.unlink()
                    n += 1
            except OSError:
                pass
            self._evictions += 1
        if keys:
            self._persist_index()
        return n

    def stats(self) -> CacheStats:
        return CacheStats(
            hits=self._hits,
            misses=self._misses,
            stores=self._stores,
            evictions=self._evictions,
            bytes_in_use=sum(v["size"] for v in self._index.values()),
        )

    def info(self) -> dict:
        """Human-readable snapshot for ``--cache-info`` CLI output."""
        s = self.stats()
        return {
            "base": str(self._base),
            "entries": len(self._index),
            "hits": s.hits,
            "misses": s.misses,
            "stores": s.stores,
            "evictions": s.evictions,
            "bytes_in_use": s.bytes_in_use,
            "max_bytes": self._max_bytes,
            "ttl_seconds": self._ttl,
        }


# ---------------------------------------------------------------------------
# PersistentChunkCache — mtime + size keyed
# ---------------------------------------------------------------------------


class PersistentChunkCache:
    """Cache audio chunks keyed by (source path, mtime, size, params).

    Unlike :class:`PersistentDownloadCache`, the key here is *content-
    independent*: the cache assumes that for the same source audio
    with the same split parameters, the chunks are stable.  This is
    true in practice — ffmpeg's chunking is deterministic — and it
    means the cache survives changes to the chunking implementation
    as long as the parameters are unchanged.

    P2-4: 原有 TTL 字段没有任何清理路径 — 现补 :meth:`prune()`
    (按 TTL)与条数上限(``max_entries``,按 ``last_access`` LRU 驱逐);
    put 改为逐文件原子写,避免崩溃留下半写 chunk。
    """

    def __init__(
        self,
        base: Optional[Path] = None,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        max_entries: int = _DEFAULT_MAX_CHUNK_ENTRIES,
    ) -> None:
        self._base = Path(base) if base else persistent_cache_dir() / "chunks"
        self._base.mkdir(parents=True, exist_ok=True)
        self._ttl = int(ttl_seconds)
        self._max_entries = int(max_entries)
        self._name = "chunks"
        self._index = _load_index(self._base, self._name)
        self._hits = 0
        self._misses = 0
        self._lru_seq = 0  # LRU 单调序号 — time.time() 在 Windows ~15ms 粒度下会打平
        self._dirty = [False]
        self._finalizer = weakref.finalize(
            self,
            _flush_index_finalizer,
            self._base,
            self._name,
            self._index,
            self._dirty,
        )

    def _key(self, src: Path, params: dict) -> str:
        st = src.stat()
        payload = {
            "path": str(Path(src).resolve()),
            "mtime": int(st.st_mtime),
            "size": st.st_size,
            "params": params,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _persist_index(self) -> None:
        _save_index(self._base, self._name, self._index)
        self._dirty[0] = False

    def _flush_dirty_index(self) -> None:
        if self._dirty[0]:
            self._dirty[0] = False
            _save_index(self._base, self._name, self._index)

    def close(self) -> None:
        """落盘内存中的 index 变更并解除 finalize 兜底。"""
        self._flush_dirty_index()
        self._finalizer.detach()

    def has(self, src: Path, params: dict) -> bool:
        return self.get(src, params) is not None

    def get(self, src: Path, params: dict) -> Optional[Path]:
        key = self._key(src, params)
        entry = self._index.get(key)
        if entry is None or not (self._base / entry["filename"]).exists():
            self._misses += 1
            return None
        # P2-4: LRU 触点只写内存,落盘时机收敛到 put/_evict/close
        self._lru_seq += 1
        entry["last_access"] = time.time()
        entry["lru_seq"] = self._lru_seq  # 同毫秒触点用序号决胜
        self._dirty[0] = True
        self._hits += 1
        return self._base / entry["filename"]

    def put(self, src: Path, params: dict, chunks_dir: Path) -> Path:
        """Snapshot ``chunks_dir`` (a directory of WAV files) into the cache.

        Returns the cached directory path.
        P2-4: 逐文件原子写 — 只有全部文件落位后才写 index;崩溃只
        留下无 index 引用的孤儿文件(下次 get 仍是 miss),不会命中
        半写的 chunk。
        """
        key = self._key(src, params)
        target = self._base / key
        target.mkdir(parents=True, exist_ok=True)
        for f in chunks_dir.iterdir():
            if f.is_file():
                _atomic_copy(f, target / f.name)
        self._index[key] = {
            "src": str(Path(src).resolve()),
            "filename": key,
            "params": params,
            "created": time.time(),
            "last_access": time.time(),
            "lru_seq": self._lru_seq + 1,
        }
        self._lru_seq += 1
        self._persist_index()
        self._enforce_cap()
        return target

    def prune(self) -> int:
        """Evict entries older than ``ttl_seconds`` and return the count."""
        if self._ttl <= 0:
            return 0
        cutoff = time.time() - self._ttl
        victims = [k for k, v in self._index.items() if v.get("created", 0) < cutoff]
        return self._evict(victims)

    def _enforce_cap(self) -> None:
        """LRU-evict until ``len(index) <= max_entries``."""
        if self._max_entries <= 0:
            return
        excess = len(self._index) - self._max_entries
        if excess <= 0:
            return
        ordered = sorted(
            self._index.items(),
            key=lambda kv: (
                kv[1].get("last_access", 0),
                kv[1].get("lru_seq", 0),
            ),
        )
        victims = [k for k, _ in ordered[:excess]]
        self._evict(victims)

    def _evict(self, keys: list[str]) -> int:
        n = 0
        for k in keys:
            entry = self._index.pop(k, None)
            if entry is None:
                continue
            p = self._base / entry["filename"]
            try:
                if p.exists():
                    shutil.rmtree(p, ignore_errors=True)
                    n += 1
            except OSError:
                pass
        if keys:
            self._persist_index()
        return n

    def info(self) -> dict:
        return {
            "base": str(self._base),
            "entries": len(self._index),
            "hits": self._hits,
            "misses": self._misses,
            "max_entries": self._max_entries,
            "ttl_seconds": self._ttl,
        }
