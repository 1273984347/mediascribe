"""回归测试：批量代码审查修复 (2026-11-03 自 test_review_fixes.py 759 行按主题拆分)。

原文件聚合 17 个审查批次的测试; 拆分后按主题落位, 行为零变化。
本文件覆盖: douyin 无 playwright 降级 / BrowserManager / 小红书图文报错 / stream_download / _ocr_images 计数
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
        with patch.object(d, "_extract_media_url_with_browser", return_value=(None, None)):
            with pytest.raises(RuntimeError, match="媒体 URL|media URL"):
                d.download(
                    SourceRef(raw_input="x", kind="douyin", url="https://www.douyin.com/video/1"),
                    Settings(workspace_root=tmp_path),
                )


# ---------------------------------------------------------------------------
# P0-3: --config 纯配置文件启动（user_url）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P1-4: close_if_running
# ---------------------------------------------------------------------------


class TestBrowserCloseIfRunning:
    def setup_method(self):
        from mediascribe import browser as br_mod

        br_mod.BrowserManager._instance = None

    def teardown_method(self):
        from mediascribe import browser as br_mod

        br_mod.BrowserManager._instance = None

    def test_no_instance_does_not_start_browser(self):
        from mediascribe import browser as br_mod

        # 未 patch playwright：如果实现错误地启动了浏览器，此测试会失败
        br_mod.BrowserManager.close_if_running()
        assert br_mod.BrowserManager._instance is None

    def test_existing_instance_is_closed(self):
        from mediascribe import browser as br_mod

        inst = MagicMock()
        br_mod.BrowserManager._instance = inst
        br_mod.BrowserManager.close_if_running()
        inst.close.assert_called_once()
        # close() 会把 _instance 复位为 None（这里手动模拟）
        br_mod.BrowserManager._instance = None

    def test_headless_mismatch_keeps_existing_instance(self):
        from mediascribe import browser as br_mod

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


# ---------------------------------------------------------------------------
# P1-5: 小红书图文笔记明确报错
# ---------------------------------------------------------------------------


class TestXiaohongshuImageNote:
    def test_image_note_raises_actionable_error(self, tmp_path):
        from mediascribe.config import Settings
        from mediascribe.downloaders.xiaohongshu import XiaohongshuDownloader
        from mediascribe.i18n import set_language
        from mediascribe.models import SourceRef

        set_language("zh")  # 断言中文文案, 先钉住 locale
        d = XiaohongshuDownloader()
        try:
            with patch.object(
                d,
                "_extract_media_and_meta",
                return_value=("https://sns-img-bd.xhscdn.com/cover.jpg", "标题", "image"),
            ):
                with pytest.raises(RuntimeError, match="图文笔记"):
                    d.download(
                        SourceRef(
                            raw_input="x",
                            kind="xiaohongshu",
                            url="https://www.xiaohongshu.com/explore/1",
                        ),
                        Settings(workspace_root=tmp_path),
                    )
        finally:
            set_language("en")


# ---------------------------------------------------------------------------
# P1-6: stream_download
# ---------------------------------------------------------------------------


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
        with pytest.raises(Exception):  # noqa: B017 — 具体异常类型随网络栈变化
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
                texts, success, total = d._ocr_images(["slow", "fast1", "fast2"], Path("."))
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


class TestStreamDownloadSizeLimit:
    """max_bytes 上限(2026-10-29 审查 P2):恶意/损坏源不得灌满磁盘。"""

    def test_oversize_aborts_and_cleans_part(self, tmp_path):
        import pytest

        from mediascribe.downloaders._http_download import stream_download

        dest = tmp_path / "out.mp4"
        dest.write_bytes(b"OLD-GOOD")
        big = _FakeSession([b"x" * 100, b"y" * 100, b"z" * 100])
        # locale 无关: en/zh 文案都含 "<limit> MiB"
        with pytest.raises(RuntimeError, match=r"1 MiB"):
            stream_download("https://x/v.mp4", dest, session=big, max_bytes=150)
        # .part 清理干净 + 旧文件未被破坏
        assert list(tmp_path.glob("*part*")) == []
        assert dest.read_bytes() == b"OLD-GOOD"

    def test_within_limit_passes(self, tmp_path):
        from mediascribe.downloaders._http_download import stream_download

        dest = tmp_path / "out.mp4"
        out = stream_download(
            "https://x/v.mp4",
            dest,
            session=_FakeSession([b"a" * 10, b"b" * 5]),
            max_bytes=1024,
        )
        assert out == dest
        assert dest.stat().st_size == 15

    def test_env_override_read(self, tmp_path, monkeypatch):
        from mediascribe.downloaders import _http_download as hd
        from mediascribe.downloaders._http_download import stream_download

        monkeypatch.setenv("MEDIASCRIBE_MAX_DOWNLOAD_MB", "1")
        dest = tmp_path / "out.mp4"
        import pytest

        with pytest.raises(RuntimeError, match=r"1 MiB"):
            stream_download(
                "https://x/v.mp4",
                dest,
                # > 1 MiB 才越限(env 以 MiB 为单位, 最小 1)
                session=_FakeSession([b"x" * (1024 * 1024), b"y"]),
            )
