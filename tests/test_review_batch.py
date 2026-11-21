"""回归测试：批量代码审查修复 (2026-11-03 自 test_review_fixes.py 759 行按主题拆分)。

原文件聚合 17 个审查批次的测试; 拆分后按主题落位, 行为零变化。
本文件覆盖: BatchConfig / 环境变量前缀 / ProcessCache / TranscriberPool / merge_cli_args / process_single_video_safe / get_user_videos
"""

from __future__ import annotations

import json
import sys
import time
import unittest
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
    def test_config_maps_whisper_model_and_language(self):
        from mediascribe import transcribe as tmod

        cfg = SimpleNamespace(whisper_model="medium", language="en")
        with patch("mediascribe.Pipeline") as pipe_cls, patch("mediascribe.Settings") as set_cls:
            pool = tmod.TranscriberPool(config=cfg)
            from mediascribe.config import default_engine

            set_cls.assert_called_once_with(model="medium", engine=default_engine())
            pool.transcribe(Path("/tmp/a.wav"))
            args, kwargs = pipe_cls.return_value.transcribe.call_args
        assert kwargs.get("language") == "en"
        assert Path(args[0]) == Path("/tmp/a.wav")

    def test_default_keeps_zh_small(self):
        from mediascribe import transcribe as tmod

        with patch("mediascribe.Pipeline") as pipe_cls, patch("mediascribe.Settings") as set_cls:
            pool = tmod.TranscriberPool()
            from mediascribe.config import default_engine

            set_cls.assert_called_once_with(model="small", engine=default_engine())
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


# ---------------------------------------------------------------------------
# P1-1(2026-11-20 架构评审): 批处理线新增引擎维度
# ---------------------------------------------------------------------------
class TestBatchEngineField:
    def test_engine_env_wiring(self):
        from mediascribe.batch_config import BatchConfig

        with patch.dict("os.environ", {"DOUYIN_BATCH_ENGINE": "faster-whisper"}, clear=False):
            cfg = BatchConfig.from_env()
        assert cfg.engine == "faster-whisper"

    def test_engine_default_none_follows_probe(self):
        from mediascribe.batch_config import BatchConfig
        from mediascribe.config import default_engine

        assert BatchConfig().engine is None
        # TranscriberPool 侧: config.engine 缺省 → default_engine() 探测
        from types import SimpleNamespace

        from mediascribe import transcribe as tmod

        cfg = SimpleNamespace(whisper_model="small", language="zh")
        with (
            mock.patch("mediascribe.Pipeline"),
            mock.patch("mediascribe.Settings") as SetCls,
        ):
            tmod.TranscriberPool(config=cfg)
        SetCls.assert_called_once_with(model="small", engine=default_engine())


# ---------------------------------------------------------------------------
# P1-5(2026-11-20 架构评审): 下载失败分类路由
# ---------------------------------------------------------------------------
class TestDownloadFailureRouting:
    def _run(self, tmp_path, download_fn):
        import mediascribe.batch_cli as v3

        cfg = SimpleNamespace(headless=True, max_retries=1, max_wait_for_media=1)
        return v3.process_single_video_safe(
            video={"video_id": "123", "url": "https://www.douyin.com/video/123"},
            index=1,
            total=1,
            download_dir=tmp_path,
            config=cfg,
            get_media_url_fn=lambda *a, **k: "https://v26.douyinvod.com/media-audio/x.mp4",
            download_media_fn=download_fn,
            transcribe_fn=lambda p: None,
            log=MagicMock(),
        )

    def test_gone_download_error_classified(self, tmp_path):
        from mediascribe.downloaders.download_errors import (
            CATEGORY_GONE,
            DownloadError,
        )

        def boom(url, path, max_retries=3):
            raise DownloadError(CATEGORY_GONE, "404 not found", status=404)

        result = self._run(tmp_path, boom)
        assert result["status"] == "failed"
        assert result["stage"] == "download"
        assert result["failure_class"] == "gone"

    def test_bot_wall_download_error_classified(self, tmp_path):
        from mediascribe.downloaders.download_errors import (
            CATEGORY_BOT_WALL,
            DownloadError,
        )

        def boom(url, path, max_retries=3):
            raise DownloadError(CATEGORY_BOT_WALL, "403", status=403)

        result = self._run(tmp_path, boom)
        assert result["failure_class"] == "bot_wall"

    def test_timeout_classified_transient(self, tmp_path):
        import requests as _rq

        def boom(url, path, max_retries=3):
            raise _rq.Timeout("timed out")

        result = self._run(tmp_path, boom)
        assert result["failure_class"] == "transient"

    def test_legacy_falsy_return_has_no_class(self, tmp_path):
        """旧契约:下载函数返回 False(无异常)→ 无类别,下轮照旧重试。"""
        result = self._run(tmp_path, lambda url, path, max_retries=3: False)
        assert "failure_class" not in result  # 无类别 → 下轮照旧重试


class TestGoneVideosSkippedOnResume:
    def test_mark_gone_then_filter_skips(self, tmp_path):
        from mediascribe.batch_cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path / "cache")
        video = {"video_id": "gone1", "url": "https://www.douyin.com/video/gone1"}
        cache.mark_processed(
            video_id="gone1",
            video_url=video["url"],
            success=False,
            failure_class="gone",
        )
        assert not cache.is_processed("gone1")  # 失败不算已处理
        assert cache.is_permanently_failed("gone1")
        assert cache.filter_unprocessed([video]) == []  # 断点续传直接跳过

    def test_transient_failure_still_retried(self, tmp_path):
        from mediascribe.batch_cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path / "cache")
        video = {"video_id": "t1", "url": "https://www.douyin.com/video/t1"}
        cache.mark_processed(
            video_id="t1",
            video_url=video["url"],
            success=False,
            failure_class="transient",
        )
        assert cache.filter_unprocessed([video]) == [video]

    def test_success_clears_gone_mark(self, tmp_path):
        from mediascribe.batch_cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path / "cache")
        video = {"video_id": "s1", "url": "https://www.douyin.com/video/s1"}
        cache.mark_processed(
            video_id="s1", video_url=video["url"], success=False, failure_class="gone"
        )
        cache.mark_processed(video_id="s1", video_url=video["url"], success=True)
        assert cache.filter_unprocessed([video]) == []


class TestBotWallBackoff:
    def test_record_bot_wall_sleeps_exponentially(self, tmp_path, monkeypatch):
        import mediascribe.batch_cli as v3
        from mediascribe.batch_cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path / "cache")
        sleeps: list = []
        monkeypatch.setattr(v3.time, "sleep", lambda sec: sleeps.append(sec))
        recorder = v3._ResultRecorder(
            tracker=MagicMock(), cache=cache, log=MagicMock(), t=lambda key, **kw: key
        )

        def failed(fc):
            return {
                "video_id": "v",
                "url": "https://www.douyin.com/video/v",
                "status": "failed",
                "stage": "download",
                "failure_class": fc,
            }

        recorder.record(failed("bot_wall"), 1.0, "w")
        recorder.record(failed("bot_wall"), 1.0, "w")
        recorder.record(failed("bot_wall"), 1.0, "w")
        # 指数退避: 2, 4, 8(streak 从 1 起)
        assert sleeps == [2.0, 4.0, 8.0]

    def test_success_resets_streak(self, tmp_path, monkeypatch):
        import mediascribe.batch_cli as v3
        from mediascribe.batch_cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path / "cache")
        sleeps: list = []
        monkeypatch.setattr(v3.time, "sleep", lambda sec: sleeps.append(sec))
        recorder = v3._ResultRecorder(
            tracker=MagicMock(), cache=cache, log=MagicMock(), t=lambda key, **kw: key
        )

        recorder.record(
            {
                "video_id": "v",
                "url": "u",
                "status": "failed",
                "stage": "download",
                "failure_class": "bot_wall",
            },
            1.0,
            "w",
        )
        recorder.record(
            {
                "video_id": "v2",
                "url": "u2",
                "status": "success",
                "transcript": "t",
                "audio": "a",
            },
            1.0,
            "w",
        )
        recorder.record(
            {
                "video_id": "v3",
                "url": "u3",
                "status": "failed",
                "stage": "download",
                "failure_class": "bot_wall",
            },
            1.0,
            "w",
        )
        assert sleeps == [2.0, 2.0], "成功后 streak 归零,退避回到第一档"

    def test_report_counts_failure_classes(self, tmp_path):
        from mediascribe.report import generate_summary_report

        results = [
            {
                "video_id": "a",
                "url": "https://www.douyin.com/video/a",
                "status": "failed",
                "stage": "download",
                "failure_class": "gone",
            },
            {
                "video_id": "b",
                "url": "https://www.douyin.com/video/b",
                "status": "failed",
                "stage": "download",
                "failure_class": "bot_wall",
            },
            {
                "video_id": "c",
                "url": "https://www.douyin.com/video/c",
                "status": "failed",
                "stage": "exception",
            },
        ]
        report_path = generate_summary_report(
            user_url="https://www.douyin.com/user/x",
            results=results,
            output_dir=tmp_path,
        )
        text = report_path.read_text(encoding="utf-8")
        assert "失败类别: gone" in text
        assert "类别: bot_wall" in text
        assert "bot_wall×1" in text and "gone×1" in text


# ---------------------------------------------------------------------------
# P2-5(2026-11-20 架构评审): 孤儿 .part 清扫 + 视频列表缓存 TTL
# ---------------------------------------------------------------------------
class TestOrphanPartSweep:
    def test_old_part_swept_fresh_kept(self, tmp_path):
        import os
        import time as _t

        import mediascribe.batch_cli as v3

        old_part = tmp_path / "v1.mp4.abc.part"
        old_part.write_bytes(b"x")
        fresh_part = tmp_path / "v2.mp4.def.part"
        fresh_part.write_bytes(b"x")
        # old 拨到 25h 前
        old_ts = _t.time() - 25 * 3600
        os.utime(old_part, (old_ts, old_ts))

        removed = v3._sweep_orphan_parts(
            tmp_path, max_age_hours=24, log=MagicMock(), i18n_t=lambda k, **kw: k
        )
        assert removed == 1
        assert not old_part.exists()
        assert fresh_part.exists()

    def test_no_part_files_noop(self, tmp_path):
        import mediascribe.batch_cli as v3

        assert (
            v3._sweep_orphan_parts(
                tmp_path, max_age_hours=24, log=MagicMock(), i18n_t=lambda k, **kw: k
            )
            == 0
        )


class TestVideoListTtl:
    def test_expired_list_returns_empty(self, tmp_path):
        import json
        from datetime import datetime, timedelta

        from mediascribe.batch_cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path / "cache")
        videos = [{"video_id": "v1", "url": "https://www.douyin.com/video/v1"}]
        cache.save_user_videos("https://www.douyin.com/user/x", videos)
        # 手动把 cached_at 拨老
        cache._cache_data["users"][next(iter(cache._cache_data["users"]))]["cached_at"] = (
            datetime.now() - timedelta(hours=48)
        ).isoformat()
        cache._save()
        assert cache.get_user_videos("https://www.douyin.com/user/x", ttl_hours=24) == []
        assert cache.get_user_videos("https://www.douyin.com/user/x", ttl_hours=72) == videos

    def test_fresh_list_default_ttl_valid(self, tmp_path):
        from mediascribe.batch_cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path / "cache")
        videos = [{"video_id": "v1", "url": "u"}]
        cache.save_user_videos("https://www.douyin.com/user/x", videos)
        assert cache.get_user_videos("https://www.douyin.com/user/x") == videos

    def test_ttl_zero_disables(self, tmp_path):
        from datetime import datetime, timedelta

        from mediascribe.batch_cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path / "cache")
        cache.save_user_videos("https://www.douyin.com/user/x", [{"video_id": "v1"}])
        cache._cache_data["users"][next(iter(cache._cache_data["users"]))]["cached_at"] = (
            datetime.now() - timedelta(hours=999)
        ).isoformat()
        cache._save()
        assert cache.get_user_videos("https://www.douyin.com/user/x", ttl_hours=0) != []

    def test_batch_config_env_wiring(self):
        from mediascribe.batch_config import BatchConfig

        with patch.dict("os.environ", {"DOUYIN_BATCH_LIST_TTL_HOURS": "0"}, clear=False):
            assert BatchConfig.from_env().list_ttl_hours == 0
        assert BatchConfig().list_ttl_hours == 24


# ---------------------------------------------------------------------------
# P2-6(2026-11-20 架构评审): 磁盘空间守卫
# ---------------------------------------------------------------------------
class TestDiskGuard:
    def test_min_free_default_is_2x_max_download(self, tmp_path):
        from mediascribe.platform_compat import min_free_disk_mb

        with patch.dict("os.environ", {}, clear=False):
            import os as _os

            _os.environ.pop("MEDIASCRIBE_MIN_FREE_MB", None)
            _os.environ.pop("MEDIASCRIBE_MAX_DOWNLOAD_MB", None)
            assert min_free_disk_mb() == 2 * 4096

    def test_min_free_override(self):
        from mediascribe.platform_compat import min_free_disk_mb

        with patch.dict("os.environ", {"MEDIASCRIBE_MIN_FREE_MB": "500"}, clear=False):
            assert min_free_disk_mb() == 500

    def test_free_disk_mb_oserror_returns_none(self, tmp_path):
        from mediascribe.platform_compat import free_disk_mb

        with patch(
            "mediascribe.platform_compat.shutil.disk_usage",
            side_effect=OSError("no such volume"),
        ):
            assert free_disk_mb(tmp_path) is None

    def test_free_disk_mb_real(self, tmp_path):
        from mediascribe.platform_compat import free_disk_mb

        value = free_disk_mb(tmp_path)
        assert value is None or value > 0

    def test_web_submit_507_when_disk_low(self, tmp_path):
        """验收指标(P2-6): mock 磁盘低水位,提交路径返回明确错误码。"""
        from unittest.mock import patch as _patch

        try:
            from fastapi.testclient import TestClient
        except ImportError:  # pragma: no cover - optional dep
            import pytest

            pytest.skip("fastapi not installed")

        from mediascribe.web.app import create_app

        app_obj = create_app(workspace=tmp_path)
        client = TestClient(app_obj)
        try:
            with (
                _patch("mediascribe.platform_compat.free_disk_mb", return_value=1),
                _patch("mediascribe.platform_compat.min_free_disk_mb", return_value=100),
            ):
                r = client.post("/api/jobs", json={"urls": ["https://example.com/x.mp4"]})
            assert r.status_code == 507, r.text
            assert "insufficient disk space" in r.json()["detail"]
        finally:
            store = getattr(app_obj.state, "job_store", None)
            if store is not None:
                store.close()


class TestBatchEnvPrefixConvergence(unittest.TestCase):
    """R-4(2026-11-21 架构评审): 前缀收敛与空串健壮性。"""

    def test_mediascribe_prefix_is_primary(self):
        import os
        from unittest.mock import patch

        from mediascribe.batch_config import BatchConfig

        with patch.dict(os.environ, {"MEDIASCRIBE_BATCH_MAX_VIDEOS": "9"}, clear=False):
            cfg = BatchConfig.from_env()
        self.assertEqual(cfg.max_videos, 9)

    def test_legacy_douyin_prefix_still_reads_with_warning(self):
        import logging
        import os
        from unittest.mock import patch

        from mediascribe.batch_config import BatchConfig

        with patch.dict(
            os.environ,
            {"DOUYIN_BATCH_WORKERS": "2", "MEDIASCRIBE_BATCH_WORKERS": ""},
            clear=False,
        ):
            with self.assertLogs("mediascribe.batch_config", level=logging.WARNING):
                cfg = BatchConfig.from_env()
        self.assertEqual(cfg.workers, 2)

    def test_empty_env_value_treated_as_unset(self):
        import os
        from unittest.mock import patch

        from mediascribe.batch_config import BatchConfig

        with patch.dict(
            os.environ,
            {"MEDIASCRIBE_BATCH_WORKERS": "", "MEDIASCRIBE_BATCH_MAX_VIDEOS": ""},
            clear=False,
        ):
            cfg = BatchConfig.from_env()
        self.assertEqual(cfg.workers, BatchConfig().workers)
        self.assertEqual(cfg.max_videos, BatchConfig().max_videos)
