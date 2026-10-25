"""可选依赖契约：fastapi / pydantic 缺失时, Web 层仍要能导入。

背景
----
2026-10-25 的职责拆分把 ``mediascribe/web/app.py`` 拆成 20 个模块, 独立盲审
实测发现：其中 6 个 route 模块顶层 ``from fastapi import …``、``app.py`` 的
子模块导入也在 try/except 之外 ⇒ **fastapi 缺失时 ``import
mediascribe.web.app`` 直接 ImportError**，模块 docstring 承诺的

    ``create_app()`` → ``RuntimeError("FastAPI is not installed…")``

根本走不到（那个分支成了死代码）。同类问题在 ``security.py`` 更隐蔽：兜底把
``Header`` 置 ``None``，而 ``Header(default=None)`` 出现在**默认参数**位置、
函数定义时求值 ⇒ 兜底自身就抛 ``TypeError``。

本文件把这条契约变成可回归的断言：在**子进程**里屏蔽 fastapi / pydantic /
starlette，逐个 import ``mediascribe/web/`` 下的模块。
（用子进程而非同进程 —— 屏蔽 import 的探针会污染同进程里已导入的模块状态。）

用法::

    python -m pytest tests/test_web_optional_deps.py -q
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent

#: 需要逐个 import 的模块(20 个 = 12 辅助 + 7 路由 + app)
MODULES = [
    "mediascribe.web.logging_setup",
    "mediascribe.web.public_url",
    "mediascribe.web.cors",
    "mediascribe.web.static_assets",
    "mediascribe.web.validation",
    "mediascribe.web.security",
    "mediascribe.web.ratelimit",
    "mediascribe.web.gpu_health",
    "mediascribe.web.job_runner",
    "mediascribe.web.ws_bridge",
    "mediascribe.web.models",
    "mediascribe.web.pipeline_bridge",
    "mediascribe.web.route_extension",
    "mediascribe.web.route_health",
    "mediascribe.web.route_jobs",
    "mediascribe.web.route_pages",
    "mediascribe.web.route_transcribe",
    "mediascribe.web.route_wiki",
    "mediascribe.web.route_ws",
    "mediascribe.web.extension_builder",
    "mediascribe.web.assembly",
    "mediascribe.web.app",
]

_PROBE = textwrap.dedent(
    """
    import builtins, importlib, sys

    BLOCKED = {"fastapi", "pydantic", "starlette"}
    _real_import = builtins.__import__

    def _guard(name, *args, **kwargs):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("blocked for probe: " + name)
        return _real_import(name, *args, **kwargs)

    builtins.__import__ = _guard

    modules = __MODULES__
    failed = []
    for mod in modules:
        try:
            importlib.import_module(mod)
        except Exception as exc:
            failed.append((mod, type(exc).__name__ + ": " + str(exc)))

    for mod, err in failed:
        print("FAIL " + mod + "  " + err)

    # app 模块可导入时, create_app() 必须抛设计好的 RuntimeError
    if "mediascribe.web.app" not in dict(failed):
        app = importlib.import_module("mediascribe.web.app")
        try:
            app.create_app()
        except RuntimeError as exc:
            print("RUNTIME_ERROR_OK " + str(exc))
        except Exception as exc:
            print("BAD_EXCEPTION " + type(exc).__name__ + ": " + str(exc))
            failed.append(("create_app", "bad exception type"))
        else:
            print("NO_EXCEPTION create_app() 没有抛异常")
            failed.append(("create_app", "no exception"))

    sys.exit(1 if failed else 0)
    """
)


class TestOptionalDependencyContract(unittest.TestCase):
    """fastapi / pydantic 是**可选**依赖 —— 缺失时 Web 层要能优雅降级。"""

    def test_web_modules_import_without_fastapi(self):
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE.replace("__MODULES__", repr(MODULES))],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=(
                "fastapi/pydantic 缺失时以下模块导入失败（可选依赖契约未达成）:\n"
                f"{proc.stdout}\n{proc.stderr[-1500:]}"
            ),
        )
        self.assertIn("RUNTIME_ERROR_OK", proc.stdout)

    def test_probe_covers_every_web_module(self):
        """探针本身要覆盖全部模块 —— 漏一个模块, 上面的断言就失去意义。"""
        on_disk = {
            f"mediascribe.web.{p.stem}"
            for p in (ROOT / "mediascribe" / "web").glob("*.py")
            if p.stem not in ("__init__",)
        }
        missing = on_disk - set(MODULES)
        self.assertFalse(
            missing,
            msg=f"这些 web 模块没被可选依赖探针覆盖: {sorted(missing)}",
        )


if __name__ == "__main__":
    unittest.main()
