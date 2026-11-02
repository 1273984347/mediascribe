"""Tests for learn.audit_terms — term-library hygiene audit (2026-11-02).

背景: SAFE_TERMS 重复对连挂 5 次 CI 的事故后, 查重长期靠手工纪律;
本模块把五类检查 (conflict/reverse/chain/near_dup/noop) 钉死为可跑命令。
"""

from __future__ import annotations

import json
from pathlib import Path

from mediascribe.learn import _load_db, _save_db, audit_terms


def _make_db(tmp_path: Path, pairs: list[tuple[str, str]]):
    """直接构造带指定对的术语库文件, 返回路径。"""
    path = tmp_path / "terms.json"
    terms = {
        f"{w}\x00{r}": {
            "wrong": w,
            "right": r,
            "count": 2,
            "confirmed": True,
            "contexts": [],
            "source": "test",
        }
        for w, r in pairs
    }
    path.write_text(
        json.dumps({"version": 2, "terms": terms}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _kinds(findings, severity=None):
    if severity is not None:
        findings = [f for f in findings if f.severity == severity]
    return {f.kind for f in findings}


class TestAuditTerms:
    def test_clean_db_no_findings(self, tmp_path):
        path = _make_db(tmp_path, [("获取病", "霍去病"), ("爱新觉罗", "爱新觉罗·玄烨")])
        assert audit_terms(path) == []

    def test_empty_db_no_findings(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("{}", encoding="utf-8")
        assert audit_terms(path) == []

    def test_conflict_same_wrong_multiple_rights(self, tmp_path):
        path = _make_db(tmp_path, [("徐涛", "徐涛老师"), ("徐涛", "徐涛教授")])
        findings = audit_terms(path)
        assert "conflict" in _kinds(findings, "error")

    def test_reverse_pair(self, tmp_path):
        path = _make_db(tmp_path, [("甲乙", "丙丁"), ("丙丁", "甲乙")])
        findings = audit_terms(path)
        assert "reverse" in _kinds(findings, "error")

    def test_chain_double_hit(self, tmp_path):
        path = _make_db(tmp_path, [("甲乙", "丙丁"), ("丙丁", "戊己")])
        findings = audit_terms(path)
        assert "chain" in _kinds(findings, "warning")

    def test_no_chain_when_target_maps_to_itself(self, tmp_path):
        # A→B 且 B→B (B 是自身目标) 不构成双跳
        path = _make_db(tmp_path, [("甲乙", "丙丁"), ("丙丁", "丙丁")])
        kinds = _kinds(audit_terms(path))
        assert "chain" not in kinds

    def test_near_dup_normalization_variant(self, tmp_path):
        path = _make_db(tmp_path, [("获取病", "霍去病"), ("获取病 ", "霍去病")])
        findings = audit_terms(path)
        assert "near_dup" in _kinds(findings, "warning")

    def test_noop_case_only_pair(self, tmp_path):
        path = _make_db(tmp_path, [("AI", "ai")])
        findings = audit_terms(path)
        assert "noop" in _kinds(findings, "warning")

    def test_homophone_variant_detected(self, tmp_path):
        # 归一化不同 (字不同) 但两侧拼音全同 — 同音字变体重复
        path = _make_db(tmp_path, [("在与此", "再接再厉"), ("载与此", "再接再利")])
        findings = audit_terms(path)
        assert "near_dup" in _kinds(findings, "warning")

    def test_findings_carry_evidence_detail(self, tmp_path):
        path = _make_db(tmp_path, [("徐涛", "徐涛老师"), ("徐涛", "徐涛教授")])
        (f,) = audit_terms(path)
        assert "徐涛" in f.detail and f.severity == "error"

    def test_roundtrip_with_load_db(self, tmp_path):
        path = _make_db(tmp_path, [("获取病", "霍去病")])
        db = _load_db(path)
        db.add("徐涛", "徐涛老师")
        db.add("徐涛", "徐涛教授")
        _save_db(db, path)
        kinds = _kinds(audit_terms(path), "error")
        assert kinds == {"conflict"}
