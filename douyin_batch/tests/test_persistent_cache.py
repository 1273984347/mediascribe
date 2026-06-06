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
        for k in ("VIDEO2TEXT_CACHE_DIR", "XDG_CACHE_HOME"):
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
        from video2text.cache import persistent_cache_dir

        p = persistent_cache_dir(override=Path("/custom/cache"))
        self.assertEqual(p, Path("/custom/cache"))

    def test_env_var_wins(self):
        from video2text.cache import persistent_cache_dir

        os.environ["VIDEO2TEXT_CACHE_DIR"] = "/env/cache"
        p = persistent_cache_dir()
        self.assertEqual(p, Path("/env/cache"))

    def test_xdg_cache_home(self):
        from video2text.cache import persistent_cache_dir

        os.environ["XDG_CACHE_HOME"] = "/xdg"
        p = persistent_cache_dir()
        self.assertEqual(p, Path("/xdg/video2text"))

    def test_app_name(self):
        from video2text.cache import persistent_cache_dir

        os.environ["XDG_CACHE_HOME"] = "/xdg"
        p = persistent_cache_dir("myapp")
        self.assertEqual(p, Path("/xdg/myapp"))

    def test_fallback_to_home(self):
        from video2text.cache import persistent_cache_dir

        # On Windows, %LOCALAPPDATA% takes precedence.  On Linux/macOS
        # the .cache fallback is used.  Either way it should point
        # inside the user's home / appdata, not at /tmp.
        with mock.patch.object(Path, "home", return_value=Path("/home/x")):
            p = persistent_cache_dir()
        self.assertIn(str(p), [
            str(Path("/home/x/.cache/video2text")),
            str(Path("C:/Users/12739/AppData/Local/video2text/Cache")),
        ])


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
        from video2text.cache import PersistentDownloadCache

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
    from video2text.cache import _save_index
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
        from video2text.cache import PersistentChunkCache

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
# Settings.cache_dir — new optional field
# ---------------------------------------------------------------------------


class TestSettingsCacheDir(unittest.TestCase):
    def test_default_is_none(self):
        from video2text.config import Settings

        s = Settings(workspace_root=self.tmp_path())
        self.assertIsNone(s.cache_dir)

    def test_explicit_path(self):
        from video2text.config import Settings

        custom = Path("/custom/cache")
        s = Settings(workspace_root=self.tmp_path(), cache_dir=custom)
        self.assertEqual(s.cache_dir, custom)

    def tmp_path(self) -> Path:
        import tempfile

        return Path(tempfile.mkdtemp(prefix="v2t_settings_"))


if __name__ == "__main__":
    unittest.main()
