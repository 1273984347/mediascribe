"""知识库 Phase 2/3 测试 — LLM 概念提取、概念笔记、Web API 视图。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mediascribe" / "web"))

from mediascribe.wiki import ConceptExtractor, WikiVault  # noqa: E402

FAKE_METADATA = {
    "source": {"raw_input": "u", "kind": "bilibili", "url": "https://www.bilibili.com/video/BV1"},
    "engine": "faster-whisper",
    "model": "large-v3",
    "language": "zh",
    "generated_at": "2026-09-11T23:41:07",
    "download_metadata": {
        "title": "键盘实测",
        "uploader": "影视飓风",
        "webpage_url": "https://www.bilibili.com/video/BV1",
    },
}
FAKE_MD = (
    "# 键盘实测\n\n## 转录内容\n\n"
    "**[00:12]** 我们实测了卫星轴的手感。\n"
    "**[03:12]** 卫星轴在大键上相当稳定。\n"
)

GOOD_JSON = (
    '[{"name": "卫星轴", "mentions": ['
    '{"ts": "00:12", "quote": "我们实测了卫星轴的手感"}, '
    '{"ts": "03:12", "quote": "卫星轴在大键上相当稳定"}]}]'
)


def _fake_extractor(payload=GOOD_JSON):
    return ConceptExtractor(call_fn=lambda prompt: payload)


class TestConceptExtractor(unittest.TestCase):
    def test_from_env_none_without_key(self):
        old = os.environ.pop("MEDIASCRIBE_LLM_API_KEY", None)
        old_enabled = os.environ.pop("MEDIASCRIBE_LLM_ENABLED", None)
        try:
            self.assertIsNone(ConceptExtractor.from_env())
        finally:
            if old is not None:
                os.environ["MEDIASCRIBE_LLM_API_KEY"] = old
            if old_enabled is not None:
                os.environ["MEDIASCRIBE_LLM_ENABLED"] = old_enabled

    def test_extract_parses_json(self):
        out = _fake_extractor().extract("标题", FAKE_MD)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "卫星轴")
        self.assertEqual(len(out[0]["mentions"]), 2)
        self.assertEqual(out[0]["mentions"][1]["ts"], "03:12")

    def test_extract_strips_code_fences(self):
        payload = "```json\n" + GOOD_JSON + "\n```"
        out = _fake_extractor(payload).extract("标题", FAKE_MD)
        self.assertEqual(out[0]["name"], "卫星轴")

    def test_extract_malformed_returns_empty(self):
        self.assertEqual(_fake_extractor("不是 JSON").extract("t", FAKE_MD), [])
        self.assertEqual(_fake_extractor('{"name": 1}').extract("t", FAKE_MD), [])

    def test_extract_caps_concepts_and_drops_empty_names(self):
        payload = (
            '[{"name": ""}, {"name": "甲"}, {"name": "乙"}, {"name": "丙"}, '
            '{"name": "丁"}, {"name": "戊"}, {"name": "己"}, {"name": "庚"}, '
            '{"name": "辛"}, {"name": "壬"}]'
        )
        out = _fake_extractor(payload).extract("t", FAKE_MD)
        self.assertLessEqual(len(out), ConceptExtractor.MAX_CONCEPTS)


class TestConceptNotes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = WikiVault(Path(self._tmp.name) / "vault")

    def _write_md(self, name: str, content: str) -> Path:
        p = Path(self._tmp.name) / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_archive_with_concepts_creates_note_and_links(self):
        info = self.vault.archive_transcript(
            self._write_md("a.md", FAKE_MD), FAKE_METADATA, extractor=_fake_extractor()
        )
        raw = self.vault.raw_dir / Path(info["raw"]).name
        raw_text = raw.read_text(encoding="utf-8")
        # front-matter concepts 列表 + 归属区概念互链
        self.assertIn('concepts: ["卫星轴"]', raw_text)
        self.assertIn("> 概念:[[概念 - 卫星轴]]", raw_text)
        # 概念笔记聚合时间戳引用
        note = (self.vault.wiki_dir / "概念 - 卫星轴.md").read_text(encoding="utf-8")
        self.assertIn("type: 概念", note)
        self.assertIn("count: 2", note)
        self.assertIn("**[03:12]** 卫星轴在大键上相当稳定", note)
        self.assertIn(f"## [[{Path(info['raw']).stem}]]", note)
        # HOME 出现概念分组
        home = (self.vault.root / "HOME.md").read_text(encoding="utf-8")
        self.assertIn("## 概念", home)
        self.assertIn("[[概念 - 卫星轴]]", home)

    def test_concept_aggregates_across_videos(self):
        self.vault.archive_transcript(
            self._write_md("a.md", FAKE_MD), FAKE_METADATA, extractor=_fake_extractor()
        )
        meta2 = {
            **FAKE_METADATA,
            "download_metadata": {"title": "第二期", "uploader": "影视飓风"},
        }
        self.vault.archive_transcript(
            self._write_md("b.md", "# 第二期\n\n**[01:00]** 又聊了卫星轴。\n"),
            meta2,
            extractor=_fake_extractor(),
        )
        note = (self.vault.wiki_dir / "概念 - 卫星轴.md").read_text(encoding="utf-8")
        self.assertIn("在 2 篇转写中出现", note)
        self.assertIn("[[2026-09-11 键盘实测]]", note)
        self.assertIn("[[2026-09-11 第二期]]", note)

    def test_no_extractor_degrades_gracefully(self):
        info = self.vault.archive_transcript(self._write_md("a.md", FAKE_MD), FAKE_METADATA)
        self.assertEqual(info["concepts"], [])
        self.assertFalse((self.vault.wiki_dir / "概念 - 卫星轴.md").exists())
        raw = (self.vault.raw_dir / Path(info["raw"]).name).read_text(encoding="utf-8")
        self.assertNotIn("概念:", raw)


class TestWikiWebApi(unittest.TestCase):
    """Phase 3 — /api/wiki* 端点 + /wiki 页面。"""

    def setUp(self):
        try:
            from fastapi.testclient import TestClient

            import app as app_module
        except Exception:
            self.skipTest("fastapi not installed")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        old = os.environ.pop("MEDIASCRIBE_API_TOKEN", None)
        self.addCleanup(lambda: os.environ.pop("MEDIASCRIBE_API_TOKEN", None))
        if old is not None:
            os.environ["MEDIASCRIBE_API_TOKEN"] = old
        self.app_module = app_module
        self.client = TestClient(app_module.create_app(workspace=Path(self._tmp.name) / "ws"))
        # 归档一篇带概念的转写
        vault = self.client.app.state.wiki_vault
        assert vault is not None
        md = Path(self._tmp.name) / "a.md"
        md.write_text(FAKE_MD, encoding="utf-8")
        vault.archive_transcript(md, FAKE_METADATA, extractor=_fake_extractor())

    def test_wiki_page_served(self):
        r = self.client.get("/wiki")
        self.assertEqual(r.status_code, 200)
        self.assertIn("知识库", r.text)

    def test_wiki_index_lists_notes(self):
        r = self.client.get("/api/wiki")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["enabled"])
        self.assertGreaterEqual(data["raw_count"], 1)
        types = {n["type"] for n in data["notes"]}
        self.assertIn("raw", types)
        self.assertIn("wiki", types)
        self.assertIn("概念 - 卫星轴.md", " ".join(n["path"] for n in data["notes"]))

    def test_wiki_note_returns_markdown(self):
        r = self.client.get("/api/wiki/note", params={"name": "raw/2026-09-11 键盘实测.md"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("**[00:12]**", r.text)

    def test_wiki_note_rejects_traversal(self):
        r = self.client.get("/api/wiki/note", params={"name": "../secret.md"})
        self.assertIn(r.status_code, (400, 404))
        r = self.client.get("/api/wiki/note", params={"name": "..\\secret.md"})
        self.assertIn(r.status_code, (400, 404))

    def test_wiki_note_missing_404(self):
        r = self.client.get("/api/wiki/note", params={"name": "raw/不存在.md"})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
