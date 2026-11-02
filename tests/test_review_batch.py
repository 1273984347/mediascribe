"""回归测试：批量代码审查修复 (2026-11-03 自 test_review_fixes.py 759 行按主题拆分)。

原文件聚合 17 个审查批次的测试; 拆分后按主题落位, 行为零变化。
本文件覆盖: BatchConfig / 环境变量前缀 / ProcessCache / TranscriberPool / merge_cli_args / process_single_video_safe / get_user_videos
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# P0-3: --config 纯配置文件启动（user_url）
# ---------------------------------------------------------------------------


class TestBatchConfigUserUrl:
    def test_user_url_roundtrip_via_file(self, tmp_path):
        from mediascribe.batch_config import BatchConfig

        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "user_url": "https://www.douyin.com/user/MS4wLjABAAAAtest",
                    "max_videos": 3,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        cfg = BatchConfig.from_file(cfg_path)
        assert cfg.user_url == "https://www.douyin.com/user/MS4wLjABAAAAtest"
        assert cfg.max_videos == 3

    def test_user_url_default_none(self):
        from mediascribe.batch_config import BatchConfig

        assert BatchConfig().user_url is None

    def test_save_load_keeps_user_url(self, tmp_path):
        from mediascribe.batch_config import BatchConfig

        cfg = BatchConfig(user_url="https://www.douyin.com/user/U", max_videos=7)
        p = tmp_path / "save.json"
        cfg.save(p)
        loaded = BatchConfig.from_file(p)
        assert loaded.user_url == "https://www.douyin.com/user/U"


# ---------------------------------------------------------------------------
# P1-1: 环境变量前缀
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P1-1: 环境变量前缀
# ---------------------------------------------------------------------------


class TestEnvPrefix:
    def test_primary_prefix_douyin_batch(self):
        from mediascribe.batch_config import BatchConfig

        with patch.dict(
            "os.environ",
            {"DOUYIN_BATCH_MAX_VIDEOS": "7", "DOUYIN_BATCH_WORKERS": "3"},
            clear=False,
        ):
            cfg = BatchConfig.from_env()
        assert cfg.max_videos == 7
        assert cfg.workers == 3

    def test_legacy_prefix_still_read_with_warning(self):
        from mediascribe import batch_config as cfg_mod
        from mediascribe.batch_config import BatchConfig

        env = {
            "DOUYIN_BATCH_MAX_VIDEOS": "5",
            "DOYIN_BATCH_WORKERS": "4",  # 旧前缀
        }
        with patch.dict("os.environ", env, clear=False):
            with patch.object(cfg_mod, "logger") as mock_log:
                cfg = BatchConfig.from_env()
        assert cfg.max_videos == 5  # 新前缀优先生效
        assert cfg.workers == 4  # 旧前缀兼容读取
        mock_log.warning.assert_called()  # 且给出弃用警告
        assert "DOYIN_BATCH" in mock_log.warning.call_args.args[0]


# ---------------------------------------------------------------------------
# P1-2 / P2-5: 缓存失败可重试 + 原子写 + .bak 保留
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P1-2 / P2-5: 缓存失败可重试 + 原子写 + .bak 保留
# ---------------------------------------------------------------------------


class TestProcessCacheFailedRetryable:
    def test_failed_video_is_retryable(self, tmp_path):
        from mediascribe.batch_cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        cache.mark_processed("v1", "http://x/1", success=False)
        assert cache.is_processed("v1") is False
        videos = [{"video_id": "v1", "url": "http://x/1"}, {"video_id": "v2", "url": "u2"}]
        # v1 失败 → 重新纳入待处理（可重试）；v2 从未处理 → 照常纳入
        assert [v["video_id"] for v in cache.filter_unprocessed(videos)] == ["v1", "v2"]
        # 记录仍保留（统计可见），只是不算已处理
        assert cache.get_processed("v1")["success"] is False

        # 落盘后重新加载，依旧可重试
        again = ProcessCache(cache_dir=tmp_path)
        assert again.filter_unprocessed(videos) == videos

    def test_success_video_still_skipped(self, tmp_path):
        from mediascribe.batch_cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        cache.mark_processed("ok", "u", success=True)
        assert cache.is_processed("ok") is True
        assert cache.filter_unprocessed([{"video_id": "ok"}]) == []

    def test_atomic_save_leaves_no_tmp(self, tmp_path):
        from mediascribe.batch_cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        for i in range(5):
            cache.mark_processed(f"v{i}", "u", success=True)
        assert (tmp_path / "processed_videos.json").exists()
        assert list(tmp_path.glob("*.tmp")) == []

    def test_corrupt_cache_renamed_to_bak(self, tmp_path):
        from mediascribe.batch_cache import ProcessCache

        f = tmp_path / "processed_videos.json"
        f.write_text("{not json", encoding="utf-8")
        cache = ProcessCache(cache_dir=tmp_path)
        assert cache.is_processed("any") is False
        bak = tmp_path / "processed_videos.json.bak"
        assert bak.exists()
        assert bak.read_text(encoding="utf-8") == "{not json"
        # 坏文件已让位，缓存可以重新写入
        cache.mark_processed("v", "u", success=True)
        assert cache.is_processed("v") is True


# ---------------------------------------------------------------------------
# P1-3: js_content 嵌套 div
# ---------------------------------------------------------------------------

HTML_NESTED = """
<html><body>
<div id="js_content">
  <div class="outer">
    <p>第一段在嵌套div之前</p>
    <div class="inner"><p>嵌套div里的正文</p></div>
    <img data-src="https://mmbiz.qpic.cn/after-nested.png">
    <p>嵌套之后还有正文</p>
  </div>
</div>
<p>js_content 外部尾部</p>
</body></html>
"""


# ---------------------------------------------------------------------------
# P1-7: TranscriberPool 接受 BatchConfig
# ---------------------------------------------------------------------------


class TestTranscriberPoolConfig:
    def setup_method(self):
        from mediascribe import transcribe as tmod

        tmod.TranscriberPool._instance = None

    def teardown_method(self):
        from mediascribe import transcribe as tmod

        tmod.TranscriberPool._instance = None

    def test_config_maps_whisper_model_and_language(self):
        from mediascribe import transcribe as tmod

        cfg = SimpleNamespace(whisper_model="medium", language="en")
        with patch("mediascribe.Pipeline") as pipe_cls, patch("mediascribe.Settings") as set_cls:
            pool = tmod.TranscriberPool(config=cfg)
            set_cls.assert_called_once_with(model="medium")
            pool.transcribe(Path("/tmp/a.wav"))
            args, kwargs = pipe_cls.return_value.transcribe.call_args
        assert kwargs.get("language") == "en"
        assert Path(args[0]) == Path("/tmp/a.wav")

    def test_default_keeps_zh_small(self):
        from mediascribe import transcribe as tmod

        with patch("mediascribe.Pipeline") as pipe_cls, patch("mediascribe.Settings") as set_cls:
            pool = tmod.TranscriberPool()
            set_cls.assert_called_once_with(model="small")
            pool.transcribe(Path("/tmp/a.wav"))
            _, kwargs = pipe_cls.return_value.transcribe.call_args
        assert kwargs.get("language") == "zh"

    def test_transcribe_audio_passes_config(self):
        from mediascribe import transcribe as tmod

        with patch.object(tmod, "TranscriberPool") as pool_cls:
            tmod.transcribe_audio(Path("/tmp/x.wav"), config="CFG")
        pool_cls.assert_called_once_with(config="CFG")


# ---------------------------------------------------------------------------
# P2-1: OCR 进度计数 + 顺序回填
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P2-6: merge_cli_args 只覆盖显式参数
# ---------------------------------------------------------------------------


class TestMergeCliArgs:
    def test_none_args_do_not_override_config(self):
        from mediascribe.batch_config import BatchConfig

        cfg = BatchConfig(
            max_videos=5,
            headless=False,
            keep_audio=True,
            output_dir="orig",
            log_level="DEBUG",
            max_retries=9,
        )

        class Args:
            num = None
            workers = None
            no_headless = False
            retries = None
            output_dir = None
            keep_audio = False
            log_level = None

        cfg.merge_cli_args(Args())
        assert cfg.max_videos == 5
        assert cfg.headless is False  # 未传 --no-headless 不覆盖
        assert cfg.keep_audio is True  # 未传 --keep-audio 不覆盖
        assert cfg.output_dir == "orig"
        assert cfg.log_level == "DEBUG"
        assert cfg.max_retries == 9

    def test_explicit_args_override(self):
        from mediascribe.batch_config import BatchConfig

        cfg = BatchConfig()

        class Args:
            num = 9
            workers = 4
            no_headless = True
            retries = 2
            output_dir = "newdir"
            keep_audio = True
            log_level = "WARNING"

        cfg.merge_cli_args(Args())
        assert cfg.max_videos == 9
        assert cfg.workers == 4
        assert cfg.headless is False
        assert cfg.max_retries == 2
        assert cfg.output_dir == "newdir"
        assert cfg.keep_audio is True
        assert cfg.log_level == "WARNING"


# ---------------------------------------------------------------------------
# P2-7: 下载前 URL 安全检查 + 文件名清洗
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P2-7: 下载前 URL 安全检查 + 文件名清洗
# ---------------------------------------------------------------------------


class TestProcessSingleVideoSafety:
    def _run(self, tmp_path, media_url, download_fn):
        import mediascribe.batch_cli as v3

        cfg = SimpleNamespace(headless=True, max_retries=1, max_wait_for_media=1)
        return v3.process_single_video_safe(
            video={"video_id": "123", "url": "https://www.douyin.com/video/123"},
            index=1,
            total=1,
            download_dir=tmp_path,
            config=cfg,
            get_media_url_fn=lambda *a, **k: media_url,
            download_media_fn=download_fn,
            transcribe_fn=lambda p: None,
            log=MagicMock(),
        )

    def test_untrusted_media_url_skipped_before_download(self, tmp_path):
        calls = []

        def fake_download(url, path, max_retries=3):
            calls.append(url)
            return True

        result = self._run(tmp_path, "https://evil.example.com/x.mp4", fake_download)
        assert result["status"] == "failed"
        assert result["stage"] == "media_url"
        assert "unsafe" in (result.get("error") or "")
        assert calls == []  # 未进入下载

    def test_trusted_media_url_downloads_to_sanitized_name(self, tmp_path):
        seen = {}

        def fake_download(url, path, max_retries=3):
            seen["url"] = url
            seen["path"] = Path(path)
            return False  # 下载失败即可，只验证调用参数

        result = self._run(tmp_path, "https://v26.douyinvod.com/media-audio/x.mp4", fake_download)
        assert seen["url"] == "https://v26.douyinvod.com/media-audio/x.mp4"
        assert seen["path"].name == "123.mp4"  # 安全清洗不改变正常 ID
        assert ".." not in seen["path"].name

    def test_malicious_video_id_sanitized(self, tmp_path):
        import mediascribe.batch_cli as v3

        seen = {}

        def fake_download(url, path, max_retries=3):
            seen["path"] = Path(path)
            return False

        cfg = SimpleNamespace(headless=True, max_retries=1, max_wait_for_media=1)
        v3.process_single_video_safe(
            video={"video_id": "../../evil", "url": "https://www.douyin.com/video/x"},
            index=1,
            total=1,
            download_dir=tmp_path,
            config=cfg,
            get_media_url_fn=lambda *a, **k: "https://www.douyinvod.com/x.mp4",
            download_media_fn=fake_download,
            transcribe_fn=lambda p: None,
            log=MagicMock(),
        )
        assert ".." not in seen["path"].name
        assert "/" not in seen["path"].name and "\\" not in seen["path"].name
        # 文件不会逃出 download_dir
        assert seen["path"].parent == tmp_path


# ---------------------------------------------------------------------------
# P2-8: url_utils 主机名精确匹配
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P2-13: get_user_videos 滚动参数注入
# ---------------------------------------------------------------------------


class TestGetUserVideosScrollParams:
    def test_max_scroll_rounds_caps_scrolling(self):
        from mediascribe import browser as br_mod

        html_by_round = [
            '<html><a href="/video/1">v</a></html>',
            '<html><a href="/video/2">v</a></html>',
            '<html><a href="/video/3">v</a></html>',
        ]
        page = MagicMock(name="Page")
        seq = list(html_by_round)
        page.content.side_effect = lambda: seq.pop(0) if seq else html_by_round[-1]

        mgr = br_mod.BrowserManager.__new__(br_mod.BrowserManager)
        mgr._initialized = True
        mgr._headless = True
        mgr._context = MagicMock()
        mgr._context.new_page.return_value = page
        br_mod.BrowserManager._instance = mgr
        try:
            with patch("mediascribe.browser.time.sleep"):
                videos = br_mod.get_user_videos(
                    "https://www.douyin.com/user/X",
                    max_videos=10,
                    initial_wait=0,
                    scroll_pause=0,
                    max_scroll_rounds=2,
                )
        finally:
            br_mod.BrowserManager._instance = None
        ids = sorted(v["video_id"] for v in videos)
        # 只滚动 2 轮 → 只收集 video/1 与 video/2（旧公式会是 3 轮拿到 3 个）
        assert ids == ["1", "2"]


# ---------------------------------------------------------------------------
# P2-15: 汇总报告 ASCII 文件名
# ---------------------------------------------------------------------------
