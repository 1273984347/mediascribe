"""mediascribe.wiki — Karpathy 式知识库 vault 层(Phase 1)测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mediascribe.wiki import (
    WikiVault,
    sanitize_note_name,
    vault_for_workspace,
    wiki_enabled,
)

FAKE_METADATA = {
    "source": {
        "raw_input": "https://www.bilibili.com/video/BV1xx",
        "kind": "bilibili",
        "url": "https://www.bilibili.com/video/BV1xx",
    },
    "engine": "faster-whisper",
    "model": "large-v3",
    "language": "zh",
    "generated_at": "2026-09-11T23:41:07",
    "download_metadata": {
        "title": "【实测】机械键盘:避坑指南",
        "uploader": "影视飓风",
        "duration": 504,
        "webpage_url": "https://www.bilibili.com/video/BV1xx",
    },
}

FAKE_MARKDOWN = "# 【实测】机械键盘:避坑指南\n\n## 转录内容\n\n**[00:00]** 大家好。\n"


class TestWikiVault(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = WikiVault(Path(self._tmp.name) / "vault")

    def _archive(
        self, metadata=None, markdown=FAKE_MARKDOWN, url="https://www.bilibili.com/video/BV1xx"
    ):
        src_md = Path(self._tmp.name) / "out.md"
        src_md.write_text(markdown, encoding="utf-8")
        meta = dict(FAKE_METADATA)
        if metadata is not None:
            meta.update(metadata)
        meta["download_metadata"] = {
            **meta.get("download_metadata", {}),
            "webpage_url": url,
        }
        return self.vault.archive_transcript(src_md, meta)

    def test_archive_creates_raw_note_with_frontmatter_and_attribution(self):
        info = self._archive()
        raw = self.vault.raw_dir / Path(info["raw"]).name
        self.assertTrue(raw.exists())
        text = raw.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"))
        for needle in (
            'title: "【实测】机械键盘 避坑指南"',
            'url: "https://www.bilibili.com/video/BV1xx"',
            'platform: "哔哩哔哩"',
            'author: "影视飓风"',
            'duration: "8分24秒"',
            'engine: "faster-whisper"',
            'model: "large-v3"',
            'language: "zh"',
            "created: 2026-09-11 23:41:07",
            "> [[作者 - 影视飓风]] · [[平台 - 哔哩哔哩]] · [原始链接](https://www.bilibili.com/video/BV1xx)",
            "**[00:00]** 大家好。",
        ):
            self.assertIn(needle, text)

    def test_author_and_platform_notes_generated(self):
        self._archive()
        author_note = self.vault.wiki_dir / "作者 - 影视飓风.md"
        platform_note = self.vault.wiki_dir / "平台 - 哔哩哔哩.md"
        self.assertTrue(author_note.exists())
        self.assertTrue(platform_note.exists())
        author_text = author_note.read_text(encoding="utf-8")
        self.assertIn("type: 作者", author_text)
        self.assertIn("共 1 篇转写", author_text)
        # 笔记名含日期前缀 + 标题
        self.assertIn("[[2026-09-11 【实测】机械键盘 避坑指南]]", author_text)

    def test_home_index_lists_recent_and_counts(self):
        self._archive()
        home = self.vault.root / "HOME.md"
        self.assertTrue(home.exists())
        text = home.read_text(encoding="utf-8")
        self.assertIn("type: home", text)
        self.assertIn("共 1 篇转写 · 1 位作者 · 1 个平台", text)
        self.assertIn("## 最近转写", text)
        self.assertIn("[[2026-09-11 【实测】机械键盘 避坑指南]]", text)
        self.assertIn("[[作者 - 影视飓风]] (1)", text)
        self.assertIn("[[平台 - 哔哩哔哩]] (1)", text)

    def test_idempotent_same_url_single_entry(self):
        self._archive()
        self._archive()  # 同 URL 再次归档
        raws = list(self.vault.raw_dir.glob("*.md"))
        self.assertEqual(len(raws), 1)
        home = (self.vault.root / "HOME.md").read_text(encoding="utf-8")
        self.assertIn("共 1 篇转写", home)

    def test_different_urls_same_title_no_collision(self):
        self._archive(url="https://a.com/1")
        self._archive(url="https://a.com/2")
        raws = list(self.vault.raw_dir.glob("*.md"))
        self.assertEqual(len(raws), 2)
        names = {p.name for p in raws}
        self.assertIn("2026-09-11 【实测】机械键盘 避坑指南.md", names)
        self.assertIn("2026-09-11 【实测】机械键盘 避坑指南 -2.md", names)

    def test_filename_sanitized(self):
        meta = {
            **FAKE_METADATA,
            "download_metadata": {
                **FAKE_METADATA["download_metadata"],
                "title": 'a/b:c*d?"e<>f|g',
            },
        }
        info = self._archive(metadata=meta)
        name = Path(info["raw"]).name
        for ch in '\\/:*?"<>|':
            self.assertNotIn(ch, name)
        self.assertTrue((self.vault.raw_dir / name).exists())

    def test_title_falls_back_to_md_heading(self):
        meta = {
            **FAKE_METADATA,
            "download_metadata": {"uploader": "某作者"},
        }
        info = self._archive(metadata=meta)
        self.assertIn("机械键盘", info["title"])
        self.assertTrue((self.vault.wiki_dir / "平台 - 哔哩哔哩.md").exists())
        self.assertTrue((self.vault.wiki_dir / "作者 - 某作者.md").exists())
        raw = self.vault.raw_dir / Path(info["raw"]).name
        self.assertIn('title: "【实测】机械键盘 避坑指南"', raw.read_text(encoding="utf-8"))

    def test_author_note_aggregates_multiple_videos(self):
        self._archive(url="https://a.com/1")
        meta2 = {
            **FAKE_METADATA,
            "download_metadata": {
                **FAKE_METADATA["download_metadata"],
                "title": "第二期:键盘养护",
            },
        }
        self._archive(metadata=meta2, url="https://a.com/2")
        note = (self.vault.wiki_dir / "作者 - 影视飓风.md").read_text(encoding="utf-8")
        self.assertIn("共 2 篇转写", note)
        self.assertIn("[[2026-09-11 第二期 键盘养护]]", note)


class TestWikiToggle(unittest.TestCase):
    def test_enabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.pop("MEDIASCRIBE_WIKI", None)
            try:
                self.assertTrue(wiki_enabled())
                self.assertIsNotNone(vault_for_workspace(tmp))
            finally:
                if old is not None:
                    os.environ["MEDIASCRIBE_WIKI"] = old

    def test_disabled_by_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.pop("MEDIASCRIBE_WIKI", None)
            os.environ["MEDIASCRIBE_WIKI"] = "0"
            try:
                self.assertFalse(wiki_enabled())
                self.assertIsNone(vault_for_workspace(tmp))
            finally:
                if old is not None:
                    os.environ["MEDIASCRIBE_WIKI"] = old
                else:
                    os.environ.pop("MEDIASCRIBE_WIKI", None)

    def test_sanitize_note_name(self):
        self.assertEqual(sanitize_note_name('a/b:c*d?"e<>f|'), "a b c d e f")
        self.assertEqual(sanitize_note_name(""), "未命名")
        self.assertEqual(sanitize_note_name("  "), "未命名")
        self.assertEqual(sanitize_note_name("正常 标题"), "正常 标题")


if __name__ == "__main__":
    unittest.main()
