"""Gates for the restored queue: stage-chain state and paged job list.

P0-1 (2026-11-28): ``restoreQueue()`` painted only the text of a terminal
card (chip / stage name / percentage) and never touched the six stage nodes,
so a finished task showed an empty progress chain next to the word
"完成 100%" -- a self-contradicting card after every reload.

P0-2 (2026-11-28): ``GET /api/jobs`` returned every historical job and the UI
rendered them one by one, which stretched the page to ~27 screens with 127
stored tasks.

The repository has no JavaScript test runner, so the front-end gates use the
source-level assertion style already used by
tests/test_web_interaction_review.py.  The API gates drive the real FastAPI
route through TestClient.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mediascribe.web.app import create_app

JS_PATH = (
    Path(__file__).resolve().parent.parent
    / "mediascribe"
    / "web"
    / "static"
    / "modules"
    / "jobs.js"
)
JS_SRC = JS_PATH.read_text(encoding="utf-8")


def _function_body(src: str, name: str) -> str:
    """Return the body of a top-level JS function via brace matching.

    Brace counting is exact for the JS in this repo; splitting on a textual
    marker would break as soon as the marker appears twice.
    """
    start = src.index("function " + name)
    paren_start = src.index("(", start)
    depth = 0
    for pos in range(paren_start, len(src)):
        char = src[pos]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                break
    else:  # pragma: no cover - source corruption
        raise AssertionError("unbalanced parameter list: " + name)
    body_start = src.index("{", pos)
    depth = 0
    for pos in range(body_start, len(src)):
        char = src[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return src[body_start : pos + 1]
    raise AssertionError("unterminated function: " + name)


def _region(src: str, start_marker: str, end_marker: str) -> str:
    """Return the slice between two markers (exclusive of the end marker)."""
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


def test_apply_chain_state_is_a_module_level_function() -> None:
    """The chain painter must live outside the WebSocket closure.

    ``restoreQueue()`` renders cards **without** a WebSocket, so it needs its
    own way to paint the six stage nodes.  Keeping the helper at module level
    makes it reachable from both paths.
    """
    assert "function applyChainState(" in JS_SRC
    # Module level => the definition starts at column 0 (not indented inside
    # another function body).
    definition = next(
        line for line in JS_SRC.splitlines() if line.startswith("function applyChainState(")
    )
    assert definition.startswith("function applyChainState(card, opts)")
    body = _function_body(JS_SRC, "applyChainState(")
    # All three modes must be covered.
    assert 'mode === "done"' in body
    assert 'mode === "fail"' in body
    assert 'mode === "off"' in body
    # The three node states the stylesheet provides.
    assert '"node done"' in body
    assert '"node fail"' in body
    assert '"node off"' in body
    # Out-of-range / missing stage index falls back to 0.
    assert "STAGES.length" in body


def test_every_terminal_branch_paints_the_chain() -> None:
    """All four terminal branches of restoreQueue must paint the chain.

    P0-1 was exactly this: the four branches wrote chip / stage / percent
    but never the nodes, so a reloaded card looked broken.
    """
    body = _function_body(JS_SRC, "restoreQueue(")
    assert body.count("applyChainState(") >= 4

    # cancelled => off / stage 0
    cancelled = _region(body, "if (job.cancelled)", "if (job.error)")
    assert 'mode: "off"' in cancelled
    assert "stageIndex: 0" in cancelled

    # error => fail / the stage it died on
    failed = _region(body, "if (job.error)", 'card.classList.add("ok")')
    assert 'mode: "fail"' in failed
    assert "stageIndex: job.stage_index" in failed

    # success => every node done
    succeeded = _region(body, 'card.classList.add("ok")', "fetchResult(card, job)")
    assert 'mode: "done"' in succeeded

    # interrupted => off / the stage it was interrupted on
    interrupted = _region(body, "if (interrupted)", "if (job.finished)")
    assert 'mode: "off"' in interrupted
    assert "stageIndex: job.stage_index" in interrupted


def test_restore_queue_pages_and_folds_finished_cards() -> None:
    """The UI must request a page and fold finished cards away."""
    body = _function_body(JS_SRC, "restoreQueue(")
    # A bounded page, not the whole history.
    assert "limit=50" in body
    assert "offset=" in body
    # The collapsible container for the finished cards.
    assert "ensureFinishedGroup(" in body
    assert "container: doneBody" in body
    # The "load more" entry point and the counters.
    assert "updateMoreButton(" in body
    assert "updateDoneSummary(" in body


def test_finished_group_markup_is_created() -> None:
    """The collapsed group and the load-more button are created by the JS."""
    assert "function ensureFinishedGroup(" in JS_SRC
    assert '"finished-group"' in JS_SRC
    assert '"finished-body"' in JS_SRC
    assert '"more-history"' in JS_SRC
    assert "function loadMoreHistory(" in JS_SRC
    # The collapsed container is a native <details> => collapsed costs no
    # layout height, and its summary carries the finished count.
    assert 'document.createElement("details")' in JS_SRC
    assert 'document.createElement("summary")' in JS_SRC
    assert "已结束 (" in JS_SRC


def test_job_list_is_paged(tmp_path: Path) -> None:
    """GET /api/jobs reports total/truncated and honours limit."""
    app = create_app(workspace=tmp_path / "ws")
    try:
        with TestClient(app) as client:
            response = client.get("/api/jobs")
            assert response.status_code == 200
            payload = response.json()
            assert isinstance(payload["jobs"], list)
            assert isinstance(payload["total"], int)
            assert isinstance(payload["truncated"], bool)
            assert payload["total"] >= len(payload["jobs"])
            # No limit parameter => the default page size caps the response.
            assert len(payload["jobs"]) <= 50

            limited = client.get("/api/jobs?limit=5").json()
            assert len(limited["jobs"]) <= 5
            assert limited["total"] >= len(limited["jobs"])
            if limited["total"] > len(limited["jobs"]):
                assert limited["truncated"] is True
    finally:
        app.state.job_executor.shutdown(wait=True, cancel_futures=True)
        store = getattr(app.state, "job_store", None)
        if store is not None:
            store.close()


def test_limit_cuts_the_page_but_total_stays_complete(tmp_path: Path) -> None:
    """A limited page is truncated while total keeps counting every job."""
    app = create_app(workspace=tmp_path / "ws")
    try:
        store = app.state.job_store
        for index in range(3):
            store.upsert(f"chain-job-{index}", f"https://example.com/{index}.mp4")

        with TestClient(app) as client:
            everything = client.get("/api/jobs").json()
            assert everything["total"] == 3
            assert len(everything["jobs"]) == 3
            assert everything["truncated"] is False

            first = client.get("/api/jobs?limit=2").json()
            assert len(first["jobs"]) == 2
            assert first["total"] == 3
            assert first["truncated"] is True
    finally:
        app.state.job_executor.shutdown(wait=True, cancel_futures=True)
        store = getattr(app.state, "job_store", None)
        if store is not None:
            store.close()


def test_job_list_clamps_out_of_range_limits(tmp_path: Path) -> None:
    """Out-of-range limits are pinned to a bound instead of raising."""
    app = create_app(workspace=tmp_path / "ws")
    try:
        store = app.state.job_store
        for index in range(250):
            store.upsert(f"clamp-job-{index}", f"https://example.com/{index}.mp4")

        with TestClient(app) as client:
            assert client.get("/api/jobs?limit=0").status_code == 200
            assert client.get("/api/jobs?limit=-5").status_code == 200
            assert client.get("/api/jobs?limit=99999").status_code == 200
            huge = client.get("/api/jobs?limit=99999").json()
            # 250 rows were stored, but the durable history window is 200
            # entries (JobStore.list's own limit, unchanged by this fix),
            # so the visible set is capped there.
            assert len(huge["jobs"]) == 200
            assert huge["total"] == 200
            zero = client.get("/api/jobs?limit=0").json()
            assert len(zero["jobs"]) == 1
            negative = client.get("/api/jobs?limit=-5").json()
            assert len(negative["jobs"]) == 1
    finally:
        app.state.job_executor.shutdown(wait=True, cancel_futures=True)
        store = getattr(app.state, "job_store", None)
        if store is not None:
            store.close()
