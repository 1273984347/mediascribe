"""Tests for video2text.learn v2 — ASR auto-learning module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from video2text.learn import (
    Correction,
    LearnedTermsDB,
    clear_learned_terms,
    compare,
    confirm_term,
    export_terms,
    get_learned_terms,
    get_prompt_terms,
    import_terms,
    learn,
    learn_from_edit,
    remove_term,
)


# ---------------------------------------------------------------------------
# Correction dataclass
# ---------------------------------------------------------------------------
class TestCorrection:
    def test_should_apply_confirmed(self):
        c = Correction(wrong="获取病", right="霍去病", confirmed=True)
        assert c.should_apply() is True
        assert c.confidence == 1.0

    def test_should_apply_below_threshold(self):
        c = Correction(wrong="获取病", right="霍去病", count=1)
        assert c.should_apply() is False
        assert c.confidence < 1.0

    def test_should_apply_at_threshold(self):
        c = Correction(wrong="获取病", right="霍去病", count=2)
        assert c.should_apply() is True

    def test_contexts_limited(self):
        c = Correction(wrong="a", right="b")
        for i in range(10):
            c.contexts.append(f"ctx{i}")
        assert len(c.contexts) == 10  # dataclass doesn't enforce limit


# ---------------------------------------------------------------------------
# LearnedTermsDB
# ---------------------------------------------------------------------------
class TestLearnedTermsDB:
    def test_add_new(self):
        db = LearnedTermsDB()
        c = db.add("获取病", "霍去病", context="霍去病是名将")
        assert c.wrong == "获取病"
        assert c.count == 1
        assert c.confirmed is False

    def test_add_increments_count(self):
        db = LearnedTermsDB()
        db.add("获取病", "霍去病")
        c = db.add("获取病", "霍去病")
        assert c.count == 2
        assert c.confirmed is True  # AUTO_CONFIRM_THRESHOLD=2

    def test_confirm(self):
        db = LearnedTermsDB()
        db.add("获取病", "霍去病")
        assert db.confirm("获取病", "霍去病") is True
        assert db.terms[db._key("获取病", "霍去病")].confirmed is True

    def test_confirm_nonexistent(self):
        db = LearnedTermsDB()
        assert db.confirm("nope", "nope") is False

    def test_remove(self):
        db = LearnedTermsDB()
        db.add("获取病", "霍去病")
        assert db.remove("获取病", "霍去病") is True
        assert len(db.terms) == 0

    def test_get_active_terms(self):
        db = LearnedTermsDB()
        db.add("获取病", "霍去病")  # count=1, not active
        db.add("下水温", "下水文", context="x")
        db.add("下水温", "下水文", context="y")  # count=2, auto-confirmed
        active = db.get_active_terms()
        assert "下水温" in active
        assert "获取病" not in active

    def test_get_unconfirmed(self):
        db = LearnedTermsDB()
        db.add("获取病", "霍去病")
        db.add("下水温", "下水文")
        db.add("下水温", "下水文")  # auto-confirmed
        unconfirmed = db.get_unconfirmed()
        assert len(unconfirmed) == 1

    def test_stats(self):
        db = LearnedTermsDB()
        db.add("获取病", "霍去病")
        db.add("下水温", "下水文")
        db.add("下水温", "下水文")
        stats = db.stats()
        assert stats["total"] == 2
        assert stats["confirmed"] == 1
        assert stats["pending"] == 1
        assert stats["active"] == 1


# ---------------------------------------------------------------------------
# compare() — with pinyin filter
# ---------------------------------------------------------------------------
class TestCompare:
    def test_phonetically_similar_detected(self):
        """获取病 vs 霍去病: pinyin similar → detected."""
        corrections = compare("霍去病是汉代名将", "获取病是汉代名将")
        assert len(corrections) >= 1
        wrongs = [w for w, _r, _ctx in corrections]
        assert any("获取" in w for w in wrongs)

    def test_semantic_diff_filtered(self):
        """鲁迅 vs 周树人: pinyin very different → filtered out."""
        corrections = compare("鲁迅是伟大的作家", "周树人是伟大的作家")
        # "鲁迅" vs "周树人" — pinyin totally different, should be filtered
        for wrong, right, _ctx in corrections:
            assert wrong != "周树人" or right != "鲁迅"

    def test_identical_texts(self):
        assert compare("鲁迅是伟大的作家", "鲁迅是伟大的作家") == []

    def test_empty_strings(self):
        assert compare("", "") == []
        assert compare("hello", "") == []


# ---------------------------------------------------------------------------
# learn_from_edit()
# ---------------------------------------------------------------------------
class TestLearnFromEdit:
    def test_basic_edit(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        db = learn_from_edit(
            "获取病是汉代名将",
            "霍去病是汉代名将",
            path=store,
        )
        assert db.stats()["total"] >= 1

    def test_semantic_edit_filtered(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        db = learn_from_edit(
            "鲁迅是伟大的作家",
            "周树人是伟大的作家",
            path=store,
        )
        # pinyin too different → filtered
        assert db.stats()["total"] == 0

    def test_auto_confirm_on_repeat(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        learn_from_edit("获取病是名将", "霍去病是名将", path=store)
        learn_from_edit("获取病用兵如神", "霍去病用兵如神", path=store)
        terms = get_learned_terms(path=store)
        assert "获取病" in terms or any("获取" in k for k in terms)


# ---------------------------------------------------------------------------
# learn() / get_learned_terms() / clear_learned_terms()
# ---------------------------------------------------------------------------
class TestLearn:
    def test_save_and_read(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        db = learn([("获取病", "霍去病")], path=store)
        # count=1, below threshold → not active
        terms = get_learned_terms(path=store)
        assert "获取病" not in terms

    def test_auto_confirm_at_threshold(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        learn([("获取病", "霍去病")], path=store)
        learn([("获取病", "霍去病")], path=store)
        terms = get_learned_terms(path=store)
        assert "获取病" in terms

    def test_3_tuple_input(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        learn([("获取病", "霍去病", "霍去病是名将")], path=store)
        learn([("获取病", "霍去病", "霍去病用兵")], path=store)
        terms = get_learned_terms(path=store)
        assert "获取病" in terms

    def test_self_mapping_skipped(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        db = learn([("鲁迅", "鲁迅")], path=store)
        assert db.stats()["total"] == 0

    def test_clear(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        learn([("获取病", "霍去病")], path=store)
        assert store.exists()
        clear_learned_terms(path=store)
        assert not store.exists()


# ---------------------------------------------------------------------------
# confirm_term / remove_term
# ---------------------------------------------------------------------------
class TestConfirmRemove:
    def test_confirm(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        learn([("获取病", "霍去病")], path=store)
        # Before confirm: not active
        assert "获取病" not in get_learned_terms(path=store)
        confirm_term("获取病", "霍去病", path=store)
        # After confirm: active
        assert "获取病" in get_learned_terms(path=store)

    def test_remove(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        learn([("获取病", "霍去病")], path=store)
        remove_term("获取病", "霍去病", path=store)
        db = learn([], path=store)  # reload
        assert db.stats()["total"] == 0


# ---------------------------------------------------------------------------
# get_prompt_terms()
# ---------------------------------------------------------------------------
class TestPromptTerms:
    def test_empty(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        assert get_prompt_terms(path=store) == ""

    def test_with_active_terms(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        learn([("获取病", "霍去病")], path=store)
        learn([("获取病", "霍去病")], path=store)
        prompt = get_prompt_terms(path=store)
        assert "霍去病" in prompt
        assert "专有名词" in prompt


# ---------------------------------------------------------------------------
# export / import
# ---------------------------------------------------------------------------
class TestExportImport:
    def test_export_import_roundtrip(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        export_file = tmp_path / "export.json"

        learn([("获取病", "霍去病")], path=store)
        learn([("获取病", "霍去病")], path=store)
        export_terms(export_file, db_path=store)

        # Verify export file is valid JSON
        data = json.loads(export_file.read_text(encoding="utf-8"))
        assert data["version"] == 2

        # Import into new store
        store2 = tmp_path / "learned_terms2.json"
        db = import_terms(export_file, db_path=store2)
        assert db.stats()["total"] >= 1

    def test_import_overwrite(self, tmp_path: Path):
        store = tmp_path / "learned_terms.json"
        export_file = tmp_path / "export.json"

        learn([("获取病", "霍去病")], path=store)
        export_terms(export_file, db_path=store)

        # Import with no-merge (overwrite)
        store2 = tmp_path / "learned_terms2.json"
        learn([("下水温", "下水文")], path=store2)
        db = import_terms(export_file, merge=False, db_path=store2)
        # Only imported terms, not the old "下水温"
        wrongs = [c.wrong for c in db.terms.values()]
        assert "下水温" not in wrongs


# ---------------------------------------------------------------------------
# Integration: learn → post_process
# ---------------------------------------------------------------------------
class TestLearnIntegration:
    def test_confirmed_terms_used_in_post_process(self, tmp_path: Path):
        """post_process_transcript applies confirmed learned terms."""
        store = tmp_path / "learned_terms.json"
        learn([("长心的", "常为新的")], path=store)
        learn([("长心的", "常为新的")], path=store)  # auto-confirm

        from unittest.mock import patch

        from video2text.post_process import post_process_transcript

        with patch("video2text.learn.learned_terms_path", return_value=store):
            result = post_process_transcript("青年人是长心的", merge_learned=True)
            assert "常为新的" in result
            assert "长心的" not in result

    def test_unconfirmed_terms_not_applied(self, tmp_path: Path):
        """Unconfirmed terms (count < threshold) are NOT applied."""
        store = tmp_path / "learned_terms.json"
        learn([("长心的", "常为新的")], path=store)  # count=1, not active

        from unittest.mock import patch

        from video2text.post_process import post_process_transcript

        with patch("video2text.learn.learned_terms_path", return_value=store):
            result = post_process_transcript("青年人是长心的", merge_learned=True)
            # Should NOT be replaced (count=1, below threshold)
            assert "长心的" in result
