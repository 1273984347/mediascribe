"""版本号单一真源的一致性测试。

背景：Web 层曾出现三处各说各话的版本号 ——
  * ``mediascribe/web/app.py`` 硬编码 ``FastAPI(version="3.2.0c")``
  * ``/api/health`` 另写一份 ``"3.2.0c"``
  * ``mediascribe/web/static/index.html`` 的 footer 硬编码 ``v3.3.0``

真源是 ``mediascribe._version.__version__``（``mediascribe/__init__`` 转发
同一常量）。本测试锁死「真源 → 各出口」这条链：任何一处回退到硬编码
字面量、或某个出口漏改，都会变红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC_INDEX = ROOT / "mediascribe" / "web" / "static" / "index.html"
WEB_APP = ROOT / "mediascribe" / "web" / "app.py"
PYPROJECT = ROOT / "pyproject.toml"


def test_package_version_is_the_single_source() -> None:
    from mediascribe import __version__ as public
    from mediascribe._version import __version__ as canonical

    assert public == canonical
    # PEP 440: 允许 a/b/rc 预发布段(v3.4.0a1 这类 tag 的发布链验证需要);
    # dev/N 本地段仍不允许。
    assert re.fullmatch(r"\d+\.\d+\.\d+((a|b|rc)\d+)?", canonical), (
        f"版本号应形如 3.3.0 或 3.4.0a1, 实际为 {canonical!r}"
    )


def test_pyproject_version_matches_package() -> None:
    from mediascribe import __version__

    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml 里找不到 version 字段"
    assert match.group(1) == __version__, (
        f"pyproject.toml version={match.group(1)} 与 mediascribe.__version__={__version__} 不一致"
    )


def test_app_and_health_report_the_package_version(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from mediascribe import __version__
    from mediascribe.web.app import create_app

    app = create_app(workspace=tmp_path)
    assert app.version == __version__, "FastAPI 实例的 version 应等于包版本"

    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["version"] == __version__, "/api/health 的 version 应等于包版本"


def test_index_page_footer_uses_injected_version(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from mediascribe import __version__
    from mediascribe.web.app import create_app

    app = create_app(workspace=tmp_path)
    client = TestClient(app)
    html = client.get("/").text
    assert f"MediaScribe v{__version__}" in html, "footer 应渲染出当前包版本"
    assert "{{APP_VERSION}}" not in html, "占位符应已被后端替换"


def test_static_template_keeps_the_placeholder() -> None:
    html = STATIC_INDEX.read_text(encoding="utf-8")
    assert "{{APP_VERSION}}" in html, "index.html 应保留占位符, 由后端注入版本号"


def test_no_hardcoded_version_literal_remains_in_the_web_layer() -> None:
    """防回退：Web 层不得再出现硬编码版本字面量。"""
    app_src = WEB_APP.read_text(encoding="utf-8")
    assert not re.search(r'version\s*=\s*"\d+\.\d', app_src), (
        "app.py 里出现了硬编码版本号, 应改用 __version__"
    )
    assert not re.search(r'"version"\s*:\s*"\d+\.\d', app_src), (
        "app.py 里出现了硬编码的 version 字段值, 应改用 app.version"
    )

    html = STATIC_INDEX.read_text(encoding="utf-8")
    assert not re.search(r"MediaScribe v\d+\.\d", html), (
        "index.html 的 footer 不得硬编码版本号, 应使用 {{APP_VERSION}} 占位符"
    )
