"""数据治理行为测试 — 术语库路径覆盖 / clear 备份 / vault 落点覆盖(2026-10-03)."""

from __future__ import annotations

from pathlib import Path

from mediascribe.learn import clear_learned_terms, learned_terms_path
from mediascribe.wiki import vault_for_workspace


def test_learned_terms_env_override(tmp_path, monkeypatch):
    target = tmp_path / "custom" / "terms.json"
    monkeypatch.setenv("MEDIASCRIBE_LEARNED_TERMS", str(target))
    assert learned_terms_path() == target


def test_learned_terms_default_is_cache_dir(monkeypatch):
    monkeypatch.delenv("MEDIASCRIBE_LEARNED_TERMS", raising=False)
    p = learned_terms_path()
    assert p.name == "learned_terms.json"


def test_clear_creates_timestamped_backup(tmp_path, monkeypatch):
    store = tmp_path / "learned_terms.json"
    store.write_text('{"terms": {}, "next_id": 1}', encoding="utf-8")
    monkeypatch.setenv("MEDIASCRIBE_LEARNED_TERMS", str(store))
    clear_learned_terms()
    assert not store.exists()
    backups = list(tmp_path.glob("learned_terms.json.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"terms": {}, "next_id": 1}'


def test_clear_noop_when_missing(tmp_path, monkeypatch):
    store = tmp_path / "absent.json"
    monkeypatch.setenv("MEDIASCRIBE_LEARNED_TERMS", str(store))
    clear_learned_terms()  # 不抛异常即可
    assert list(tmp_path.iterdir()) == []


def test_vault_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIASCRIBE_VAULT_DIR", str(tmp_path / "my-vault"))
    vault = vault_for_workspace(tmp_path / "ws")
    assert vault is not None
    assert Path(vault.root) == tmp_path / "my-vault"


def test_vault_default_under_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIASCRIBE_VAULT_DIR", raising=False)
    vault = vault_for_workspace(tmp_path / "ws")
    assert vault is not None
    assert Path(vault.root) == tmp_path / "ws" / "vault"
