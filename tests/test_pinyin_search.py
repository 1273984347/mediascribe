"""mediascribe.pinyin_search — 拼音归一化搜索键(全拼/首字母/模糊音)测试。

含两层: 键构造与查询归一化的单元测试, 以及 WikiVault.search 的
拼音通道集成测试(含 pypinyin 缺失降级)。
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mediascribe import pinyin_search
from mediascribe.wiki import WikiVault

FAKE_METADATA = {
    "source": {
        "raw_input": "https://www.bilibili.com/video/BV1xx",
        "kind": "bilibili",
        "url": "https://www.bilibili.com/video/BV1xx",
    },
    "engine": "faster-whisper",
    "model": "large-v3",
    "language": "zh",
    "generated_at": "2026-11-03T00:00:00",
    "download_metadata": {
        "title": "机械键盘",
        "uploader": "某作者",
        "duration": 60,
        "webpage_url": "https://www.bilibili.com/video/BV1xx",
    },
}


class TestFoldFuzzy(unittest.TestCase):
    """模糊音折叠: 折叠顺序必须保持(卷舌先于 h->f)。"""

    def test_retroflex_initials(self):
        self.assertEqual(pinyin_search.fold_fuzzy("zhang"), "zan")
        self.assertEqual(pinyin_search.fold_fuzzy("chang"), "can")
        self.assertEqual(pinyin_search.fold_fuzzy("shi"), "si")

    def test_nasal_finals(self):
        self.assertEqual(pinyin_search.fold_fuzzy("ang"), "an")
        self.assertEqual(pinyin_search.fold_fuzzy("eng"), "en")
        self.assertEqual(pinyin_search.fold_fuzzy("ling"), "nin")

    def test_h_folds_only_after_retroflex(self):
        # sh 里的 h 已被 sh->s 消化, 剩下的 h 才折成 f
        self.assertEqual(pinyin_search.fold_fuzzy("shang"), "san")
        self.assertEqual(pinyin_search.fold_fuzzy("hai"), "fai")

    def test_n_l_class(self):
        self.assertEqual(pinyin_search.fold_fuzzy("li"), "ni")
        self.assertEqual(pinyin_search.fold_fuzzy("nan"), "nan")


class TestKeys(unittest.TestCase):
    def test_full_key(self):
        self.assertEqual(pinyin_search.pinyin_key("徐涛"), "xutao")

    def test_full_key_fuzzy_folded(self):
        self.assertEqual(pinyin_search.pinyin_key("战士"), "zansi")

    def test_full_key_mixed_latin(self):
        # CJK 音节在前, 拉丁在后; 折叠对拉丁同样生效(查询侧折叠方式一致)
        self.assertEqual(pinyin_search.pinyin_key("Phase 4 概念"), "gainianpfase4")

    def test_full_key_without_cjk_is_none(self):
        self.assertIsNone(pinyin_search.pinyin_key("keyboard 01"))

    def test_initials_key(self):
        self.assertEqual(pinyin_search.initials_key("徐涛"), "xt")
        # 机(ji) 械(xie) 键(jian) 盘(pan) -> jxjp
        self.assertEqual(pinyin_search.initials_key("机械键盘"), "jxjp")

    def test_initials_key_without_cjk_is_none(self):
        self.assertIsNone(pinyin_search.initials_key("notes"))


class TestNormalizeQuery(unittest.TestCase):
    def test_pure_ascii_letters_fold(self):
        self.assertEqual(pinyin_search.normalize_query("XuTao"), "xutao")
        self.assertEqual(pinyin_search.normalize_query("  zhanshi "), "zansi")

    def test_rejected_queries(self):
        self.assertIsNone(pinyin_search.normalize_query("徐涛"))  # 非 ASCII
        self.assertIsNone(pinyin_search.normalize_query("x1"))  # 含数字
        self.assertIsNone(pinyin_search.normalize_query("x"))  # 单字母
        self.assertIsNone(pinyin_search.normalize_query(""))
        self.assertIsNone(pinyin_search.normalize_query(None))  # type: ignore[arg-type]


class TestMatchTitle(unittest.TestCase):
    def test_full_pinyin_substring(self):
        self.assertTrue(pinyin_search.match_title("徐涛考研政治基础班 01", "xutao"))
        self.assertFalse(pinyin_search.match_title("徐涛考研政治基础班 01", "yutao"))

    def test_initials_substring(self):
        self.assertTrue(pinyin_search.match_title("【实测】机械键盘", "jxjp"))
        self.assertFalse(pinyin_search.match_title("【实测】机械键盘", "xyzw"))


class TestVaultPinyinSearch(unittest.TestCase):
    """WikiVault.search 拼音通道: 全拼 / 首字母 / 模糊音 / 降级。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = WikiVault(Path(self._tmp.name) / "vault")

    def _archive(self, title: str, markdown: str, url: str) -> None:
        src_md = Path(self._tmp.name) / "in.md"
        src_md.write_text(markdown, encoding="utf-8")
        meta = {
            **FAKE_METADATA,
            "download_metadata": {**FAKE_METADATA["download_metadata"], "title": title},
            "source": {**FAKE_METADATA["source"], "url": url, "raw_input": url},
        }
        self.vault.archive_transcript(src_md, meta)

    def test_full_pinyin_finds_note(self):
        self._archive(
            "机械键盘:避坑指南",
            "# 机械键盘:避坑指南\n\n## 转录内容\n\n**[00:00]** 大家好。\n",
            "https://www.bilibili.com/video/BV1xx",
        )
        results = self.vault.search("jixiejianpan")
        self.assertEqual(len(results), 1)
        hit = results[0]
        self.assertEqual(hit["type"], "raw")
        self.assertTrue(hit["path"].startswith("raw/"))
        self.assertIn("大家好", hit["snippet"])

    def test_initials_finds_note(self):
        self._archive(
            "机械键盘:避坑指南",
            "# 机械键盘:避坑指南\n\n**[00:00]** 大家好。\n",
            "https://www.bilibili.com/video/BV1xx",
        )
        self.assertEqual(len(self.vault.search("jxjp")), 1)

    def test_fuzzy_pinyin_finds_note_both_sides(self):
        self._archive(
            "战士的觉悟",
            "# 战士的觉悟\n\n**[00:00]** 内容。\n",
            "https://www.bilibili.com/video/BV2yy",
        )
        # 查询侧折叠("zhanshi"->"zansi")与标题侧折叠殊途同归
        self.assertEqual(len(self.vault.search("zansi")), 1)
        self.assertEqual(len(self.vault.search("zhanshi")), 1)

    def test_pinyin_no_match(self):
        self._archive("机械键盘", "# 机械键盘\n\n正文。\n", "https://www.bilibili.com/video/BV1xx")
        self.assertEqual(self.vault.search("zzzz"), [])

    def test_substring_hit_wins_over_pinyin(self):
        # 中文查询不走拼音通道, 仍按原文子串命中并给出命中点片段
        self._archive(
            "机械键盘",
            "# 机械键盘\n\n**[00:10]** 轴体切换很顺滑。\n",
            "https://www.bilibili.com/video/BV1xx",
        )
        results = self.vault.search("轴体")
        self.assertEqual(len(results), 1)
        self.assertIn("轴体", results[0]["snippet"])

    def test_degrades_without_pypinyin(self):
        self._archive(
            "机械键盘",
            "# 机械键盘\n\n**[00:10]** 轴体切换很顺滑。\n",
            "https://www.bilibili.com/video/BV1xx",
        )
        with patch.dict(sys.modules, {"pypinyin": None}):
            self.assertEqual(self.vault.search("jixiejianpan"), [])
            # 子串通道不受降级影响
            self.assertEqual(len(self.vault.search("轴体")), 1)


if __name__ == "__main__":
    unittest.main()
