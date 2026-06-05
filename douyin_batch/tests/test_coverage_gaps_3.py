"""Coverage round 3 — fill retry.py / config.py / platform_compat.py gaps."""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# retry.py — download_media_with_retry
# ---------------------------------------------------------------------------


class TestDownloadMediaWithRetry(unittest.TestCase):
    """Tests for retry.download_media_with_retry()."""

    def setUp(self) -> None:
        self.tmp = Path(self._make_tmp())
        self.tmp.mkdir(parents=True, exist_ok=True)

    def _make_tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="v2t_dl_")

    def test_success_first_try(self):
        from douyin_batch.retry import download_media_with_retry

        target = self.tmp / "audio.m4a"
        fake_resp = mock.MagicMock()
        fake_resp.__enter__ = mock.MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = mock.MagicMock(return_value=False)
        fake_resp.raise_for_status = mock.MagicMock()
        fake_resp.iter_content.return_value = [b"chunk1", b"chunk2"]
        with mock.patch("douyin_batch.retry.requests.get", return_value=fake_resp):
            ok = download_media_with_retry("https://example.com/a.m4a", target)
        self.assertTrue(ok)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), b"chunk1chunk2")
        fake_resp.raise_for_status.assert_called_once()

    def test_success_after_retries(self):
        import requests as _req

        from douyin_batch.retry import download_media_with_retry

        target = self.tmp / "r.m4a"
        good = mock.MagicMock()
        good.raise_for_status = mock.MagicMock()
        good.iter_content.return_value = [b"data"]
        # bad must raise a requests.RequestException so retry() catches it
        bad_exc = _req.RequestException("net down")
        with mock.patch(
            "douyin_batch.retry.requests.get",
            side_effect=[bad_exc, bad_exc, good],
        ), mock.patch("douyin_batch.retry.time.sleep") as msleep, mock.patch(
            "douyin_batch.retry.print"
        ):
            ok = download_media_with_retry("u", target, max_retries=3)
        self.assertTrue(ok)
        self.assertEqual(target.read_bytes(), b"data")
        # 2 failures → 2 sleeps before the 3rd success
        self.assertEqual(msleep.call_count, 2)

    def test_returns_false_on_final_failure(self):
        from douyin_batch.retry import download_media_with_retry

        target = self.tmp / "f.m4a"
        with mock.patch(
            "douyin_batch.retry.requests.get",
            side_effect=ConnectionError("no network"),
        ), mock.patch("douyin_batch.retry.time.sleep"), mock.patch(
            "douyin_batch.retry.print"
        ) as mpr:
            ok = download_media_with_retry("u", target, max_retries=2)
        self.assertFalse(ok)
        # No file written
        self.assertFalse(target.exists())
        # At least one final-failure print happened
        printed = " ".join(str(c.args[0]) for c in mpr.call_args_list)
        self.assertIn("下载最终失败", printed)

    def test_creates_parent_dir(self):
        from douyin_batch.retry import download_media_with_retry

        deep = self.tmp / "a" / "b" / "c" / "audio.m4a"
        fake_resp = mock.MagicMock()
        fake_resp.raise_for_status = mock.MagicMock()
        fake_resp.iter_content.return_value = [b"x"]
        with mock.patch("douyin_batch.retry.requests.get", return_value=fake_resp):
            ok = download_media_with_retry("u", deep)
        self.assertTrue(ok)
        self.assertTrue(deep.exists())
        self.assertTrue(deep.parent.exists())

    def test_on_retry_callback_fires(self):
        from douyin_batch.retry import download_media_with_retry

        target = self.tmp / "cb.m4a"
        with mock.patch(
            "douyin_batch.retry.requests.get",
            side_effect=ConnectionError("boom"),
        ), mock.patch("douyin_batch.retry.time.sleep"), mock.patch(
            "douyin_batch.retry.print"
        ) as mpr:
            ok = download_media_with_retry("u", target, max_retries=1)
        self.assertFalse(ok)
        # _on_retry prints ⚠️ ... 第 N 次重试
        warns = [c.args[0] for c in mpr.call_args_list if "重试" in str(c.args[0])]
        self.assertTrue(any("1" in str(w) for w in warns))

    def test_empty_chunks_skipped(self):
        from douyin_batch.retry import download_media_with_retry

        target = self.tmp / "empty.m4a"
        fake_resp = mock.MagicMock()
        fake_resp.raise_for_status = mock.MagicMock()
        # iter_content yields None / empty bytes — code filters them out
        fake_resp.iter_content.return_value = [b"", None, b"real", b""]
        with mock.patch("douyin_batch.retry.requests.get", return_value=fake_resp):
            ok = download_media_with_retry("u", target)
        self.assertTrue(ok)
        self.assertEqual(target.read_bytes(), b"real")


# ---------------------------------------------------------------------------
# config.py — BatchConfig extra paths
# ---------------------------------------------------------------------------


class TestBatchConfigExtras(unittest.TestCase):
    """Tests for BatchConfig.from_env, merge_cli_args, save, __str__."""

    def setUp(self) -> None:
        from douyin_batch import config as cfg_mod

        self._saved_env = dict(os.environ)
        self.tmp = Path(self._make_tmp())
        self.tmp.mkdir(parents=True, exist_ok=True)

    def _make_tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="v2t_cfg_")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)

    def test_from_env_overrides_defaults(self):
        from douyin_batch.config import BatchConfig

        os.environ["DOYIN_BATCH_HEADLESS"] = "false"
        os.environ["DOYIN_BATCH_MAX_VIDEOS"] = "42"
        os.environ["DOYIN_BATCH_WORKERS"] = "8"
        os.environ["DOYIN_BATCH_LANGUAGE"] = "en"
        os.environ["DOYIN_BATCH_MODEL"] = "large-v3"
        os.environ["DOYIN_BATCH_OUTPUT_DIR"] = "/tmp/x"
        os.environ["DOYIN_BATCH_KEEP_AUDIO"] = "1"
        os.environ["DOYIN_BATCH_LOG_LEVEL"] = "DEBUG"
        os.environ["DOYIN_BATCH_MAX_RETRIES"] = "5"
        cfg = BatchConfig.from_env()
        self.assertFalse(cfg.headless)
        self.assertEqual(cfg.max_videos, 42)
        self.assertEqual(cfg.workers, 8)
        self.assertEqual(cfg.language, "en")
        self.assertEqual(cfg.whisper_model, "large-v3")
        self.assertEqual(cfg.output_dir, "/tmp/x")
        self.assertTrue(cfg.keep_audio)
        self.assertEqual(cfg.log_level, "DEBUG")
        self.assertEqual(cfg.max_retries, 5)

    def test_from_env_yes_no_truthy(self):
        from douyin_batch.config import BatchConfig

        os.environ["DOYIN_BATCH_KEEP_AUDIO"] = "yes"
        cfg = BatchConfig.from_env()
        self.assertTrue(cfg.keep_audio)
        os.environ["DOYIN_BATCH_KEEP_AUDIO"] = "no"
        cfg2 = BatchConfig.from_env()
        self.assertFalse(cfg2.keep_audio)

    def test_from_env_missing_keys_keep_defaults(self):
        from douyin_batch.config import BatchConfig

        # No env vars set → all defaults preserved
        for k in list(os.environ):
            if k.startswith("DOYIN_BATCH_"):
                del os.environ[k]
        cfg = BatchConfig.from_env()
        self.assertTrue(cfg.headless)
        self.assertEqual(cfg.max_videos, 10)
        self.assertEqual(cfg.language, "zh")

    def test_merge_cli_args_num(self):
        from douyin_batch.config import BatchConfig

        cfg = BatchConfig()
        args = mock.MagicMock()
        args.num = 25
        args.workers = None
        args.no_headless = False
        args.retries = None
        args.output_dir = "out"
        args.keep_audio = False
        merged = cfg.merge_cli_args(args)
        self.assertEqual(merged.max_videos, 25)

    def test_merge_cli_args_workers(self):
        from douyin_batch.config import BatchConfig

        cfg = BatchConfig()
        args = mock.MagicMock()
        args.num = None
        args.workers = 4
        args.no_headless = False
        args.retries = None
        args.output_dir = "x"
        args.keep_audio = False
        cfg.merge_cli_args(args)
        self.assertEqual(cfg.workers, 4)

    def test_merge_cli_args_no_headless_toggle(self):
        from douyin_batch.config import BatchConfig

        cfg = BatchConfig()  # headless=True by default
        args = mock.MagicMock()
        args.num = None
        args.workers = None
        args.no_headless = True  # → headless=False
        args.retries = None
        args.output_dir = "x"
        args.keep_audio = False
        cfg.merge_cli_args(args)
        self.assertFalse(cfg.headless)

    def test_merge_cli_args_retries(self):
        from douyin_batch.config import BatchConfig

        cfg = BatchConfig()
        args = mock.MagicMock()
        args.num = None
        args.workers = None
        args.no_headless = False
        args.retries = 7
        args.output_dir = "x"
        args.keep_audio = False
        cfg.merge_cli_args(args)
        self.assertEqual(cfg.max_retries, 7)

    def test_save_creates_parent_dir(self):
        from douyin_batch.config import BatchConfig

        deep = self.tmp / "deep" / "nested" / "cfg.json"
        cfg = BatchConfig(max_videos=5, language="en")
        cfg.save(deep)
        self.assertTrue(deep.exists())
        data = json.loads(deep.read_text(encoding="utf-8"))
        self.assertEqual(data["max_videos"], 5)
        self.assertEqual(data["language"], "en")
        # ensure_ascii=False → Chinese 字段名保留
        for k in data:
            self.assertIsInstance(k, str)

    def test_str_contains_all_fields(self):
        from douyin_batch.config import BatchConfig

        cfg = BatchConfig()
        s = str(cfg)
        self.assertIn("📋 当前配置:", s)
        # Every field is mentioned
        for f in ("headless", "max_videos", "language", "whisper_model",
                  "output_dir", "workers", "log_level", "skip_existing"):
            self.assertIn(f, s)

    def test_from_dict_filters_unknown_fields(self):
        from douyin_batch.config import BatchConfig

        data = {"max_videos": 99, "unknown_field": "ignored", "another": 1}
        cfg = BatchConfig.from_dict(data)
        self.assertEqual(cfg.max_videos, 99)
        self.assertFalse(hasattr(cfg, "unknown_field"))

    def test_from_file_missing_returns_defaults(self):
        from douyin_batch.config import BatchConfig

        missing = self.tmp / "absent.json"
        cfg = BatchConfig.from_file(missing)
        self.assertEqual(cfg.max_videos, 10)  # default
        self.assertTrue(cfg.headless)


# ---------------------------------------------------------------------------
# platform_compat.py — find_ffmpeg / install / open_file_location
# ---------------------------------------------------------------------------


class TestFindFfmpeg(unittest.TestCase):
    def setUp(self) -> None:
        from douyin_batch import platform_compat as pc

        self.pc = pc

    def test_found_in_path(self):
        with mock.patch.object(self.pc.shutil, "which", return_value="/usr/bin/ffmpeg"):
            p = self.pc.find_ffmpeg()
        self.assertEqual(p, Path("/usr/bin/ffmpeg"))

    def test_found_in_common_path(self):
        # which() returns None but a common path exists
        with mock.patch.object(self.pc.shutil, "which", return_value=None), mock.patch.object(
            self.pc, "get_os", return_value="linux"
        ), mock.patch.object(self.pc.Path, "exists", return_value=True):
            # the existence check is on Path("/usr/bin/ffmpeg")
            p = self.pc.find_ffmpeg()
        self.assertIsNotNone(p)
        self.assertIn("ffmpeg", str(p))

    def test_not_found(self):
        with mock.patch.object(self.pc.shutil, "which", return_value=None), mock.patch.object(
            self.pc, "get_os", return_value="linux"
        ), mock.patch.object(self.pc.Path, "exists", return_value=False):
            p = self.pc.find_ffmpeg()
        self.assertIsNone(p)

    def test_unknown_os(self):
        with mock.patch.object(self.pc.shutil, "which", return_value=None), mock.patch.object(
            self.pc, "get_os", return_value="plan9"
        ):
            p = self.pc.find_ffmpeg()
        self.assertIsNone(p)


class TestInstallFfmpegInstructions(unittest.TestCase):
    def setUp(self) -> None:
        from douyin_batch import platform_compat as pc

        self.pc = pc

    def test_windows(self):
        with mock.patch.object(self.pc, "get_os", return_value="windows"):
            s = self.pc.install_ffmpeg_instructions()
        self.assertIn("https://www.gyan.dev/ffmpeg/builds/", s)
        self.assertIn("choco install ffmpeg", s)
        self.assertIn("scoop install ffmpeg", s)

    def test_macos(self):
        with mock.patch.object(self.pc, "get_os", return_value="macos"):
            s = self.pc.install_ffmpeg_instructions()
        self.assertIn("brew install ffmpeg", s)
        self.assertIn("sudo port install ffmpeg", s)

    def test_linux(self):
        with mock.patch.object(self.pc, "get_os", return_value="linux"):
            s = self.pc.install_ffmpeg_instructions()
        self.assertIn("sudo apt install ffmpeg", s)
        self.assertIn("dnf install ffmpeg", s)
        self.assertIn("pacman -S ffmpeg", s)

    def test_unknown(self):
        with mock.patch.object(self.pc, "get_os", return_value="plan9"):
            s = self.pc.install_ffmpeg_instructions()
        self.assertIn("Please install it for your OS", s)


class TestOpenFileLocation(unittest.TestCase):
    def setUp(self) -> None:
        from douyin_batch import platform_compat as pc

        self.pc = pc

    def test_windows_uses_explorer(self):
        with mock.patch.object(self.pc, "is_windows", return_value=True), mock.patch(
            "douyin_batch.platform_compat.subprocess.run"
        ) as mr:
            ok = self.pc.open_file_location(Path("file.txt"))
        self.assertTrue(ok)
        args = mr.call_args.args[0]
        self.assertEqual(args[0], "explorer")
        self.assertEqual(args[1], "/select,")
        # 3rd arg is str(path) — on Windows contains "file.txt"
        self.assertIn("file.txt", args[2])

    def test_macos_uses_open(self):
        with mock.patch.object(self.pc, "is_windows", return_value=False), mock.patch(
            "douyin_batch.platform_compat.is_macos", return_value=True
        ), mock.patch(
            "douyin_batch.platform_compat.subprocess.run"
        ) as mr:
            ok = self.pc.open_file_location(Path("video.mp4"))
        self.assertTrue(ok)
        args = mr.call_args.args[0]
        self.assertEqual(args[0], "open")
        self.assertEqual(args[1], "-R")
        self.assertIn("video.mp4", args[2])

    def test_linux_uses_xdg_open(self):
        with mock.patch.object(self.pc, "is_windows", return_value=False), mock.patch(
            "douyin_batch.platform_compat.is_macos", return_value=False
        ), mock.patch(
            "douyin_batch.platform_compat.subprocess.run"
        ) as mr:
            ok = self.pc.open_file_location(Path("video.mp4"))
        self.assertTrue(ok)
        args = mr.call_args.args[0]
        self.assertEqual(args[0], "xdg-open")
        # parent directory of the file
        self.assertEqual(Path(args[1]), Path("video.mp4").parent)

    def test_subprocess_failure_returns_false(self):
        with mock.patch.object(self.pc, "is_windows", return_value=True), mock.patch(
            "douyin_batch.platform_compat.subprocess.run",
            side_effect=OSError("no explorer"),
        ):
            ok = self.pc.open_file_location(Path("C:/x"))
        self.assertFalse(ok)


class TestGetTempDir(unittest.TestCase):
    def test_returns_path(self):
        from douyin_batch.platform_compat import get_temp_dir

        p = get_temp_dir()
        self.assertIsInstance(p, Path)
        self.assertTrue(str(p))  # non-empty


class TestNormalizePath(unittest.TestCase):
    def test_expanduser_and_resolve(self):
        from douyin_batch.platform_compat import normalize_path

        p = normalize_path("~/v.txt")
        self.assertIsInstance(p, Path)
        # resolve() makes it absolute
        self.assertTrue(p.is_absolute())

    def test_relative_path_becomes_absolute(self):
        from douyin_batch.platform_compat import normalize_path

        p = normalize_path("./relative.txt")
        self.assertTrue(p.is_absolute())


class TestGetOsBranches(unittest.TestCase):
    def test_unknown_os(self):
        from douyin_batch import platform_compat as pc

        with mock.patch.object(pc.platform, "system", return_value="Plan9"):
            self.assertEqual(pc.get_os(), "unknown")

    def test_linux(self):
        from douyin_batch import platform_compat as pc

        with mock.patch.object(pc.platform, "system", return_value="Linux"):
            self.assertEqual(pc.get_os(), "linux")

    def test_darwin(self):
        from douyin_batch import platform_compat as pc

        with mock.patch.object(pc.platform, "system", return_value="Darwin"):
            self.assertEqual(pc.get_os(), "macos")

    def test_windows(self):
        from douyin_batch import platform_compat as pc

        with mock.patch.object(pc.platform, "system", return_value="Windows"):
            self.assertEqual(pc.get_os(), "windows")


class TestGetPythonInfo(unittest.TestCase):
    def test_includes_ffmpeg_available(self):
        from douyin_batch.platform_compat import get_python_info

        info = get_python_info()
        self.assertIn("python_version", info)
        self.assertIn("python_implementation", info)
        self.assertIn("os", info)
        self.assertIn("os_version", info)
        self.assertIn("machine", info)
        self.assertIn("ffmpeg_available", info)
        self.assertIsInstance(info["ffmpeg_available"], bool)


if __name__ == "__main__":
    unittest.main()
