"""Web UI 交互回归测试 —— 2026-10-31 交互审查的固化。

背景
----
本文件把「交互审查」发现的一批缺陷钉成自动化测试。这些缺陷有一个共同点:
**机器门禁当时全绿, 但用户流程是断的** ——

* P0-1  ``status.js`` 漏了 ``$`` 的 import ⇒ ``updateMeters()`` 抛
  ``ReferenceError`` ⇒ 任务卡渲染到一半就中断 ⇒ ``connectWs`` 从未执行
  ⇒ WebSocket 从未建立 ⇒ 用户提交后只看到一张永远停在「排队中」的卡片,
  永远拿不到文稿, 状态栏显示的是 ``提交失败:$ is not defined``。
  **没有任何 Python 测试能发现它** —— 前端 JS 没有测试覆盖。

* P0-2  转写阶段是一次阻塞调用(``progress.with_progress`` 里
  ``pipeline.transcribe`` 包住全部耗时), 期间**不发任何事件** ⇒ 服务端
  WS 的空闲超时(默认 300s)把连接掐断 ⇒ 长视频转写必然中途断线。

* P0-3  刷新页面后任务全丢: 后端没有列举任务的接口, 前端也不记 job_id。

* P1-4  ``fetchResult`` 里 ``if (!r.ok) return;`` ⇒ 取结果失败时卡片显示
  「完成」却没有文稿、没有任何错误提示。

* P1-5  FastAPI 422 的 ``detail`` 是**数组** ⇒ 前端 ``new Error(detail)``
  把整个数组 toString 成 Python dict repr 喷到状态栏。

* P2-6  页面从不提示「这台服务器要不要令牌」。

因此本文件的策略是:
**用 AST / 正则静态检查前端源码 + 用 TestClient 验证后端行为**,
把「前端有没有接上正确的钩子」变成可回归的断言。
"""

from __future__ import annotations

import ast
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

STATIC = ROOT / "mediascribe" / "web" / "static"
MODULES = STATIC / "modules"


def _has_fastapi() -> bool:
    from importlib import util

    return util.find_spec("fastapi") is not None


def _module_path(name: str) -> Path:
    """Locate a front-end source file (``modules/*.js`` or ``static/*.js``)."""
    for candidate in (MODULES / name, STATIC / name):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"找不到前端文件: {name}")


def _module_text(name: str) -> str:
    """Read a front-end source file.

    ``modules/*.js`` live under ``static/modules/``; ``wiki.js`` sits in
    ``static/`` itself — so look in both places.
    """
    return _module_path(name).read_text(encoding="utf-8")


def _imported_names(path: Path) -> set[str]:
    """Return every name bound by an ``import { ... } from`` statement.

    The front-end is JavaScript, so this is a regex scan rather than an
    ``ast`` walk — Python's parser cannot read ES modules.
    """
    src = path.read_text(encoding="utf-8")
    names: set[str] = set()
    for match in re.finditer(r"import\s*\{([^}]*)\}\s*from", src):
        for part in match.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            names.add(part.split(" as ")[-1].strip())
    return names


def _locally_defined(path: Path) -> set[str]:
    """Names declared locally in the file (``const $ = ...`` 等)。

    A module that *defines* ``$`` does not need to import it — ``dom.js``
    and ``wiki.js`` both declare their own ``$`` helper.
    """
    src = path.read_text(encoding="utf-8")
    names: set[str] = set()
    for match in re.finditer(r"(?:const|let|var|function)\s+([A-Za-z_$][\w$]*)", src):
        names.add(match.group(1))
    return names


def _function_body(code: str, signature: str) -> str:
    """Extract one function's body by brace balancing.

    Splitting on a textual marker is fragile — comment blocks get stripped
    by :func:`_code_lines`, and neighbouring functions may share the marker.
    Brace counting is exact for the JS in this repo.

    The opening brace is the one **after the parameter list** — a signature
    like ``function f(job, opts = {})`` contains a brace inside the
    parameter defaults, and naively taking the first ``{`` would capture
    the wrong region.
    """
    idx = code.find(signature)
    if idx < 0:
        raise AssertionError(f"源码里找不到 {signature!r}")
    # 跳过参数列表: 找到与签名起始处配平的右括号。
    depth = 0
    paren_start = code.find("(", idx)
    if paren_start < 0:
        raise AssertionError(f"{signature!r} 之后没有 '('")
    for pos in range(paren_start, len(code)):
        ch = code[pos]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
    else:  # pragma: no cover - 源码损坏
        raise AssertionError(f"{signature!r} 的参数列表不配平")
    start = code.find("{", pos)
    if start < 0:
        raise AssertionError(f"{signature!r} 之后没有 '{{'")
    depth = 0
    for pos in range(start, len(code)):
        ch = code[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return code[start : pos + 1]
    raise AssertionError(f"{signature!r} 的花括号不配平")


def _code_lines(path: Path) -> list[str]:
    """Source lines with comments stripped.

    Comment text must not be mistaken for code — an old snippet quoted in a
    comment (``// 旧代码 `if (!r.ok) return;` ``) is not a live statement.
    """
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        # 去掉行尾注释（不处理字符串内的 # 情况，前端源码里没有这种写法）。
        line = re.sub(r"//.*$", "", line)
        out.append(line)
    return out


class TestFrontendModuleImports(unittest.TestCase):
    """每个前端模块用到的标识符必须真的被导入 (P0-1 的根因)。

    2026-10-31 实证: ``status.js`` 用了 ``$("rec-light")`` 却只 import 了
    ``{ activeJobs, statusEl }`` ⇒ 运行时 ``ReferenceError: $ is not defined``,
    而**整个 Python 测试套件全绿** —— 因为没有任何测试跑过前端 JS。

    这条测试把「模块里出现的 ``$`` 调用 ⇒ 必须有 ``$`` 的 import」变成
    静态断言, 防止同类漏导入再次逃逸。
    """

    def test_dollar_is_imported_wherever_used(self):
        offenders = []
        # 扫全部前端 JS —— modules/ 下的模块 + static/ 根下的独立脚本。
        candidates = sorted(MODULES.glob("*.js")) + sorted(STATIC.glob("*.js"))
        for path in candidates:
            code = _code_lines(path)
            # 用法: ``$(`` —— 只看真正的代码行，不看注释。
            uses = [ln for ln in code if re.search(r"(?<![\w$])\$\s*\(", ln)]
            if not uses:
                continue
            # ``$`` 可以来自 import，也可以是本文件自己定义的辅助函数。
            if "$" in _imported_names(path) or "$" in _locally_defined(path):
                continue
            offenders.append(f"{path.name}: 用了 $ 但既没 import 也没定义")
        self.assertEqual(
            offenders,
            [],
            msg="前端模块漏 import $ ⇒ 运行时 ReferenceError（2026-10-31 P0-1）: "
            + "; ".join(offenders),
        )

    def test_status_module_imports_dollar(self):
        """定点断言 —— P0-1 的直接回归防线。"""
        self.assertIn(
            "$",
            _imported_names(MODULES / "status.js"),
            msg="status.js 必须 import $ —— 否则 updateMeters() 抛 ReferenceError",
        )

    def test_update_meters_exists_and_touches_rec_light(self):
        """updateMeters 必须真的操作指示灯 —— 它是 P0-1 的触发点。"""
        src = _module_text("status.js")
        self.assertIn("function updateMeters", src)
        self.assertIn("rec-light", src, msg="updateMeters 未操作 rec-light")


class TestFrontendJobFlowHooks(unittest.TestCase):
    """前端必须真的接上正确的钩子 (P0-1 / P1-4 / P1-5)。"""

    def test_render_job_card_opens_websocket(self):
        """任务卡渲染后必须建立 WS —— P0-1 断的就是这一步。"""
        src = _module_text("jobs.js")
        self.assertIn("connectWs(card, job)", src)
        # 顺序: connectWs 必须在 renderJobCard 内被调用。
        render_body = src.split("function renderJobCard", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn(
            "connectWs(card, job)",
            render_body,
            msg="renderJobCard 未调用 connectWs ⇒ WS 永不建立",
        )

    def test_result_fetch_failure_is_reported(self):
        """取结果失败必须显式报错 —— 旧代码是静默 return。"""
        # 用**去掉注释**的代码行 —— 旧代码片段出现在注释里, 不算活的语句。
        code = "\n".join(_code_lines(MODULES / "jobs.js"))
        fetch_body = _function_body(code, "async function fetchResult")
        self.assertIn(
            "if (!r.ok) {",
            fetch_body,
            msg="fetchResult 未对非 2xx 响应做处理",
        )
        self.assertNotIn(
            "if (!r.ok) return;",
            fetch_body,
            msg="fetchResult 仍在静默吞掉取结果失败",
        )
        self.assertIn(
            "取回文稿失败",
            fetch_body,
            msg="取结果失败没有可见的错误提示",
        )
        self.assertIn('classList.add("show")', fetch_body, msg="错误提示未激活样式")

    def test_422_detail_is_humanized(self):
        """422 的 detail 数组必须转成可读文本 (P1-5)。"""
        status_src = _module_text("status.js")
        self.assertIn("function detailToText", status_src)
        self.assertIn("Array.isArray(detail)", status_src, msg="detailToText 未处理数组形态")
        jobs_src = _module_text("jobs.js")
        self.assertIn("readError", jobs_src, msg="提交错误未走 readError")

    def test_client_count_limit_mirrors_backend(self):
        """前端上限必须与后端 max_length=20 一致 —— 否则用户白等一个 422。"""
        src = _module_text("jobs.js")
        self.assertIn("urls.length > 20", src, msg="前端未预检超过 20 条链接")
        self.assertIn("一次最多提交 20 条链接", src)

    def test_ws_reconnect_and_heartbeat_present(self):
        """P0-2: 客户端必须有重连与心跳。"""
        src = _module_text("jobs.js")
        self.assertIn("scheduleReconnect", src, msg="缺少断线重连逻辑")
        self.assertIn("startHeartbeat", src, msg="缺少心跳保活")
        self.assertIn('"pong"', src, msg="客户端未回 pong")
        self.assertIn("RECONNECT_DELAYS", src)

    def test_job_ids_persisted_and_restored(self):
        """P0-3: job_id 必须持久化并在加载时恢复。"""
        dom_src = _module_text("dom.js")
        self.assertIn("JOB_STORE_KEY", dom_src, msg="未持久化 job_id")
        self.assertIn("loadStoredJobIds", dom_src)
        jobs_src = _module_text("jobs.js")
        self.assertIn("restoreQueue", jobs_src, msg="缺少队列恢复逻辑")
        self.assertIn("rememberJobId", jobs_src)

    def test_restore_does_not_require_local_storage(self):
        """服务端才是事实源 —— 本地存储为空时也必须恢复 (P0-3 的另一半)。"""
        code = "\n".join(_code_lines(MODULES / "jobs.js"))
        restore_body = _function_body(code, "async function restoreQueue")
        self.assertNotIn(
            "if (ids.length === 0) return;",
            restore_body,
            msg="恢复逻辑以本地存储为前置条件 ⇒ 换浏览器/清缓存后仍然丢任务",
        )

    def test_finished_states_are_distinguished(self):
        """刷新后 完成 / 失败 / 已取消 三态必须分别显示。"""
        src = _module_text("jobs.js")
        restore_body = src.split("async function restoreQueue", 1)[1].split("\nfunction ", 1)[0]
        for needle in ("job.cancelled", "job.error", "已取消", "失败", "完成"):
            self.assertIn(needle, restore_body, msg=f"恢复逻辑缺 {needle} 分支")

    def test_cancel_button_hidden_after_terminal_state(self):
        """终态后不该再有「终止」按钮 —— 点了只会得到「取消失败」。"""
        src = _module_text("jobs.js")
        self.assertIn(
            'cancelBtn.style.display = "none"',
            src,
            msg="终态后未隐藏终止按钮",
        )


class TestAuthHintPresent(unittest.TestCase):
    """P2-6: 页面必须提示服务器是否需要令牌。"""

    def test_index_html_has_auth_badge(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="auth-badge"', html, msg="index.html 缺鉴权徽标元素")

    def test_gpu_module_reads_auth_enabled(self):
        src = _module_text("gpu.js")
        self.assertIn("auth_enabled", src, msg="未读取 /api/health 的 auth_enabled")
        self.assertIn("renderAuthBadge", src)

    def test_styles_define_badge(self):
        css = (STATIC / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".auth-badge", css, msg="未定义 auth-badge 样式")


class TestWikiErrorMessages(unittest.TestCase):
    """P2-7: Wiki 页的错误提示必须可读且可见。"""

    def test_wiki_maps_status_codes(self):
        src = _module_text("wiki.js")
        self.assertIn("function httpErrorText", src)
        for code in ("401", "403", "404", "429"):
            self.assertIn(f"status === {code}", src, msg=f"未处理 HTTP {code}")

    def test_wiki_delete_guards_null_current(self):
        """``current`` 为 null 时直接 ``current.note.path`` 会抛 TypeError。"""
        code = "\n".join(_code_lines(STATIC / "wiki.js"))
        delete_body = _function_body(code, "async function deleteNote")
        self.assertIn("if (!current)", delete_body, msg="deleteNote 未防御 current 为 null")

    def test_wiki_delete_failure_is_visible(self):
        """删除失败只写 title 属性 ⇒ 用户看不见 —— 必须写进查看区。"""
        code = "\n".join(_code_lines(STATIC / "wiki.js"))
        delete_body = _function_body(code, "async function deleteNote")
        self.assertIn("setViewerMessage", delete_body, msg="删除失败没有可见提示")

    def test_viewer_error_style_defined(self):
        css = (STATIC / "wiki.css").read_text(encoding="utf-8")
        self.assertIn(".viewer-err", css, msg="未定义查看区错误样式")


class TestListJobsEndpoint(unittest.TestCase):
    """P0-3 后端半: ``GET /api/jobs`` 列举任务。"""

    @unittest.skipUnless(_has_fastapi(), "fastapi 未安装")
    def test_lists_jobs(self):
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        app = create_app()
        registry = app.state.jobs
        j1 = registry.create("https://example.com/a")
        j2 = registry.create("https://example.com/b")
        with TestClient(app) as client:
            resp = client.get("/api/jobs")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("jobs", data)
            ids = [j["job_id"] for j in data["jobs"]]
            self.assertIn(j1.job_id, ids)
            self.assertIn(j2.job_id, ids)
            for entry in data["jobs"]:
                for field in ("job_id", "url", "stage", "finished", "cancelled", "error"):
                    self.assertIn(field, entry)

    @unittest.skipUnless(_has_fastapi(), "fastapi 未安装")
    def test_newest_first(self):
        """最新任务排在最前 —— 字典插入序即创建序, 不用时间戳。"""
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        app = create_app()
        registry = app.state.jobs
        older = registry.create("https://example.com/older")
        newer = registry.create("https://example.com/newer")
        with TestClient(app) as client:
            ids = [j["job_id"] for j in client.get("/api/jobs").json()["jobs"]]
            self.assertEqual(ids[0], newer.job_id)
            self.assertIn(older.job_id, ids)

    @unittest.skipUnless(_has_fastapi(), "fastapi 未安装")
    def test_requires_token_when_configured(self):
        """鉴权依赖必须与其它 job 路由一致。"""
        import os

        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        os.environ["MEDIASCRIBE_API_TOKEN"] = "unit-test-token"
        try:
            app = create_app()
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/jobs").status_code, 401)
                ok = client.get(
                    "/api/jobs",
                    headers={"Authorization": "Bearer unit-test-token"},
                )
                self.assertEqual(ok.status_code, 200)
        finally:
            os.environ.pop("MEDIASCRIBE_API_TOKEN", None)

    @unittest.skipUnless(_has_fastapi(), "fastapi 未安装")
    def test_single_job_route_still_works(self):
        """新增路由不能遮蔽 ``GET /api/jobs/{job_id}``。"""
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        app = create_app()
        job = app.state.jobs.create("https://example.com/x")
        with TestClient(app) as client:
            resp = client.get(f"/api/jobs/{job.job_id}")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["job_id"], job.job_id)


class TestWsKeepalive(unittest.TestCase):
    """P0-2: 长静默阶段的 WS 不得被空闲超时掐断。"""

    @unittest.skipUnless(_has_fastapi(), "fastapi 未安装")
    def test_ws_survives_silent_stage_with_pong(self):
        """客户端回 pong ⇒ 连接活过 idle_timeout。

        修复前: idle_timeout=3s 的环境下, 一个不发任何事件的 job 会在
        3s 后被服务端主动关闭 (实测 WebSocketDisconnect)。
        修复后: 客户端每 2s 回一个 pong, 连接必须活过 8s。
        """
        import os
        import time

        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        os.environ["MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS"] = "3"
        os.environ["MEDIASCRIBE_WS_MAX_SESSION_SECONDS"] = "600"
        try:
            app = create_app()
            job = app.state.jobs.create("https://example.com/long")
            with TestClient(app) as client:
                with client.websocket_connect(f"/ws/progress/{job.job_id}") as ws:
                    start = time.monotonic()
                    pongs = 0
                    elapsed = 0.0
                    try:
                        while elapsed < 8.0:
                            msg = ws.receive_json()
                            if msg.get("event") == "ping":
                                pongs += 1
                                ws.send_json({"event": "pong"})
                            elapsed = time.monotonic() - start
                    except Exception as exc:  # pragma: no cover - 失败路径
                        self.fail(f"WS 在 {elapsed:.1f}s 被掐断: {type(exc).__name__}")
                    self.assertGreaterEqual(elapsed, 8.0, "WS 未活过 idle_timeout")
                    self.assertGreaterEqual(pongs, 2, "未收到足够 ping 以触发保活")
        finally:
            os.environ.pop("MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS", None)
            os.environ.pop("MEDIASCRIBE_WS_MAX_SESSION_SECONDS", None)

    @unittest.skipUnless(_has_fastapi(), "fastapi 未安装")
    def test_ping_carries_job_snapshot(self):
        """ping 里带 job 快照 —— 客户端重连后靠它补状态。"""
        import os

        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        os.environ["MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS"] = "1"
        try:
            app = create_app()
            job = app.state.jobs.create("https://example.com/snap")
            with TestClient(app) as client:
                with client.websocket_connect(f"/ws/progress/{job.job_id}") as ws:
                    for _ in range(6):
                        msg = ws.receive_json()
                        if msg.get("event") == "ping":
                            self.assertIn("job_id", msg, "ping 未携带 job 快照")
                            self.assertIn("stage", msg, "ping 未携带 stage")
                            break
                    else:
                        self.fail("未收到 ping")
        finally:
            os.environ.pop("MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS", None)


class TestBackendNoHardcodedChinese(unittest.TestCase):
    """本仓硬规则: 用户可见字符串必须走 ``t()`` (AGENTS.md §三)。"""

    def test_new_route_has_no_cjk_literals(self):
        """新增的 GET /api/jobs 不应引入硬编码中文。"""
        path = ROOT / "mediascribe" / "web" / "route_jobs.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fname = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if fname == "print":
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            if any("\u4e00" <= ch <= "\u9fff" for ch in arg.value):
                                offenders.append(f"print @ line {node.lineno}")
            if isinstance(node, ast.Raise) and node.exc is not None:
                for sub in ast.walk(node.exc):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        if any("\u4e00" <= ch <= "\u9fff" for ch in sub.value):
                            offenders.append(f"raise @ line {node.lineno}")
        self.assertEqual(offenders, [], msg="route_jobs.py 出现硬编码中文: " + ", ".join(offenders))


class TestBlindReviewFixes(unittest.TestCase):
    """独立盲审（2026-10-31，无头 Chromium 实测）发现的 7 条缺陷的回归防线。

    这些问题比第一轮审查更隐蔽 —— 它们只在**真实浏览器 + 真实后端**的
    时序下才显现，静态审读与 node 垫片都测不到：

    * P0-1 刷新后「已完成」任务的文稿被渲染 3 份，下载按钮一次点击触发
      3 次下载（服务端对已结束任务发两次 snapshot + restoreQueue 自己一次）。
    * P0-2 客户端把「结果尚未落盘」断言成「文稿内容为空(可能视频没有语音
      内容)」—— 事实错误，且不会自愈，用户据此以为没转写成功只能重跑。
    * P1-3 未知 job ⇒ 服务端发 error 事件并 4404 关闭 ⇒ 客户端忽略它 ⇒
      每秒无限重连（退避计数被 error 消息清零）。
    * P1-4 提交路径与 restoreQueue 竞态 ⇒ 同一 job 渲染两张卡。
    * P2-5 服务端空闲超时因「发 ping 就刷新 last_activity」而成死代码。
    * P2-6 鉴权徽标在探测失败时谎报「本机免令牌」。
    * P2-7 wiki 删除失败时 replaceChildren 抹掉正在看的笔记正文。
    """

    def _code(self, name: str) -> str:
        return "\n".join(_code_lines(_module_path(name)))

    def _body(self, name: str, signature: str) -> str:
        return _function_body(self._code(name), signature)

    # ---------------------------------------------------------- P0-1
    def test_finished_job_does_not_open_websocket(self):
        """终态任务不建 WS —— 否则服务端两次 snapshot ⇒ 文稿渲染两份。"""
        src = self._code("jobs.js")
        self.assertIn("skipWs", src, msg="缺少 skipWs 选项")
        render_body = self._body("jobs.js", "function renderJobCard")
        self.assertIn("skipWs", render_body, msg="renderJobCard 未按 skipWs 跳过建 WS")

    def test_fetch_result_is_idempotent(self):
        """fetchResult 必须幂等 —— 同一张卡只渲染一次文稿。"""
        body = self._body("jobs.js", "async function fetchResult")
        self.assertIn("__resultRendered", body, msg="fetchResult 无防重入守卫")

    # ---------------------------------------------------------- P0-2
    def test_result_fetch_retries(self):
        """结果未落盘时要重试, 不能一次就下结论。"""
        body = self._body("jobs.js", "async function fetchResult")
        self.assertIn("RETRY_DELAYS", body, msg="fetchResult 无退避重试")
        self.assertIn("await new Promise", body, msg="fetchResult 重试未真正等待")

    def test_no_false_empty_claim(self):
        """不得断言「文稿内容为空」—— 结果可能只是还没落盘。"""
        body = self._body("jobs.js", "async function fetchResult")
        self.assertNotIn("可能视频没有语音内容", body)
        self.assertIn("结果尚未就绪", body, msg="缺少「结果尚未就绪」文案")

    # ---------------------------------------------------------- P1-3
    def test_handles_server_error_event(self):
        """服务端 error 事件必须被处理 —— 否则每秒无限重连。"""
        src = self._code("jobs.js")
        self.assertIn('evt === "error"', src, msg="未处理服务端的 error 事件")

    def test_handles_fatal_close_codes(self):
        """4404/1008/1011 表示「别再连了」—— 必须终止重连。"""
        src = self._code("jobs.js")
        for code in ("4404", "1008", "1011"):
            self.assertIn(code, src, msg=f"未处理致命 close code {code}")

    def test_error_does_not_reset_backoff(self):
        """退避计数只在**进度事件**上归零 —— error 消息不能归零。"""
        src = self._code("jobs.js")
        # markConnected 的调用点必须落在 stage_* 事件分支里。
        self.assertIn("markConnected();", src)
        after_stage = src.split("markConnected();", 1)[1][:120]
        self.assertNotIn(
            "let msg",
            after_stage,
            msg="markConnected 仍在消息解析之前被调用 ⇒ error 消息也会清零退避",
        )

    # ---------------------------------------------------------- P1-4
    def test_render_job_card_deduplicates(self):
        """renderJobCard 必须按 job_id 去重。"""
        render_body = self._body("jobs.js", "function renderJobCard")
        self.assertIn("cardExists(job.job_id)", render_body, msg="renderJobCard 未做去重")

    def test_card_exists_returns_element_not_boolean(self):
        """cardExists 必须返回**元素** —— 返回 Boolean 会让调用方崩。"""
        body = self._body("jobs.js", "function cardExists")
        self.assertNotIn(
            "return Boolean(",
            body,
            msg="cardExists 返回布尔值 ⇒ renderJobCard 会把 true 当成卡片返回",
        )

    # ---------------------------------------------------------- P2-5
    def test_server_does_not_refresh_idle_on_ping(self):
        """服务端发 ping 时**不得**刷新 last_activity。

        「发出 ping」不代表对端活着（浏览器断网但 TCP 未断时 ping 会写进
        缓冲区而不报错）⇒ 只有**收到 pong** 才能证明连接可用。
        """
        path = ROOT / "mediascribe" / "web" / "route_ws.py"
        src = path.read_text(encoding="utf-8")
        ping_branch = src.split('{"event": "ping"', 1)
        self.assertGreaterEqual(len(ping_branch), 2, msg="未找到 ping 发送分支")
        before = ping_branch[0].rsplit("if not done:", 1)[-1]
        self.assertNotIn(
            "last_activity = loop.time()",
            before,
            msg="发 ping 时刷新了 last_activity ⇒ 空闲超时成为死代码",
        )

    def test_server_refreshes_idle_on_client_message(self):
        """收到客户端消息（含 pong）必须刷新 last_activity。"""
        path = ROOT / "mediascribe" / "web" / "route_ws.py"
        src = path.read_text(encoding="utf-8")
        client_branch = src.split("if client_task in done:", 1)
        self.assertGreaterEqual(len(client_branch), 2, msg="未找到 client_task 分支")
        self.assertIn(
            "last_activity = loop.time()",
            client_branch[1][:400],
            msg="收到客户端消息时未刷新 last_activity ⇒ 长任务仍会被掐断",
        )

    # ---------------------------------------------------------- P2-6
    def test_auth_badge_has_unknown_state(self):
        """探测失败时不得谎报「免令牌」。"""
        src = self._code("gpu.js")
        self.assertIn("unknown", src, msg="鉴权徽标缺少「未知」状态")
        self.assertIn("reachable", src, msg="renderAuthBadge 未区分可达性")

    # ---------------------------------------------------------- P2-7
    def test_wiki_error_does_not_replace_viewer(self):
        """删除失败不得抹掉正在看的笔记正文。"""
        src = self._code("wiki.js")
        body = self._body("wiki.js", "function setViewerMessage")
        self.assertIn("appendChild", body, msg="错误提示未追加, 会覆盖正文")
        self.assertIn(
            'viewer.querySelector("pre")',
            body,
            msg="未判断查看区是否已有正文",
        )


class TestNavigationAndButtonFeedback(unittest.TestCase):
    """页面跳转路径与按钮状态反馈（用户明确要求审的两项）。

    2026-10-31 第一轮审查漏掉了「页面跳转路径」，第二轮补上：

    * ``/extension`` 原本是**孤岛** —— 首页 footer 用 ``target="_blank"``
      打开它, 页面里却没有任何返回工作台的入口。
    * 「Install guide (markdown)」的文案与行为不符 —— 该端点默认返回
      **HTML**（浏览器可读版）, 不是 markdown 文件。
    * 「清空已结束」永远可点, 但没有可清内容时点了**毫无反应**。
    """

    @unittest.skipUnless(_has_fastapi(), "fastapi 未安装")
    def test_extension_page_has_back_link(self):
        """扩展安装页必须能回到工作台。"""
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        with TestClient(create_app()) as client:
            body = client.get("/extension").text
            self.assertIn('href="/"', body, msg="/extension 缺少返回工作台的链接")

    @unittest.skipUnless(_has_fastapi(), "fastapi 未安装")
    def test_extension_page_offers_raw_markdown(self):
        """既要网页版安装指南, 也要真 markdown 文件（文案不能说谎）。"""
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        with TestClient(create_app()) as client:
            body = client.get("/extension").text
            self.assertIn("/api/extension/install.md", body, msg="缺少安装指南链接")
            self.assertIn(
                "/api/extension/install.md?raw=1",
                body,
                msg="缺少真 markdown 文件的链接 —— 文案写 markdown 但默认返回 HTML",
            )

    @unittest.skipUnless(_has_fastapi(), "fastapi 未安装")
    def test_extension_page_language_matches_site(self):
        """站点其余部分是 zh-CN —— 扩展页不该是孤立的英文页。"""
        from fastapi.testclient import TestClient

        from mediascribe.web.app import create_app

        with TestClient(create_app()) as client:
            body = client.get("/extension").text
            self.assertIn('lang="zh-CN"', body, msg="/extension 的 lang 与主站不一致")

    def test_clear_done_button_has_disabled_state(self):
        """「清空已结束」在无内容时可清时必须禁用并说明数量。"""
        code = "\n".join(_code_lines(MODULES / "jobs.js"))
        self.assertIn("refreshClearButton", code, msg="缺少清空按钮状态刷新逻辑")
        body = _function_body(code, "function refreshClearButton")
        self.assertIn("btn.disabled", body, msg="清空按钮未按可清数量禁用")
        self.assertIn("finishedCards().length", body, msg="未按实际数量计算")
        # 终态时必须刷新 —— 否则任务跑完后按钮仍是禁用的。
        self.assertIn("refreshClearButton();", code)

    def test_clear_button_disabled_style_defined(self):
        css = (STATIC / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".ghost:disabled", css, msg="未定义禁用态样式")

    def test_heartbeat_stopped_on_every_terminal_path(self):
        """三个终态路径都必须停心跳 —— 不能依赖对端善后。

        2026-10-31 实测: node 模拟脚本因心跳的 setInterval 永不退出而挂住
        (Node 里 pending 的 setInterval 会保持事件循环存活)。浏览器里虽然
        不致命, 但那意味着每个已完成 job 仍留着一个每 20s 发一次 pong 的
        定时器 —— 终态是确定的, 不该依赖服务端随后那次关闭是否送达。
        """
        code = "\n".join(_code_lines(MODULES / "jobs.js"))
        for sig in ('evt === "succeeded"', 'evt === "failed"', 'evt === "cancelled"'):
            self.assertIn(sig, code, msg=f"源码里找不到 {sig}")
        # succeeded 分支内必须停心跳。
        succeeded_branch = code.split('evt === "succeeded"', 1)[1].split("else if", 1)[0]
        self.assertIn(
            "stopHeartbeat();",
            succeeded_branch,
            msg="succeeded 分支未停心跳 ⇒ 每个已完成 job 留一个定时器",
        )
        # 重连后发现终态的那条路径也要停。
        apply_body = _function_body(code, "function applyFinished")
        self.assertIn("stopHeartbeat();", apply_body, msg="applyFinished 未停心跳")


if __name__ == "__main__":
    unittest.main()
