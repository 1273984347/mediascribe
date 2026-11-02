"""wiki.js 渲染器冒烟测试的 pytest 包装 (Phase 4, 2026-11-03)。

前端 JS 没有测试框架 — 渲染器纯函数的断言住在
``scripts/tools/wiki_render_smoke.mjs`` (node 直跑, 无框架依赖)。
本包装把它接进 pytest 门禁: 有 node 就跑, 没有就跳过
(CI runner 预装 node; 本地无 node 的纯 Python 环境不受影响)。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "tools" / "wiki_render_smoke.mjs"

node = shutil.which("node")

pytestmark = pytest.mark.skipif(node is None, reason="node not available")


def test_wiki_renderer_smoke():
    """渲染器冒烟全过 (XSS 转义 / 时间戳深链 / wikilink / 结构)。"""
    r = subprocess.run(
        [node, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(ROOT),
    )
    assert r.returncode == 0, f"renderer smoke failed:\n{r.stdout}\n{r.stderr}"
    assert "ALL PASS" in r.stdout
