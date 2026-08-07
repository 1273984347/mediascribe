"""Coverage gap-fillers for v3.2.0a Tier 2 features.

Targets uncovered branches in:
- ``video2text.cache``           (corrupted index, ttl=0, max_bytes=0, OSError)
- ``video2text.profile_cli``     (parse_iso, top=0, empty groups, filter_records)
- ``video2text.web.app``         (3 new v3.2.0a endpoints)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# cache.py — 14 missing statements
# ---------------------------------------------------------------------------


class TestCacheCoverageGaps:
    def test_corrupted_index_returns_empty(self, tmp_path: Path) -> None:
        """A JSONDecodeError on the index file must not raise — return {}."""
        from video2text import cache

        # Pre-populate an unparseable index file
        index_file = tmp_path / "downloads.json"
        index_file.write_text("{not valid json", encoding="utf-8")
        result = cache._load_index(tmp_path, "downloads")
        assert result == {}

    def test_fallback_to_home_on_no_env(self, monkeypatch) -> None:
        """When XDG / LOCALAPPDATA are unset, fall back to ~/.cache/<app>."""
        from video2text import cache

        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.delenv("VIDEO2TEXT_CACHE_DIR", raising=False)
        result = cache.persistent_cache_dir("test_fallback_app")
        # Path is non-empty and includes the app name
        assert result.name == "test_fallback_app" or "test_fallback_app" in str(result)

    def test_purge_with_zero_ttl_returns_zero(self, tmp_path: Path) -> None:
        from video2text.cache import PersistentDownloadCache

        c = PersistentDownloadCache(tmp_path, ttl_seconds=0)
        # Create a real source file before put
        src = tmp_path / "src.bin"
        src.write_bytes(b"hi")
        c.put("https://example.com/a", src)
        c.get("https://example.com/a")
        # ttl=0 means prune is a no-op
        assert c.prune() == 0

    def test_lru_evict_with_zero_max_bytes_noop(self, tmp_path: Path) -> None:
        from video2text.cache import PersistentDownloadCache

        c = PersistentDownloadCache(tmp_path, max_bytes=0)
        src = tmp_path / "src.bin"
        src.write_bytes(b"hi")
        c.put("https://example.com/a", src)
        c._enforce_cap()  # should be a no-op when max_bytes=0
        assert c.contains("https://example.com/a")

    def test_index_save_handles_oserror_on_windows(self, tmp_path: Path) -> None:
        """A read-only index file should not raise during _save_index.

        On non-Windows we lock the dir; on Windows we use a no-op to keep
        the test deterministic.
        """
        from video2text import cache

        cache._save_index(tmp_path, "downloads", {"x": {"path": "a"}})
        if os.name != "nt":
            os.chmod(tmp_path, 0o400)
        try:
            # Should not raise
            cache._save_index(tmp_path, "downloads", {"y": {"path": "b"}})
        finally:
            if os.name != "nt":
                os.chmod(tmp_path, 0o700)


# ---------------------------------------------------------------------------
# profile_cli.py — 8 missing statements
# ---------------------------------------------------------------------------


class TestProfileCliCoverageGaps:
    def test_parse_iso_invalid_returns_none(self) -> None:
        from video2text.profile_cli import _parse_iso

        assert _parse_iso("not a date") is None
        assert _parse_iso("") is None

    def test_filter_records_keeps_records_with_unparseable_ts(self) -> None:
        """Records whose ``ts`` cannot be parsed are kept (line 88-91)."""
        from video2text.profile_cli import filter_records

        records = [
            {"ts": "garbage", "label": "a", "duration_sec": 0.1},
            {"ts": "2026-01-02T00:00:00Z", "label": "b", "duration_sec": 0.2},
        ]
        out = filter_records(records, since="2026-01-01")
        # "garbage" ts is kept (None parse); "b" is kept (after cutoff)
        assert len(out) == 2

    def test_render_json_top_zero_renders_all(self) -> None:
        from video2text.profile_cli import aggregate, render_json

        records = [
            {"ts": "2026-01-01T00:00:00Z", "label": "a", "duration_sec": 0.1},
            {"ts": "2026-01-01T00:00:01Z", "label": "b", "duration_sec": 0.5},
        ]
        agg = aggregate(records)
        out = json.loads(render_json(agg, top=0))
        assert "stages" in out
        # top=0 means render all (line 168-169 skipped → all included)
        assert len(out["stages"]) == 2

    def test_filter_records_tz_aware_vs_naive(self) -> None:
        """tz-aware record >= naive cutoff — line 93-95 normalisation."""
        from video2text.profile_cli import filter_records

        records = [
            {"ts": "2026-01-02T00:00:00+00:00", "label": "a", "duration_sec": 0.1},
        ]
        # naive cutoff — get tz-normalised under the hood
        out = filter_records(records, since="2026-01-01")
        assert len(out) == 1


# ---------------------------------------------------------------------------
# web/app.py — 3 new v3.2.0a endpoints
# ---------------------------------------------------------------------------


try:
    from starlette.testclient import TestClient

    from video2text.web.app import create_app

    _HAS_WEB = True
except Exception:  # pragma: no cover
    _HAS_WEB = False


@pytest.mark.skipif(not _HAS_WEB, reason="web app not importable")
class TestWebAppJobEndpoints:
    def _build_app(self, workspace: Path):
        # Clean any old env leakage
        os.environ.pop("VIDEO2TEXT_API_TOKEN", None)
        return create_app(workspace=workspace)

    def test_ws_progress_unknown_job_sends_error_and_closes(self, tmp_path: Path) -> None:
        """The /ws/progress/{id} handler must reply with an error event
        when ``job_id`` is unknown."""
        app = self._build_app(tmp_path / "ws-unknown")
        with TestClient(app) as client:
            with client.websocket_connect("/ws/progress/does-not-exist") as ws:
                msg = ws.receive_json()
                assert msg["event"] == "error"
                assert "unknown" in msg["message"].lower()

    def test_ws_progress_sends_snapshot_for_existing_job(self, tmp_path: Path) -> None:
        """Connecting to a real job should yield a snapshot event first."""
        from video2text.progress import ProgressRegistry

        app = self._build_app(tmp_path / "ws-snapshot")
        # Replace the default registry with one holding a fresh job
        reg = ProgressRegistry()
        job = reg.create("https://example.com/v")
        app.state.jobs = reg

        with TestClient(app) as client:
            with client.websocket_connect(f"/ws/progress/{job.job_id}") as ws:
                msg = ws.receive_json()
                assert msg["event"] == "snapshot"
                assert msg["job_id"] == job.job_id
                # Finish so the handler's while-loop exits
                job.succeed(result={"text": "ok"})
                # Drain remaining snapshot
                while True:
                    try:
                        ev = ws.receive_json()
                    except Exception:
                        break
                    if ev.get("event") == "snapshot" and ev.get("finished"):
                        break

    def test_api_jobs_status_unknown_returns_404(self, tmp_path: Path) -> None:
        """GET /api/jobs/{id} with an unknown id → 404."""
        app = self._build_app(tmp_path / "api-status-unknown")
        # Set a token so the auth dependency passes
        os.environ["VIDEO2TEXT_API_TOKEN"] = "test-token-xyz"
        try:
            app2 = self._build_app(tmp_path / "api-status-unknown-2")
            with TestClient(app2) as client:
                r = client.get(
                    "/api/jobs/nope",
                    headers={"Authorization": "Bearer test-token-xyz"},
                )
                assert r.status_code == 404
        finally:
            os.environ.pop("VIDEO2TEXT_API_TOKEN", None)

    def test_api_jobs_cancel_unknown_returns_404(self, tmp_path: Path) -> None:
        """POST /api/jobs/{id}/cancel on an unknown id → 404 (v3.2.0a design)."""
        app = self._build_app(tmp_path / "api-cancel-unknown")
        os.environ["VIDEO2TEXT_API_TOKEN"] = "test-token-xyz"
        try:
            with TestClient(app) as client:
                r = client.post(
                    "/api/jobs/nope/cancel",
                    headers={"Authorization": "Bearer test-token-xyz"},
                )
                assert r.status_code == 404
        finally:
            os.environ.pop("VIDEO2TEXT_API_TOKEN", None)

    def test_api_jobs_cancel_known_returns_200(self, tmp_path: Path) -> None:
        """POST /api/jobs/{id}/cancel on a real job → 200 + cancelled=True."""
        from video2text.progress import ProgressRegistry

        app = self._build_app(tmp_path / "api-cancel-known")
        reg = ProgressRegistry()
        job = reg.create("https://example.com/v")
        app.state.jobs = reg
        os.environ["VIDEO2TEXT_API_TOKEN"] = "test-token-xyz"
        try:
            with TestClient(app) as client:
                r = client.post(
                    f"/api/jobs/{job.job_id}/cancel",
                    headers={"Authorization": "Bearer test-token-xyz"},
                )
                assert r.status_code == 200
                data = r.json()
                assert data["cancelled"] is True
        finally:
            os.environ.pop("VIDEO2TEXT_API_TOKEN", None)


# ---------------------------------------------------------------------------
# progress.py helper coverage
# ---------------------------------------------------------------------------


class TestProgressRegistryHelpers:
    def test_purge_does_not_remove_finished_recently(self) -> None:
        from video2text.progress import ProgressRegistry

        reg = ProgressRegistry()
        job = reg.create("https://example.com/v")
        job.succeed(result={"text": "ok"})
        # Default purge(older_than_seconds=3600) keeps fresh entries
        assert reg.purge() == 0
        assert reg.get(job.job_id) is not None

    def test_purge_removes_old_finished(self) -> None:
        from video2text.progress import ProgressRegistry

        reg = ProgressRegistry()
        job = reg.create("https://example.com/v")
        job.succeed(result={"text": "ok"})
        # Backdate created_at so older_than_seconds=0 catches it
        for j in reg._jobs.values():
            j.finished = True
            j.created_at = j.created_at - 100
        # older_than_seconds=0 catches everything old
        assert reg.purge(older_than_seconds=0) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
