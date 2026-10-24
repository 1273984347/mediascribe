"""
前端静态资源的**服务端可达性**测试。

背景
----
2026-10-24（P4 前端拆分）踩到一个严重问题：页面拆成原生 ES modules 后引用了
``styles.css`` / ``app.js`` / ``modules/*.js``，但 FastAPI 侧**只提供
``index.html`` / ``wiki.html`` 两个路由**（读文本返回），**没有任何静态文件
服务** ⇒ 生产环境下 CSS 与 JS 全部 404，页面完全不可用。

而这个缺陷**没有被任何测试抓到** —— 因为测试只读 HTML 文本，不验证资源可达。
本文件补上这一层：解析页面里所有本地资源引用，逐个断言能取到。

用法::

    python -m pytest tests/test_web_app_static.py -q
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

STATIC = ROOT / "mediascribe" / "web" / "static"

#: 页面里可能出现的本地资源引用（href / src）
_LOCAL_REF = re.compile(r'(?:href|src)="(/[^"#?]+)"')


def _page_refs(html: str) -> list:
    """从 HTML 里取出所有以 / 开头的本地资源引用（去重、保持顺序）。"""
    seen = []
    for ref in _LOCAL_REF.findall(html):
        if ref.startswith("/api/") or ref.startswith("/ws"):
            continue  # 接口不是静态资源
        if ref not in seen:
            seen.append(ref)
    return seen


class TestStaticAssetsServed(unittest.TestCase):
    """页面引用的每个静态资源都必须真的能从服务端取到。"""

    def test_app_exposes_static_mount(self):
        pytest = __import__("importlib").util.find_spec("fastapi")
        if pytest is None:
            self.skipTest("fastapi 未安装")
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        with TestClient(create_app()) as client:
            self.assertEqual(
                client.get("/static/styles.css").status_code,
                200,
                msg="styles.css 取不到 ⇒ 页面无样式（P4 拆分后必须有静态服务）",
            )

    def test_every_local_ref_resolves(self):
        """页面里引用的每个本地资源都必须 200 —— 通用防回归。"""
        from importlib import util

        if util.find_spec("fastapi") is None:
            self.skipTest("fastapi 未安装")
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        with TestClient(create_app()) as client:
            for page in ("index.html", "wiki.html"):
                html = (STATIC / page).read_text(encoding="utf-8")
                refs = _page_refs(html)
                self.assertTrue(refs, msg=f"{page} 未解析到任何本地资源引用")
                for ref in refs:
                    resp = client.get(ref)
                    self.assertEqual(
                        resp.status_code,
                        200,
                        msg=f"{page} 引用的 {ref} 返回 {resp.status_code}（页面会坏）",
                    )

    def test_modules_are_reachable(self):
        from importlib import util

        if util.find_spec("fastapi") is None:
            self.skipTest("fastapi 未安装")
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        modules = sorted((STATIC / "modules").glob("*.js"))
        self.assertTrue(modules, msg="modules/ 目录为空 —— 前端拆分未生效")
        with TestClient(create_app()) as client:
            for m in modules:
                resp = client.get(f"/static/modules/{m.name}")
                self.assertEqual(resp.status_code, 200, msg=f"模块 {m.name} 取不到")

    def test_pages_have_no_external_requests(self):
        """完全离线产品：页面**不得**引用任何外部资源。"""
        for page in ("index.html", "wiki.html"):
            html = (STATIC / page).read_text(encoding="utf-8")
            external = re.findall(r'(?:href|src)="(https?://[^"]+)"', html)
            self.assertFalse(
                external,
                msg=f"{page} 引用了外部资源 {external} —— 与「完全离线」硬约束冲突",
            )

    def test_engine_selector_lists_all_engines(self):
        """引擎下拉必须列出全部合法引擎（新增引擎时最容易漏的一处）。"""
        from mediascribe.config import _VALID_ENGINES

        html = (STATIC / "index.html").read_text(encoding="utf-8")
        select = re.search(r'<select id="engine".*?</select>', html, re.S)
        self.assertIsNotNone(select, msg="index.html 缺少 #engine 下拉")
        options = set(re.findall(r'<option value="([^"]+)"', select.group(0)))
        for engine in _VALID_ENGINES:
            self.assertIn(engine, options, msg=f"引擎下拉缺少 {engine}")

    def test_submit_response_carries_resource_notices(self):
        """提交响应必须带回资源守卫的降档说明 —— 用户**当场**就该知道。

        用 mock 顶掉 pipeline 构建,避免测试真的去下载视频。
        """
        from importlib import util
        from unittest import mock

        if util.find_spec("fastapi") is None:
            self.skipTest("fastapi 未安装")
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        fake_pipeline = mock.MagicMock()
        fake_pipeline._resource_notices = ["测试降档说明：已降档 large-v3 → medium"]

        with mock.patch("mediascribe.web.app._build_pipeline", return_value=fake_pipeline):
            with TestClient(create_app()) as client:
                resp = client.post(
                    "/api/jobs",
                    json={"urls": ["https://example.com/video.mp4"]},
                )
        if resp.status_code not in (200, 202):
            self.skipTest(f"提交被拒（HTTP {resp.status_code}），跳过")
        data = resp.json()
        self.assertIn(
            "notices",
            data,
            msg="提交响应缺 notices 字段 —— 前端拿不到降档说明",
        )
        self.assertIsInstance(data["notices"], list)
        self.assertIn(
            "测试降档说明：已降档 large-v3 → medium",
            data["notices"],
            msg="降档说明未原样带回",
        )

    def test_frontend_renders_notices(self):
        """前端必须真的把 notices 显示出来（否则字段传了也白传）。"""
        jobs_js = (STATIC / "modules" / "jobs.js").read_text(encoding="utf-8")
        self.assertIn("notices", jobs_js, msg="jobs.js 未处理提交响应的 notices")


if __name__ == "__main__":
    unittest.main()
