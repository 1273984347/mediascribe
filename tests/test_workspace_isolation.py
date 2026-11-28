"""Regression gate: the web workspace must not leak into the real one.

Background (P0-3, 2026-11-28): ``build_app()`` derived its default
workspace from ``Path.cwd()``.  pytest runs from the repository root, so
every web test that did not pass an explicit ``workspace=`` wrote its job
rows and transcripts into ``<repo>/web-workspace/`` -- the user's real
workspace.  These tests pin the fixed behaviour:

* the default workspace is resolved by a three-step anchor (environment
  variable > source tree > home fallback);
* the test session runs with ``MEDIASCRIBE_WORKSPACE`` pointed at a
  throw-away directory, so nothing reaches the repository workspace;
* the repository workspace holds no task rows for the test fixture URL
  ``https://example.com/video.mp4`` and no fixture transcripts.

The gates below inspect the repository workspace strictly: any job row
or transcript that belongs to the test fixture is a failure.  A missing
workspace directory is not a failure -- the gate is about what the tests
*write*, not about the existence of the directory.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_WORKSPACE = REPO_ROOT / "web-workspace"
REPO_JOB_DB = REPO_WORKSPACE / "jobs.sqlite3"
FIXTURE_URL = "https://example.com/video.mp4"


def _repo_db_snapshot() -> tuple[bool, int, int]:
    """Return ``(exists, size, mtime_ns)`` of the repository job database."""
    if not REPO_JOB_DB.exists():
        return (False, 0, 0)
    stat = REPO_JOB_DB.stat()
    return (True, stat.st_size, stat.st_mtime_ns)


def _repo_fixture_jobs() -> list[tuple[str, str]]:
    """Return the repository job rows that point at the test fixture URL.

    The database is copied first (together with its WAL side files) so the
    repository file is only ever read, never opened for writing.
    """
    if not REPO_JOB_DB.exists():
        return []
    staging = Path(tempfile.mkdtemp(prefix="mediascribe-repo-db-"))
    try:
        copy = staging / "jobs.sqlite3"
        shutil.copy2(REPO_JOB_DB, copy)
        for suffix in ("-wal", "-shm"):
            side = REPO_JOB_DB.with_name(REPO_JOB_DB.name + suffix)
            if side.exists():
                shutil.copy2(side, copy.with_name(copy.name + suffix))
        with sqlite3.connect(copy) as conn:
            rows = conn.execute(
                "SELECT job_id, url FROM jobs WHERE url = ?", (FIXTURE_URL,)
            ).fetchall()
        return [(str(row[0]), str(row[1])) for row in rows]
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _repo_fixture_transcripts() -> set[str]:
    """Return the names of repository transcripts that hold fixture content."""
    out_dir = REPO_WORKSPACE / "out"
    if not out_dir.exists():
        return set()
    offenders: set[str] = set()
    for path in out_dir.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "fixture" in text and "# Transcript" in text:
            offenders.add(path.name)
    return offenders


def test_environment_variable_wins() -> None:
    """A non-empty ``MEDIASCRIBE_WORKSPACE`` takes priority over everything."""
    from mediascribe.web.assembly import resolve_default_workspace

    target = Path("C:/somewhere/explicit-workspace")
    assert resolve_default_workspace(env_value=str(target)) == target
    # A blank value counts as "not set" and falls through to the anchors.
    assert resolve_default_workspace(env_value="   ", cwd=Path("C:/")) != target


def test_source_tree_anchor_is_used_when_no_env_is_set(tmp_path: Path) -> None:
    """A directory below a ``pyproject.toml`` root anchors the workspace there."""
    from mediascribe.web.assembly import resolve_default_workspace

    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    resolved = resolve_default_workspace(env_value="", cwd=repo / "sub", home=tmp_path)
    assert resolved == repo / "web-workspace"


def test_home_fallback_when_no_source_tree_is_found(tmp_path: Path) -> None:
    """Without a ``pyproject.toml`` above the cwd the home fallback is used."""
    from mediascribe.web.assembly import resolve_default_workspace

    home = tmp_path / "home"
    deep = home / "a" / "b" / "c"
    deep.mkdir(parents=True)

    resolved = resolve_default_workspace(env_value="", cwd=deep, home=home)
    assert resolved == home / ".mediascribe" / "workspace"
    assert resolved != deep / "web-workspace"


def test_home_fallback_reads_the_process_home_by_default(tmp_path: Path, monkeypatch) -> None:
    """The fallback follows ``Path.home()`` when no explicit home is given."""
    from mediascribe.web.assembly import resolve_default_workspace

    home = tmp_path / "home"
    deep = home / "a" / "b"
    deep.mkdir(parents=True)
    monkeypatch.delenv("MEDIASCRIBE_WORKSPACE", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(deep)

    resolved = resolve_default_workspace()
    assert resolved == home / ".mediascribe" / "workspace"


def test_default_workspace_app_writes_only_into_the_isolated_workspace(
    isolated_test_workspace: Path,
) -> None:
    """An app built without ``workspace=`` must not touch the repository."""
    from mediascribe.web.app import create_app

    assert os.environ.get("MEDIASCRIBE_WORKSPACE") == str(isolated_test_workspace)
    assert isolated_test_workspace != REPO_WORKSPACE

    before = _repo_db_snapshot()
    app = create_app()
    try:
        # Submit a durable job exactly the way the web route does: the row
        # lands in ``<workspace>/jobs.sqlite3`` -- the very table that used
        # to be polluted in the repository workspace.
        job = app.state.job_control.submit(
            FIXTURE_URL,
            request={},
            selection={},
            out_path=isolated_test_workspace / "out" / "fixture.md",
        )
        snapshot = app.state.job_control.snapshot(job.job_id)
        assert snapshot is not None
    finally:
        app.state.job_executor.shutdown(wait=True, cancel_futures=True)
        store = getattr(app.state, "job_store", None)
        if store is not None:
            store.close()

    isolated_db = isolated_test_workspace / "jobs.sqlite3"
    assert isolated_db.exists()
    with sqlite3.connect(isolated_db) as conn:
        urls = conn.execute("SELECT url FROM jobs WHERE url = ?", (FIXTURE_URL,)).fetchall()
    assert urls, "the job row was not written to the isolated workspace"

    assert _repo_db_snapshot() == before


def test_repository_workspace_has_no_fixture_jobs() -> None:
    """Gate: the repository workspace must not hold test-fixture job rows.

    The database is inspected read-only (it is copied first) and a missing
    file is not a failure: the gate is about what the tests *write*, not
    about the existence of the workspace directory.
    """
    offending = _repo_fixture_jobs()
    assert not offending, (
        f"test fixture jobs leaked into the repository workspace: {sorted(offending)}"
    )


def test_repository_workspace_has_no_fixture_transcripts() -> None:
    """Gate: no fixture transcript may sit in the repository workspace."""
    offenders = sorted(_repo_fixture_transcripts())
    assert not offenders, offenders
