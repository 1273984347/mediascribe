"""Tests for v3.2.0a persistent cache module."""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# persistent_cache_dir — resolve to a sensible default
# ---------------------------------------------------------------------------


class TestPersistentCacheDir(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {}
        for k in ("MEDIASCRIBE_CACHE_DIR", "XDG_CACHE_HOME"):
            self._saved[k] = os.environ.get(k)
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_explicit_override(self):
        from mediascribe.cache import persistent_cache_dir

        p = persistent_cache_dir(override=Path("/custom/cache"))
        self.assertEqual(p, Path("/custom/cache"))

    def test_env_var_wins(self):
        from mediascribe.cache import persistent_cache_dir

        os.environ["MEDIASCRIBE_CACHE_DIR"] = "/env/cache"
        p = persistent_cache_dir()
        self.assertEqual(p, Path("/env/cache"))

    def test_xdg_cache_home(self):
        from mediascribe.cache import persistent_cache_dir

        os.environ["XDG_CACHE_HOME"] = "/xdg"
        p = persistent_cache_dir()
        self.assertEqual(p, Path("/xdg/mediascribe"))

    def test_app_name(self):
        from mediascribe.cache import persistent_cache_dir

        os.environ["XDG_CACHE_HOME"] = "/xdg"
        p = persistent_cache_dir("myapp")
        self.assertEqual(p, Path("/xdg/myapp"))

    def test_fallback_to_home(self):
        from mediascribe.cache import persistent_cache_dir

        # On Windows, %LOCALAPPDATA% takes precedence.  On Linux/macOS
        # the .cache fallback is used.  Either way it should point
        # inside the user's home / appdata, not at /tmp.
        with mock.patch.object(Path, "home", return_value=Path("/home/x")):
            p = persistent_cache_dir()
        self.assertIn(
            str(p),
            [
                str(Path("/home/x/.cache/mediascribe")),
                str(Path("C:/Users/12739/AppData/Local/mediascribe/Cache")),
            ],
        )


# ---------------------------------------------------------------------------
# PersistentDownloadCache — content-addressed URL→path cache
# ---------------------------------------------------------------------------


class TestPersistentDownloadCache(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self._make_tmp())
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.src = self.tmp / "source.m4a"
        self.src.write_bytes(b"hello world media")

    def _make_tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="v2t_pcache_")

    def _make_cache(self, **kw) -> tuple:
        from mediascribe.cache import PersistentDownloadCache

        c = PersistentDownloadCache(base=self.tmp / "cache", **kw)
        return c

    def test_miss_then_hit(self):
        c = self._make_cache()
        self.assertIsNone(c.get("https://example.com/a.m4a"))
        c.put("https://example.com/a.m4a", self.src, suffix=".m4a")
        p = c.get("https://example.com/a.m4a")
        self.assertIsNotNone(p)
        self.assertTrue(p.exists())
        self.assertEqual(p.read_bytes(), b"hello world media")
        s = c.stats()
        self.assertEqual(s.misses, 1)
        self.assertEqual(s.hits, 1)
        self.assertEqual(s.stores, 1)

    def test_contains(self):
        c = self._make_cache()
        c.put("u1", self.src)
        self.assertTrue(c.contains("u1"))
        self.assertFalse(c.contains("u2"))

    def test_distinct_urls_distinct_entries(self):
        c = self._make_cache()
        c.put("u1", self.src, suffix=".m4a")
        c.put("u2", self.src, suffix=".m4a")
        self.assertTrue(c.contains("u1"))
        self.assertTrue(c.contains("u2"))
        # Two different keys → two different files
        files = list((self.tmp / "cache").iterdir())
        # The index file is one of them; actual cached files have sha256 names
        self.assertGreaterEqual(len([f for f in files if f.is_file() and f.suffix == ".m4a"]), 2)

    def test_clear_removes_all(self):
        c = self._make_cache()
        c.put("u1", self.src)
        c.put("u2", self.src)
        n = c.clear()
        self.assertEqual(n, 2)
        self.assertEqual(c.stats().bytes_in_use, 0)
        self.assertFalse(c.contains("u1"))

    def test_stale_index_cleaned_up(self):
        c = self._make_cache()
        c.put("u1", self.src, suffix=".m4a")
        # Manually delete the underlying file
        for entry in c._index.values():
            (self.tmp / "cache" / entry["filename"]).unlink()
            break
        # Now get() should detect the missing file and clean up
        self.assertIsNone(c.get("u1"))

    def test_ttl_prune(self):
        c = self._make_cache(ttl_seconds=10)
        c.put("u1", self.src, suffix=".m4a")
        # Backdate the entry
        for entry in c._index.values():
            entry["created"] = time.time() - 100
        _save_index_local(self.tmp / "cache", "downloads", c._index)
        n = c.prune()
        self.assertEqual(n, 1)
        self.assertFalse(c.contains("u1"))

    def test_lru_eviction(self):
        # Pure LRU: 3 × 10 bytes = 30 bytes, cap = 25.
        # put u1, u2, u3 → on the last put, one is evicted (oldest).
        # Then add u4 → another eviction.  We assert specific surviving
        # entries based on LRU access order.
        small = self.tmp / "s.m4a"
        small.write_bytes(b"X" * 10)
        c = self._make_cache(max_bytes=25)
        c.put("u1", small, suffix=".m4a")
        c.put("u2", small, suffix=".m4a")
        c.put("u3", small, suffix=".m4a")
        # total = 30 > 25; u1 (oldest) evicted on the third put
        self.assertFalse(c.contains("u1"))
        # u2 and u3 still in the cache
        self.assertTrue(c.contains("u2"))
        self.assertTrue(c.contains("u3"))
        # Touch u3 to make it most-recent
        self.assertIsNotNone(c.get("u3"))
        c.put("u4", small, suffix=".m4a")
        # total would be 30 again; u2 (now oldest) is evicted; u3 survives
        self.assertTrue(c.contains("u3"))
        self.assertFalse(c.contains("u2"))
        self.assertTrue(c.contains("u4"))

    def test_lru_access_reorders(self):
        c = self._make_cache(max_bytes=30)
        small = self.tmp / "s.m4a"
        small.write_bytes(b"X" * 10)
        c.put("u1", small, suffix=".m4a")  # 10 bytes
        c.put("u2", small, suffix=".m4a")  # 10 bytes
        c.put("u3", small, suffix=".m4a")  # 10 bytes; total 30
        # Touch u1 to make it most recent
        c.get("u1")
        # Add u4 to push past cap
        c.put("u4", small, suffix=".m4a")
        # u2 should be evicted (oldest), u1 survived
        self.assertTrue(c.contains("u1"))
        self.assertFalse(c.contains("u2"))

    def test_info_returns_dict(self):
        c = self._make_cache()
        c.put("u1", self.src, suffix=".m4a")
        info = c.info()
        self.assertIn("base", info)
        self.assertIn("entries", info)
        self.assertEqual(info["entries"], 1)
        self.assertEqual(info["hits"], 0)


def _save_index_local(cache_dir: Path, name: str, index: dict) -> None:
    """Test helper: write the index file the same way cache.py does."""
    from mediascribe.cache import _save_index

    _save_index(cache_dir, name, index)


# ---------------------------------------------------------------------------
# PersistentChunkCache — mtime + size keyed
# ---------------------------------------------------------------------------


class TestPersistentChunkCache(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self._make_tmp())
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.src = self.tmp / "source.wav"
        self.src.write_bytes(b"X" * 1000)
        self.chunks_dir = self.tmp / "chunks_src"
        self.chunks_dir.mkdir()
        (self.chunks_dir / "chunk_000.wav").write_bytes(b"audio data 1")
        (self.chunks_dir / "chunk_001.wav").write_bytes(b"audio data 2")

    def _make_tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="v2t_pchunk_")

    def _make_cache(self):
        from mediascribe.cache import PersistentChunkCache

        return PersistentChunkCache(base=self.tmp / "chunk_cache")

    def _params(self) -> dict:
        return {"chunk_seconds": 60, "overlap_seconds": 5}

    def test_miss_then_hit(self):
        c = self._make_cache()
        self.assertIsNone(c.get(self.src, self._params()))
        c.put(self.src, self._params(), self.chunks_dir)
        p = c.get(self.src, self._params())
        self.assertIsNotNone(p)
        self.assertTrue(p.exists())
        # both chunks copied
        self.assertTrue((p / "chunk_000.wav").exists())
        self.assertTrue((p / "chunk_001.wav").exists())

    def test_has(self):
        c = self._make_cache()
        self.assertFalse(c.has(self.src, self._params()))
        c.put(self.src, self._params(), self.chunks_dir)
        self.assertTrue(c.has(self.src, self._params()))

    def test_different_params_distinct_entries(self):
        c = self._make_cache()
        c.put(self.src, {"chunk_seconds": 60}, self.chunks_dir)
        c.put(self.src, {"chunk_seconds": 30}, self.chunks_dir)
        self.assertEqual(c.info()["entries"], 2)

    def test_changed_source_invalidates(self):
        c = self._make_cache()
        c.put(self.src, self._params(), self.chunks_dir)
        # Change mtime + size of source
        import os

        new = self.tmp / "source2.wav"
        new.write_bytes(b"Y" * 999)
        os.utime(new, (time.time() + 5, time.time() + 5))
        self.assertIsNone(c.get(new, self._params()))

    def test_info(self):
        c = self._make_cache()
        c.put(self.src, self._params(), self.chunks_dir)
        info = c.info()
        self.assertEqual(info["entries"], 1)
        # setUp calls put() directly, not get() — so no misses yet
        self.assertEqual(info["misses"], 0)


# ---------------------------------------------------------------------------
# P2-2/3/4 — 原子性 / 写放大 / TTL+LRU 清理
# ---------------------------------------------------------------------------


class TestCacheAtomicityAndWriteAmplification(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="v2t_pcache_atomic_"))
        self.src = self.tmp / "src.bin"
        self.src.write_bytes(b"payload" * 10)

    def _cache(self, **kw):
        from mediascribe.cache import PersistentDownloadCache

        return PersistentDownloadCache(base=self.tmp / "cache", **kw)

    def test_put_leaves_no_tmp_files(self):
        c = self._cache()
        c.put("u1", self.src, suffix=".bin")
        leftovers = [p.name for p in (self.tmp / "cache").iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [])

    def test_get_hit_does_not_rewrite_index_file(self):
        """P2-2: get() 命中只更新内存,不再全量重写 index(写放大)。"""
        c = self._cache()
        c.put("u1", self.src)
        index_path = self.tmp / "cache" / "downloads.index.json"
        mtime_before = index_path.stat().st_mtime_ns
        c.get("u1")
        c.get("u1")
        self.assertEqual(index_path.stat().st_mtime_ns, mtime_before)
        # 但 LRU 触点仍记录在内存里
        self.assertGreater(c._index[c._key_for("u1")]["last_access"], 0)

    def test_close_flushes_in_memory_touches(self):
        c = self._cache()
        c.put("u1", self.src)
        c.get("u1")  # last_access 更新只在内存(dirty)
        first_access = c._index[c._key_for("u1")]["last_access"]
        c.close()
        # 重新加载 — last_access 已落盘
        c2 = self._cache()
        self.assertEqual(c2._index[c2._key_for("u1")]["last_access"], first_access)

    def test_half_written_tmp_never_visible_as_cache(self):
        """P2-3: put 中途崩溃(拷贝抛异常)不会留下可命中的坏文件。"""
        from unittest import mock as _mock

        c = self._cache()
        import mediascribe.cache as cache_mod

        with _mock.patch.object(cache_mod.shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                c.put("u1", self.src)
        self.assertIsNone(c.get("u1"))
        leftovers = [p.name for p in (self.tmp / "cache").iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [])


class TestPersistentChunkCacheTtlAndCap(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="v2t_pchunk_cap_"))
        self.src = self.tmp / "source.wav"
        self.src.write_bytes(b"X" * 100)

    def _chunks_dir(self, name: str) -> Path:
        d = self.tmp / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "chunk_000.wav").write_bytes(b"audio")
        return d

    def _cache(self, **kw):
        from mediascribe.cache import PersistentChunkCache

        return PersistentChunkCache(base=self.tmp / "chunk_cache", **kw)

    def _params(self) -> dict:
        return {"chunk_seconds": 60}

    def test_put_leaves_no_tmp_files(self):
        c = self._cache()
        c.put(self.src, self._params(), self._chunks_dir("c1"))
        leftovers = [
            p.name
            for p in (self.tmp / "chunk_cache").rglob("*")
            if p.is_file() and ".tmp" in p.name
        ]
        self.assertEqual(leftovers, [])

    def test_prune_removes_expired_entries(self):
        c = self._cache(ttl_seconds=10)
        c.put(self.src, self._params(), self._chunks_dir("c1"))
        for entry in c._index.values():
            entry["created"] = time.time() - 100
        self._save_index(c)
        n = c.prune()
        self.assertEqual(n, 1)
        self.assertFalse(c.has(self.src, self._params()))
        # chunk 目录也被删除
        self.assertEqual(c.info()["entries"], 0)

    def test_max_entries_lru_eviction(self):
        c = self._cache(max_entries=2)
        p1 = self._params()
        c.put(self.src, p1, self._chunks_dir("c1"))
        src2 = self.tmp / "s2.wav"
        src2.write_bytes(b"Y" * 101)
        src3 = self.tmp / "s3.wav"
        src3.write_bytes(b"Z" * 102)
        c.put(src2, p1, self._chunks_dir("c2"))
        c.get(self.src, p1)  # touch src1 → 最新
        c.put(src3, p1, self._chunks_dir("c3"))  # 超 2 条 → 驱逐 src2(最旧)
        self.assertTrue(c.has(self.src, p1))
        self.assertTrue(c.has(src3, p1))
        self.assertFalse(c.has(src2, p1))

    def _save_index(self, c) -> None:
        from mediascribe.cache import _save_index

        _save_index(c._base, c._name, c._index)


# ---------------------------------------------------------------------------
# Settings.cache_dir — new optional field
# ---------------------------------------------------------------------------


class TestSettingsCacheDir(unittest.TestCase):
    def test_default_is_none(self):
        from mediascribe.config import Settings

        s = Settings(workspace_root=self.tmp_path())
        self.assertIsNone(s.cache_dir)

    def test_explicit_path(self):
        from mediascribe.config import Settings

        custom = Path("/custom/cache")
        s = Settings(workspace_root=self.tmp_path(), cache_dir=custom)
        self.assertEqual(s.cache_dir, custom)

    def tmp_path(self) -> Path:
        import tempfile

        return Path(tempfile.mkdtemp(prefix="v2t_settings_"))


if __name__ == "__main__":
    unittest.main()
