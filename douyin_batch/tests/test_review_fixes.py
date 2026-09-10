"""回归测试：批量代码审查修复（P0/P1/P2 系列）。

覆盖的关键修复：
- P0-2  douyin._extract_media_url_with_browser 无 playwright 时返回 (None, None)
- P0-3  BatchConfig.user_url 字段（--config 纯配置文件启动）
- P1-1  环境变量主前缀 DOUYIN_BATCH_*（兼容旧 DOYIN_BATCH_*）
- P1-2  失败视频不进断点续传黑名单（可重试）；v3 收尾跳过失败项文件
- P1-3  wechat_mp js_content 嵌套 div 不截断（_extract_div_block）
- P1-4  BrowserManager.close_if_running 不凭空启动浏览器
- P1-5  小红书图文笔记明确报错
- P1-6  stream_download：.part 临时文件 + 失败清理 + 覆盖写防护
- P1-7  TranscriberPool 接受 BatchConfig（whisper_model / language 映射）
- P2-1  _ocr_images 独立计数 + 按输入顺序回填
- P2-5  ProcessCache 原子写 + 损坏 .bak 保留
- P2-6  merge_cli_args 只覆盖显式提供的参数
- P2-7  process_single_video_safe 下载前 URL 安全检查 + 文件名清洗
- P2-8  url_utils 主机名精确匹配（b23.tv@127.0.0.1 绕过被封堵）
- P2-13 get_user_videos 滚动节奏参数注入
- P2-15 汇总报告 ASCII 文件名
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# P0-2: 无 playwright 回退不再崩
# ---------------------------------------------------------------------------


class TestDouyinNoPlaywrightFallback:
    def test_returns_none_pair_when_playwright_missing(self):
        from mediascribe.downloaders.douyin import DouyinDownloader

        d = DouyinDownloader()
        # sys.modules 中置 None 会让 `from playwright.sync_api import ...`
        # 抛 ImportError —— 无论环境里是否真的装了 playwright
        with patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
            result = d._extract_media_url_with_browser("https://www.douyin.com/video/1")
        assert result == (None, None)

    def test_download_raises_cleanly_when_no_media_url(self, tmp_path):
        from mediascribe.config import Settings
        from mediascribe.downloaders.douyin import DouyinDownloader
        from mediascribe.models import SourceRef

        d = DouyinDownloader()
        with patch.object(
            d, "_extract_media_url_with_browser", return_value=(None, None)
        ):
            with pytest.raises(RuntimeError, match="媒体 URL"):
                d.download(
                    SourceRef(raw_input="x", kind="douyin", url="https://www.douyin.com/video/1"),
                    Settings(workspace_root=tmp_path),
                )


# ---------------------------------------------------------------------------
# P0-3: --config 纯配置文件启动（user_url）
# ---------------------------------------------------------------------------


class TestBatchConfigUserUrl:
    def test_user_url_roundtrip_via_file(self, tmp_path):
        from douyin_batch.config import BatchConfig

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
        from douyin_batch.config import BatchConfig

        assert BatchConfig().user_url is None

    def test_save_load_keeps_user_url(self, tmp_path):
        from douyin_batch.config import BatchConfig

        cfg = BatchConfig(user_url="https://www.douyin.com/user/U", max_videos=7)
        p = tmp_path / "save.json"
        cfg.save(p)
        loaded = BatchConfig.from_file(p)
        assert loaded.user_url == "https://www.douyin.com/user/U"


# ---------------------------------------------------------------------------
# P1-1: 环境变量前缀
# ---------------------------------------------------------------------------


class TestEnvPrefix:
    def test_primary_prefix_douyin_batch(self):
        from douyin_batch.config import BatchConfig

        with patch.dict(
            "os.environ",
            {"DOUYIN_BATCH_MAX_VIDEOS": "7", "DOUYIN_BATCH_WORKERS": "3"},
            clear=False,
        ):
            cfg = BatchConfig.from_env()
        assert cfg.max_videos == 7
        assert cfg.workers == 3

    def test_legacy_prefix_still_read_with_warning(self):
        from douyin_batch import config as cfg_mod
        from douyin_batch.config import BatchConfig

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


class TestProcessCacheFailedRetryable:
    def test_failed_video_is_retryable(self, tmp_path):
        from douyin_batch.cache import ProcessCache

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
        from douyin_batch.cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        cache.mark_processed("ok", "u", success=True)
        assert cache.is_processed("ok") is True
        assert cache.filter_unprocessed([{"video_id": "ok"}]) == []

    def test_atomic_save_leaves_no_tmp(self, tmp_path):
        from douyin_batch.cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        for i in range(5):
            cache.mark_processed(f"v{i}", "u", success=True)
        assert (tmp_path / "processed_videos.json").exists()
        assert list(tmp_path.glob("*.tmp")) == []

    def test_corrupt_cache_renamed_to_bak(self, tmp_path):
        from douyin_batch.cache import ProcessCache

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


class TestWechatMpJsContentNestedDiv:
    def setup_method(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        self.d = WechatMpDownloader()

    def test_extract_div_block_balances_tags(self):
        block = self.d._extract_div_block(HTML_NESTED, 'id="js_content"')
        assert block is not None
        assert "嵌套div里的正文" in block
        assert "嵌套之后还有正文" in block
        assert "js_content 外部尾部" not in block

    def test_extract_div_block_single_quote_anchor(self):
        html = "<div ID='js_content'><p>单引号</p></div>"
        assert "单引号" in self.d._extract_div_block(html, 'id="js_content"')

    def test_extract_div_block_missing_anchor(self):
        assert self.d._extract_div_block("<html></html>", 'id="js_content"') is None

    def test_extract_text_not_truncated_by_inner_div(self):
        text = self.d._extract_text(HTML_NESTED)
        assert "第一段在嵌套div之前" in text
        assert "嵌套div里的正文" in text  # 旧正则会在这里被第一个 </div> 截断
        assert "嵌套之后还有正文" in text
        assert "js_content 外部尾部" not in text

    def test_extract_image_urls_after_nested_div(self):
        urls = self.d._extract_image_urls(HTML_NESTED)
        assert "https://mmbiz.qpic.cn/after-nested.png" in urls


# ---------------------------------------------------------------------------
# P1-4: close_if_running
# ---------------------------------------------------------------------------


class TestBrowserCloseIfRunning:
    def setup_method(self):
        from douyin_batch import browser as br_mod

        br_mod.BrowserManager._instance = None

    def teardown_method(self):
        from douyin_batch import browser as br_mod

        br_mod.BrowserManager._instance = None

    def test_no_instance_does_not_start_browser(self):
        from douyin_batch import browser as br_mod

        # 未 patch playwright：如果实现错误地启动了浏览器，此测试会失败
        br_mod.BrowserManager.close_if_running()
        assert br_mod.BrowserManager._instance is None

    def test_existing_instance_is_closed(self):
        from douyin_batch import browser as br_mod

        inst = MagicMock()
        br_mod.BrowserManager._instance = inst
        br_mod.BrowserManager.close_if_running()
        inst.close.assert_called_once()
        # close() 会把 _instance 复位为 None（这里手动模拟）
        br_mod.BrowserManager._instance = None

    def test_headless_mismatch_keeps_existing_instance(self):
        from douyin_batch import browser as br_mod

        inst = br_mod.BrowserManager.__new__(br_mod.BrowserManager)
        inst._initialized = True
        inst._headless = True
        br_mod.BrowserManager._instance = inst
        with patch.object(br_mod, "logger") as mock_log:
            mgr = br_mod.BrowserManager(headless=False)
        assert mgr is inst  # 沿用现有实例，不重建
        mock_log.warning.assert_called_once()  # 但给出参数不一致告警


# ---------------------------------------------------------------------------
# P1-5: 小红书图文笔记明确报错
# ---------------------------------------------------------------------------


class TestXiaohongshuImageNote:
    def test_image_note_raises_actionable_error(self, tmp_path):
        from mediascribe.config import Settings
        from mediascribe.downloaders.xiaohongshu import XiaohongshuDownloader
        from mediascribe.models import SourceRef

        d = XiaohongshuDownloader()
        with patch.object(
            d,
            "_extract_media_and_meta",
            return_value=("https://sns-img-bd.xhscdn.com/cover.jpg", "标题", "image"),
        ):
            with pytest.raises(RuntimeError, match="图文笔记"):
                d.download(
                    SourceRef(raw_input="x", kind="xiaohongshu", url="https://www.xiaohongshu.com/explore/1"),
                    Settings(workspace_root=tmp_path),
                )


# ---------------------------------------------------------------------------
# P1-6: stream_download
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, chunks, total=100):
        self._chunks = list(chunks)
        self.headers = {"content-length": str(total)}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192):
        yield from self._chunks


class _FakeSession:
    def __init__(self, chunks, total=100, error=None):
        self._resp = _FakeResp(chunks, total)
        self._error = error

    def get(self, *a, **k):
        if self._error is not None:
            raise self._error
        return self._resp

    def close(self):
        pass


class TestStreamDownload:
    def test_success_writes_dest(self, tmp_path):
        from mediascribe.downloaders._http_download import stream_download

        dest = tmp_path / "out.mp4"
        out = stream_download("https://x/v.mp4", dest, session=_FakeSession([b"a" * 10, b"b" * 5]))
        assert out == dest
        assert dest.read_bytes() == b"a" * 10 + b"b" * 5
        assert list(tmp_path.glob("*part*")) == []  # 无残留临时文件

    def test_failure_cleans_part_and_keeps_old_dest(self, tmp_path):
        from mediascribe.downloaders._http_download import stream_download

        dest = tmp_path / "out.mp4"
        dest.write_bytes(b"OLD-GOOD")
        broken = _FakeSession([b"partial"], error=None)
        # iter_content 中途抛错
        def _iter(chunk_size=8192):
            yield b"partial-data"
            raise ConnectionError("boom")

        broken._resp.iter_content = _iter
        with pytest.raises(ConnectionError):
            stream_download("https://x/v.mp4", dest, session=broken)
        # 旧文件未被破坏；无 .part 残留
        assert dest.read_bytes() == b"OLD-GOOD"
        assert list(tmp_path.glob("*part*")) == []

    def test_http_error_cleans_part(self, tmp_path):
        import requests as _rq

        from mediascribe.downloaders._http_download import stream_download

        dest = tmp_path / "out.mp4"
        sess = _FakeSession([], error=_rq.HTTPError("404"))
        with pytest.raises(Exception):
            stream_download("https://x/v.mp4", dest, session=sess)
        assert not dest.exists()
        assert list(tmp_path.glob("*part*")) == []

    def test_progress_cb_called(self, tmp_path):
        from mediascribe.downloaders._http_download import stream_download

        seen = []
        stream_download(
            "https://x/v.mp4",
            tmp_path / "out.mp4",
            session=_FakeSession([b"x" * 30, b"y" * 30], total=60),
            progress_cb=lambda done, total: seen.append((done, total)),
        )
        assert seen[-1] == (60, 60)


# ---------------------------------------------------------------------------
# P1-7: TranscriberPool 接受 BatchConfig
# ---------------------------------------------------------------------------


class TestTranscriberPoolConfig:
    def setup_method(self):
        from douyin_batch import transcribe as tmod

        tmod.TranscriberPool._instance = None

    def teardown_method(self):
        from douyin_batch import transcribe as tmod

        tmod.TranscriberPool._instance = None

    def test_config_maps_whisper_model_and_language(self):
        from douyin_batch import transcribe as tmod

        cfg = SimpleNamespace(whisper_model="medium", language="en")
        with patch("mediascribe.Pipeline") as pipe_cls, patch("mediascribe.Settings") as set_cls:
            pool = tmod.TranscriberPool(config=cfg)
            set_cls.assert_called_once_with(model="medium")
            pool.transcribe(Path("/tmp/a.wav"))
            args, kwargs = pipe_cls.return_value.transcribe.call_args
        assert kwargs.get("language") == "en"
        assert Path(args[0]) == Path("/tmp/a.wav")

    def test_default_keeps_zh_small(self):
        from douyin_batch import transcribe as tmod

        with patch("mediascribe.Pipeline") as pipe_cls, patch("mediascribe.Settings") as set_cls:
            pool = tmod.TranscriberPool()
            set_cls.assert_called_once_with(model="small")
            pool.transcribe(Path("/tmp/a.wav"))
            _, kwargs = pipe_cls.return_value.transcribe.call_args
        assert kwargs.get("language") == "zh"

    def test_transcribe_audio_passes_config(self):
        from douyin_batch import transcribe as tmod

        with patch.object(tmod, "TranscriberPool") as pool_cls:
            tmod.transcribe_audio(Path("/tmp/x.wav"), config="CFG")
        pool_cls.assert_called_once_with(config="CFG")


# ---------------------------------------------------------------------------
# P2-1: OCR 进度计数 + 顺序回填
# ---------------------------------------------------------------------------


class TestOcrImagesOrder:
    def test_results_follow_input_order_despite_out_of_order_completion(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        d = WechatMpDownloader()

        def fake_ocr(url, save_dir):
            if url == "slow":
                time.sleep(0.2)
                return "slow-text"
            time.sleep(0.01)
            return f"text-{url}"

        with patch.object(d, "_ocr_image", side_effect=fake_ocr):
            with patch("mediascribe.downloaders.wechat_mp.logger") as mock_log:
                texts, success, total = d._ocr_images(
                    ["slow", "fast1", "fast2"], Path(".")
                )
        assert texts == ["slow-text", "text-fast1", "text-fast2"]
        assert (success, total) == (3, 3)
        # 进度日志为独立 done 计数：依次 1/3、2/3、3/3（原公式恒为 1/3）
        progress_args = [c.args for c in mock_log.info.call_args_list]
        assert ("OCR 进度: %d/%d (并发=%d)", 1, 3, 3) in [
            (a[0], a[1], a[2], a[3]) for a in progress_args
        ]

    def test_partial_failure_keeps_order(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        d = WechatMpDownloader()
        with patch.object(d, "_ocr_image", side_effect=["first", None, "third"]):
            texts, success, total = d._ocr_images(["a", "b", "c"], Path("."))
        assert texts == ["first", "third"]
        assert (success, total) == (2, 3)


# ---------------------------------------------------------------------------
# P2-6: merge_cli_args 只覆盖显式参数
# ---------------------------------------------------------------------------


class TestMergeCliArgs:
    def test_none_args_do_not_override_config(self):
        from douyin_batch.config import BatchConfig

        cfg = BatchConfig(
            max_videos=5, headless=False, keep_audio=True,
            output_dir="orig", log_level="DEBUG", max_retries=9,
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
        from douyin_batch.config import BatchConfig

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


class TestProcessSingleVideoSafety:
    def _run(self, tmp_path, media_url, download_fn):
        import douyin_batch_v3 as v3

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
        import douyin_batch_v3 as v3

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


class TestUrlUtilsHostMatching:
    def test_userinfo_bypass_blocked(self):
        from mediascribe.url_utils import is_short_url, resolve_short_url

        # 实际主机是 127.0.0.1，不算短链：resolve 直接透传（不发起网络请求）
        assert is_short_url("http://b23.tv@127.0.0.1/") is False
        assert resolve_short_url("http://b23.tv@127.0.0.1/") == "http://b23.tv@127.0.0.1/"

    def test_path_substring_not_short(self):
        from mediascribe.url_utils import is_short_url, normalize_url

        assert is_short_url("https://www.bilibili.com/b23.tv") is False
        assert normalize_url("https://www.bilibili.com/b23.tv") == "https://www.bilibili.com/b23.tv"

    def test_real_short_domains_still_match(self):
        from mediascribe.url_utils import is_short_url

        assert is_short_url("https://b23.tv/abc") is True
        assert is_short_url("https://www.b23.tv/abc") is True  # 子域仍算
        assert is_short_url("https://v.douyin.com/xyz/") is True


# ---------------------------------------------------------------------------
# P2-13: get_user_videos 滚动参数注入
# ---------------------------------------------------------------------------


class TestGetUserVideosScrollParams:
    def test_max_scroll_rounds_caps_scrolling(self):
        from douyin_batch import browser as br_mod

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
            with patch("douyin_batch.browser.time.sleep"):
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


class TestReportAsciiFilenames:
    def test_report_files_are_ascii(self, tmp_path):
        from douyin_batch.report import generate_summary_report

        out = generate_summary_report("https://www.douyin.com/user/U", [], tmp_path)
        assert out.name.startswith("summary_") and out.suffix == ".md"
        assert out.name.isascii()
        jsons = list(tmp_path.glob("results_*.json"))
        assert len(jsons) == 1
        assert jsons[0].name.isascii()
