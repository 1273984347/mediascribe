"""回归测试：批量代码审查修复 (2026-11-03 自 test_review_fixes.py 759 行按主题拆分)。

原文件聚合 17 个审查批次的测试; 拆分后按主题落位, 行为零变化。
本文件覆盖: wechat_mp js_content 嵌套 div / url_utils 主机名精确匹配 / 汇总报告 ASCII 文件名
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


# ---------------------------------------------------------------------------
# P2-15: 汇总报告 ASCII 文件名
# ---------------------------------------------------------------------------


class TestReportAsciiFilenames:
    def test_report_files_are_ascii(self, tmp_path):
        from mediascribe.report import generate_summary_report

        out = generate_summary_report("https://www.douyin.com/user/U", [], tmp_path)
        assert out.name.startswith("summary_") and out.suffix == ".md"
        assert out.name.isascii()
        jsons = list(tmp_path.glob("results_*.json"))
        assert len(jsons) == 1
        assert jsons[0].name.isascii()
