"""Shared pytest configuration for the MediaScribe test suite.

把仓库根加进 sys.path, 取代此前 60+ 个测试文件各自复制的
``ROOT = Path(__file__).parent.parent.parent; sys.path.insert(...)``
样板(2026-10-03 审查收敛)。存量文件里的样板保留不动 —— 多余但无害;
新增测试直接 ``import mediascribe...`` 即可, 不再做路径体操。
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config) -> None:
    """Keep automatic product telemetry out of real user profiles during tests."""
    test_data = Path(tempfile.gettempdir()) / "mediascribe-pytest-events"
    test_data.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MEDIASCRIBE_APP_DATA", str(test_data))
    os.environ.setdefault("MEDIASCRIBE_EVENT_DB", str(test_data / "events.sqlite3"))
    os.environ.setdefault("MEDIASCRIBE_INTERNAL_TEST", "1")


@pytest.fixture(scope="session", autouse=True)
def isolated_test_workspace(tmp_path_factory) -> Path:
    """Point ``MEDIASCRIBE_WORKSPACE`` at a session-scoped temporary directory.

    The web app resolves its default workspace from that environment
    variable, so any test that does **not** pass an explicit ``workspace=``
    used to write its job rows and transcripts into the *real* workspace
    (the repository's ``web-workspace/`` when pytest runs from the repo
    root).  Isolating the variable keeps every test inside a throw-away
    directory.

    The directory is session-scoped on purpose: a per-test directory would
    be rebuilt for every case and would multiply filesystem churn.
    """
    workspace = tmp_path_factory.mktemp("mediascribe-test-workspace")
    previous = os.environ.get("MEDIASCRIBE_WORKSPACE")
    os.environ["MEDIASCRIBE_WORKSPACE"] = str(workspace)
    try:
        yield workspace
    finally:
        if previous is None:
            os.environ.pop("MEDIASCRIBE_WORKSPACE", None)
        else:
            os.environ["MEDIASCRIBE_WORKSPACE"] = previous
