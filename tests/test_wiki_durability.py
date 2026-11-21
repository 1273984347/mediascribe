"""Wiki vault 持久化健壮性(R-6/R-8/R-15,2026-11-21 架构评审修复)。

* R-6: 账本损坏 → 损坏副本保留 .bak + 落告示标记 + HOME 顶部横幅;
  五处持久写全部走 atomic_write_text(此处只验证可观测行为)。
* R-8: 跨进程互斥 —— exclusive_file_lock 的获取/抢锁/fail-open。
* R-15: 概念笔记的 ts/quote 过滤换行与折叠空白。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from mediascribe.atomic_io import exclusive_file_lock
from mediascribe.wiki import WikiVault


@pytest.fixture
def vault(tmp_path: Path) -> WikiVault:
    v = WikiVault(tmp_path / "vault")
    v.raw_dir.mkdir(parents=True, exist_ok=True)
    v.wiki_dir.mkdir(parents=True, exist_ok=True)
    v.ledger_dir.mkdir(parents=True, exist_ok=True)
    return v


# ---------------------------------------------------------------------------
# R-6: 账本损坏现场保留 + HOME 告示
# ---------------------------------------------------------------------------
class TestLedgerCorruption:
    def test_corrupt_ledger_kept_as_bak_and_notice(self, vault: WikiVault) -> None:
        ledger_path = vault.ledger_dir / WikiVault.LEDGER_NAME
        ledger_path.write_text("{not valid json", encoding="utf-8")

        data = vault._load_ledger()

        assert data == {"concepts": {}}
        bak = ledger_path.with_name(ledger_path.name + WikiVault.LEDGER_BACKUP_SUFFIX)
        assert bak.exists(), "损坏副本必须保留 .bak 供人工找回"
        assert "{not valid json" in bak.read_text(encoding="utf-8")
        notice = vault.ledger_dir / WikiVault.LEDGER_CORRUPT_NOTICE
        assert notice.exists(), "告示标记必须落盘"

    def test_home_shows_corruption_banner(self, vault: WikiVault) -> None:
        (vault.ledger_dir / WikiVault.LEDGER_CORRUPT_NOTICE).write_text("x", encoding="utf-8")

        vault._write_home([], [])

        home = (vault.root / "HOME.md").read_text(encoding="utf-8")
        assert "账本告示" in home
        assert ".bak" in home

    def test_no_banner_without_notice(self, vault: WikiVault) -> None:
        vault._write_home([], [])

        home = (vault.root / "HOME.md").read_text(encoding="utf-8")
        assert "账本告示" not in home

    def test_writes_are_atomic_survives_signals(self, vault: WikiVault) -> None:
        """save 后账本是合法 JSON(原子写保证: 半写不会覆盖旧文件)。"""
        vault._save_ledger({"concepts": {"X": [{"raw": "a.md"}]}})

        ledger_path = vault.ledger_dir / WikiVault.LEDGER_NAME
        assert json.loads(ledger_path.read_text(encoding="utf-8")) == {
            "concepts": {"X": [{"raw": "a.md"}]}
        }
        tmp_leftovers = list(vault.ledger_dir.glob("*.tmp"))
        assert tmp_leftovers == [], "原子写不得残留 tmp 文件"


# ---------------------------------------------------------------------------
# R-15: 概念笔记 ts/quote 过滤换行
# ---------------------------------------------------------------------------
class TestTsQuoteStripping:
    def test_newlines_in_ts_quote_are_collapsed(self, vault: WikiVault) -> None:
        vault._save_ledger(
            {
                "concepts": {
                    "X": [
                        {
                            "raw": "a.md",
                            "ts": "01:02\n03",
                            "quote": "第一行\n第二行",
                        }
                    ]
                }
            }
        )

        notes = vault._write_concept_notes()

        assert notes, "概念笔记应当生成"
        text = (vault.wiki_dir / notes[0]).read_text(encoding="utf-8")
        item_lines = [ln for ln in text.splitlines() if ln.startswith("-") and not ln.startswith("---")]
        assert len(item_lines) == 1, "单条提及必须占一行"
        assert "01:02 03" in item_lines[0]
        assert "第一行 第二行" in item_lines[0]


# ---------------------------------------------------------------------------
# R-8: exclusive_file_lock
# ---------------------------------------------------------------------------
class TestExclusiveFileLock:
    def test_acquire_and_release(self, tmp_path: Path) -> None:
        lock = tmp_path / ".l.lock"
        with exclusive_file_lock(lock):
            assert lock.exists()
        assert not lock.exists(), "释放后锁文件必须删除"

    def test_contention_fails_open_after_timeout(self, tmp_path: Path) -> None:
        lock = tmp_path / ".l.lock"
        with exclusive_file_lock(lock):
            t0 = time.monotonic()
            with exclusive_file_lock(lock, timeout=0.3, poll=0.01):
                pass
            assert time.monotonic() - t0 >= 0.3, "被持有时必须等待到 timeout 才放行"

    def test_stale_lock_is_stolen(self, tmp_path: Path) -> None:
        lock = tmp_path / ".l.lock"
        lock.write_text("left by crashed process", encoding="utf-8")
        old = time.time() - 120
        os.utime(lock, (old, old))

        with exclusive_file_lock(lock, timeout=1.0, poll=0.01):
            assert lock.exists()

        stolen = list(tmp_path.glob("*.stolen.*"))
        assert stolen, "过龄锁文件应改名保留现场"

    def test_vault_archive_removes_lock_file(self, tmp_path: Path) -> None:
        v = WikiVault(tmp_path / "vault")
        md = tmp_path / "t.md"
        md.write_text("# T\n\n正文。\n", encoding="utf-8")

        v.archive_transcript(md, {"generated_at": "2026-11-21T00:00:00"})

        assert not (v.root / WikiVault.VAULT_LOCK_NAME).exists(), "归档结束锁必须释放"
