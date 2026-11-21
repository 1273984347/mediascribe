"""P2-1(2026-11-20 架构评审)— wechat_mp 解析层**金样本合约测试**。

背景: ``wechat_mp.py`` 的正文/meta/图片/视频提取全部建立在手写正则上
(``<div>`` 标签配平器、JS 全局变量名、属性顺序敏感的 meta 匹配) ——
微信一次改版(id 改名 / 引号风格 / 模板变量)整平台**静默劣化**
(截断响应会把半页 HTML 当正文)。

合约的意义: 用真实结构的金样本把**当前行为**钉死,任何回归(改版适配、
重构、正则调整)都必须显式面对这些断言 —— fail loud,而不是悄悄丢正文。

金样本约定:
* ``article_text.html``    — 完整图文文章(嵌套 div / script/style 内嵌 /
  HTML 实体 / 四类图片形态 / 重复图片);
* ``article_video.html``   — 视频消息(JS 全局变量 + source 标签);
* ``article_video_offsite.html`` — 视频地址不在腾讯系 CDN(必须被忽略);
* ``article_truncated.html`` — 响应被截断(js_content 未闭合)。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures" / "wechat_mp"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _downloader():
    from mediascribe.downloaders.wechat_mp import WechatMpDownloader

    return WechatMpDownloader()


class TestMetaContract(unittest.TestCase):
    """meta 提取: og:title / author / js_name 昵称。"""

    def setUp(self):
        self.dl = _downloader()
        self.html = _load("article_text.html")

    def test_title_with_entity_unescaped(self):
        meta = self.dl._extract_meta(self.html)
        self.assertEqual(meta.get("title"), "深度长文：如何读<资治通鉴>")

    def test_author(self):
        meta = self.dl._extract_meta(self.html)
        self.assertEqual(meta.get("author"), "叁月聚粮")

    def test_nickname(self):
        meta = self.dl._extract_meta(self.html)
        self.assertEqual(meta.get("nickname"), "叁月聚粮读书")


class TestDivBlockContract(unittest.TestCase):
    """``<div>`` 配平器: 嵌套 div 不截断;未闭合返回剩余内容。"""

    def setUp(self):
        self.dl = _downloader()
        self.html = _load("article_text.html")

    def test_nested_divs_kept(self):
        """核心合约 —— js_content 内部嵌套 div,整块必须完整取出。"""
        block = self.dl._extract_div_block(self.html, 'id="js_content"')
        assert block is not None
        self.assertIn("第二段在嵌套 div 里", block)
        self.assertIn("第四段在 section 里", block)
        # 块必须止于 js_content 的闭合标签,不得吞掉页脚
        self.assertNotIn("页脚不属于正文", block)

    def test_anchor_missing_returns_none(self):
        self.assertIsNone(self.dl._extract_div_block(self.html, 'id="no_such"'))

    def test_single_quoted_anchor_also_matches(self):
        """引号风格变体(id='js_content')必须同样命中(微信模板变过)。"""
        html = self.html.replace('id="js_content"', "id='js_content'")
        block = self.dl._extract_div_block(html, 'id="js_content"')
        assert block is not None
        self.assertIn("第一段", block)

    def test_truncated_response_returns_rest(self):
        """截断响应合约(:373-374): 未闭合 → 返回剩余全部,尽量不丢数据。"""
        truncated = _load("article_truncated.html")
        block = self.dl._extract_div_block(truncated, 'id="js_content"')
        assert block is not None
        self.assertIn("开头段落完整保留", block)
        self.assertIn("后半段被网络截断", block)


class TestTextContract(unittest.TestCase):
    """正文提取: 段落聚合 / script·style 剔除 / 实体反转义。"""

    def setUp(self):
        self.dl = _downloader()
        self.text = self.dl._extract_text(_load("article_text.html"))

    def test_paragraphs_in_order(self):
        assert self.text is not None
        lines = self.text.split("\n\n")
        self.assertIn("第一段：读书如&攻城，需“耐心”。", lines)
        self.assertIn("第二段在嵌套 div 里，旧的非贪婪实现会在这里截断。", lines)
        self.assertIn("第四段在 section 里", lines)

    def test_br_splits_paragraph(self):
        assert self.text is not None
        self.assertIn("第三段", self.text.split("\n\n"))
        self.assertIn("含换行", self.text.split("\n\n"))

    def test_script_and_style_stripped(self):
        assert self.text is not None
        self.assertNotIn("should be stripped", self.text)
        self.assertNotIn("color: red", self.text)
        self.assertNotIn("页脚不属于正文", self.text)

    def test_no_html_tags_leak(self):
        assert self.text is not None
        self.assertNotIn("<", self.text)


class TestImageContract(unittest.TestCase):
    """图片提取: 四种形态 / 协议相对升级 / data:image 与重复剔除。"""

    def test_images_extracted_normalized_deduped(self):
        d = _downloader()
        urls = d._extract_image_urls(
            d._extract_div_block(_load("article_text.html"), 'id="js_content"') or ""
        )
        self.assertEqual(
            urls,
            [
                "https://mmbiz.qpic.cn/img-a.jpg",
                "https://mmbiz.qpic.cn/img-b.jpg",
                "https://mmbiz.qpic.cn/img-c.jpg",
            ],
        )


class TestVideoContract(unittest.TestCase):
    """视频消息: JS 全局变量优先;非腾讯 CDN 地址必须被忽略。"""

    def test_video_iframe_var_first_match(self):
        d = _downloader()
        url = d._extract_video_url(_load("article_video.html"))
        self.assertEqual(url, "https://mpvideo.qpic.cn/vid-a.mp4?dis_k=abc&dis_t=1")

    def test_offsite_video_ignored(self):
        """P0-2 安全合约: 内容驱动的视频地址只信腾讯系 CDN。"""
        d = _downloader()
        self.assertIsNone(d._extract_video_url(_load("article_video_offsite.html")))


class TestRiskControlContract(unittest.TestCase):
    """P1-5 合约: 风控页(errcode 1023/1024)必须 fail loud 而非当正文。"""

    def test_errcode_page_raises_bot_wall(self):
        from mediascribe.downloaders.download_errors import (
            CATEGORY_BOT_WALL,
            DownloadError,
        )
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        html = _load("article_text.html").replace('"errcode": 0', '"errcode": 1023')
        with self.assertRaises(DownloadError) as ctx:
            WechatMpDownloader._assert_not_risk_limited(html)
        self.assertEqual(ctx.exception.category, CATEGORY_BOT_WALL)

    def test_normal_article_passes(self):
        from mediascribe.downloaders.wechat_mp import WechatMpDownloader

        WechatMpDownloader._assert_not_risk_limited(_load("article_text.html"))


if __name__ == "__main__":
    unittest.main()
