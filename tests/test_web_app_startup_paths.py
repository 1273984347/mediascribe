"""
Web UI 启动路径的回归测试（2026-10-23 新增）。

背景
----
技术选型对比的 L2 实测撞出两个**既有产品缺陷**——两条文档里一直宣称的
启动路径都**必然失败**：

1. ``python -m mediascribe.web.app``
   → ``NameError: name '_register_health_routes' is not defined``
   根因：``if __name__ == "__main__"`` 块位于文件中部，早于路由注册函数
   ``_register_health_routes`` 等的定义；``create_app()`` 调用它们时它们
   尚未绑定。

2. ``uvicorn mediascribe.web.app:app``
   → ``ERROR: Error loading ASGI app. Attribute "app" not found in module``
   根因：模块没有模块级 ``app`` 对象，而模块 docstring 与
   ``scripts/one_click_up.py`` 一直宣称该启动方式。

为什么要有这个测试
----------------
这两个缺陷能长期存在，恰恰因为**没有任何测试覆盖启动路径**。本文件把
「启动路径可用」变成可回归的断言：以后谁把 ``__main__`` 块挪回去、或
删掉模块级 ``app``，CI 立刻红。

CI 接入：``unit`` job 跑全部 ``tests/``（``-m "not integration and not
network"``），本文件不触网、不起真实服务 ⇒ 自动进入门禁。
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

APP_PY = ROOT / "mediascribe" / "web" / "app.py"


class TestModuleLevelApp(unittest.TestCase):
    """``uvicorn mediascribe.web.app:app`` 的前提：模块级 ``app`` 必须存在。"""

    def test_module_exposes_app(self):
        m = importlib.import_module("mediascribe.web.app")
        self.assertTrue(
            hasattr(m, "app"),
            "mediascribe.web.app 缺少模块级 `app` —— `uvicorn mediascribe.web.app:app` 会失败",
        )

    def test_module_level_app_is_a_fastapi_instance(self):
        fastapi = pytest.importorskip("fastapi")  # noqa: F841
        m = importlib.import_module("mediascribe.web.app")
        self.assertIsInstance(m.app, fastapi.FastAPI)

    def test_module_level_app_serves_health(self):
        fastapi = pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        m = importlib.import_module("mediascribe.web.app")
        with TestClient(m.app) as client:
            resp = client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("ok", resp.json())


class TestMainBlockPlacement(unittest.TestCase):
    """``__main__`` 块必须位于所有路由注册函数定义之后。

    这条断言直接锁住缺陷 #1 的根因：只要 ``__main__`` 块被挪回文件中部，
    就会重新变成 ``NameError``。
    """

    def test_main_block_comes_after_route_registrars(self):
        source = APP_PY.read_text(encoding="utf-8")
        lines = source.splitlines()

        main_idx = None
        for i, line in enumerate(lines):
            if line.strip().startswith('if __name__ == "__main__"'):
                main_idx = i
                break
        self.assertIsNotNone(main_idx, "app.py 里找不到 __main__ 块")

        registrar_idxs = [i for i, line in enumerate(lines) if line.startswith("def _register_")]
        self.assertTrue(registrar_idxs, "app.py 里找不到路由注册函数")
        last_registrar = max(registrar_idxs)

        self.assertGreater(
            main_idx,
            last_registrar,
            (
                f"__main__ 块在第 {main_idx + 1} 行，早于最后一个路由注册函数"
                f"（第 {last_registrar + 1} 行）—— `python -m mediascribe.web.app` 会抛 NameError"
            ),
        )

    def test_no_route_registrar_defined_after_main_block(self):
        """更严格的等价断言：main 块之后不得再有 ``def _register_*``。"""
        source = APP_PY.read_text(encoding="utf-8")
        lines = source.splitlines()
        main_idx = next(
            i
            for i, line in enumerate(lines)
            if line.strip().startswith('if __name__ == "__main__"')
        )
        after = [
            i for i, line in enumerate(lines) if i > main_idx and line.startswith("def _register_")
        ]
        self.assertFalse(
            after,
            f"__main__ 块之后仍有路由注册函数定义（行号 {[i + 1 for i in after]}）",
        )


class TestCliEntryPoint(unittest.TestCase):
    """``python -m mediascribe.web.app`` 必须能跑起来（不只 --help）。"""

    def test_module_cli_help_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "mediascribe.web.app", "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"`python -m mediascribe.web.app --help` 失败：\n{proc.stderr[-1500:]}",
        )
        self.assertIn("--port", proc.stdout)


if __name__ == "__main__":
    unittest.main()
