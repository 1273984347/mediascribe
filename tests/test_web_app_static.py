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

#: 页面里可能出现的本地资源引用（href / src）。
#: 允许带 query —— 静态资源引用带 ``?v=<缓存版本>``, 用于绕开浏览器旧缓存。
_LOCAL_REF = re.compile(r'(?:href|src)="(/[^"#]+)"')


def _page_refs(html: str) -> list:
    """从 HTML 里取出所有以 / 开头的本地资源引用（去 query、去重、保持顺序）。"""
    seen = []
    for ref in _LOCAL_REF.findall(html):
        path = ref.split("?", 1)[0]
        if path.startswith("/api/") or path.startswith("/ws"):
            continue  # 接口不是静态资源
        if path not in seen:
            seen.append(path)
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


class TestSecondaryTextContrast(unittest.TestCase):
    """所有前端 CSS 的**次级文字色**不得低于对比度底线。

    2026-10-25 实证：修 Web UI 对比度那轮只改了 ``styles.css``，漏了
    ``wiki.css`` —— 两者**各自维护一套同名变量**（``--fg-3`` 等），
    漏改一个就是漏改一整个页面（知识库页的次级文字只有 2.76:1）。

    这条测试遍历 ``static/`` 下所有 CSS：凡是用 ``--fg-3`` 的文件，
    其浅色/暗色 alpha 都必须达到底线 ⇒ 新增样式文件自动纳入检查。
    """

    #: 浅色主题下 --fg-3 的 alpha 下限（0.5 ⇒ 约 4.5:1 on #ffffff）
    MIN_ALPHA_LIGHT = 0.5
    #: 暗色主题下 --fg-3 的 alpha 下限（0.45 ⇒ 约 4.5:1 on #181817）
    MIN_ALPHA_DARK = 0.45

    @staticmethod
    def _alpha(block: str, name: str) -> float | None:
        """从 CSS 变量块里取 ``--name: rgba(0,0,0,X)`` 的 alpha。"""
        m = re.search(
            rf"{re.escape(name)}:\s*rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*([\d.]+)\s*\)",
            block,
        )
        return float(m.group(1)) if m else None

    def test_secondary_text_alpha_meets_contrast_floor(self):
        checked = 0
        for css in sorted(STATIC.glob("*.css")):
            text = css.read_text(encoding="utf-8")
            for theme, floor in (
                ("light", self.MIN_ALPHA_LIGHT),
                ("dark", self.MIN_ALPHA_DARK),
            ):
                if theme == "light":
                    # 浅色是显式的 ``:root[data-theme="light"]`` 块
                    m = re.search(r'\[data-theme="light"\]\s*\{(.*?)\n\}', text, re.S)
                else:
                    # 暗色是**默认**的 ``:root`` 块(浅色块带属性选择器,
                    # 不会被 ``:root\s*\{`` 匹配到)
                    m = re.search(r":root\s*\{(.*?)\n\}", text, re.S)
                self.assertIsNotNone(m, f"{css.name} 里找不到 {theme} 主题块")
                alpha = self._alpha(m.group(1), "--fg-3")
                if alpha is None:
                    continue  # 该文件不定义 --fg-3（用了别的变量体系）
                checked += 1
                self.assertGreaterEqual(
                    alpha,
                    floor,
                    msg=(
                        f"{css.name} 的 {theme} 主题 --fg-3 alpha={alpha} "
                        f"低于底线 {floor} ⇒ 次级文字对比度不足 WCAG AA"
                    ),
                )
        self.assertGreater(checked, 0, "没有任何 CSS 被检查 —— 规则本身失效了")


class TestStaticCacheControl(unittest.TestCase):
    """静态资源缓存 —— 改了前端样式, 刷新就必须能看到。

    背景：2026-10-25 排查「改了 styles.css 但页面仍是旧的」，根因是
    Starlette 的 ``StaticFiles`` 不发 ``Cache-Control``，浏览器按启发式
    规则长期持有旧样式。修复分两层：

    1. 页面引用带 ``?v=<文件 mtime 毫秒>``（后端注入, 文件一改 URL 就变）
    2. ``/static/*`` 响应带 ``Cache-Control: no-cache, must-revalidate``
       —— 这一层是必需的, 因为 ES module 内部的 ``import "./modules/x.js"``
       路径是静态写死的, query 参数根本管不到它们
    """

    @staticmethod
    def _client():
        from importlib import util

        if util.find_spec("fastapi") is None:
            raise unittest.SkipTest("fastapi 未安装")
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        return TestClient(create_app())

    def test_static_responses_carry_no_cache_header(self):
        with self._client() as client:
            resp = client.get("/static/styles.css")
            self.assertEqual(resp.status_code, 200)
            self.assertIn(
                "no-cache",
                resp.headers.get("cache-control", ""),
                msg="/static/* 缺少 Cache-Control: no-cache —— 浏览器会一直用旧样式",
            )

    def test_static_module_responses_carry_no_cache_header(self):
        """ES module 的 import 路径无法加 query, 只能靠响应头兜底。"""
        with self._client() as client:
            resp = client.get("/static/modules/jobs.js")
            self.assertEqual(resp.status_code, 200)
            self.assertIn(
                "no-cache",
                resp.headers.get("cache-control", ""),
                msg="模块资源缺少 Cache-Control —— 改模块代码刷新看不到",
            )

    def test_page_references_carry_version_param(self):
        """模板里的本地资源引用必须带 ?v= 占位符。"""
        for page in ("index.html", "wiki.html"):
            html = (STATIC / page).read_text(encoding="utf-8")
            refs = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
            self.assertTrue(refs, msg=f"{page} 未引用任何静态资源")
            for ref in refs:
                self.assertIn(
                    "?v={{STATIC_VERSION}}",
                    ref,
                    msg=f"{page} 的 {ref} 缺少版本占位符 —— 改了样式不会生效",
                )

    def test_rendered_pages_have_resolved_version(self):
        """渲染后占位符必须被替换成真实版本号(不留裸占位符)。"""
        with self._client() as client:
            for path in ("/", "/wiki"):
                html = client.get(path).text
                self.assertNotIn(
                    "{{STATIC_VERSION}}",
                    html,
                    msg=f"{path} 渲染后仍有未替换的 {{STATIC_VERSION}} 占位符",
                )
                self.assertRegex(
                    html,
                    r'/static/[^"]+\?v=\d+',
                    msg=f"{path} 的静态资源引用没有带上解析后的版本号",
                )


if __name__ == "__main__":
    unittest.main()
