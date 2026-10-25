"""把 Web UI 打包成**单文件**离线预览 HTML（双击即可打开）。

背景
----
``mediascribe/web/static/index.html`` 是**服务端渲染的模板**，不是能双击打开的
独立网页：资源用绝对路径 ``/static/...``、前端是原生 ES modules、版本号与
缓存版本由后端注入占位符。``file://`` 直接打开会得到「无样式 + 模块不执行
+ 占位符裸露」的坏页面（实测过）。

本脚本把前端源码**收敛成一个自包含的 HTML 文件**：
  * ``styles.css`` 内联进 ``<style>``
  * ``modules/*.js`` 的 ES module 图转成 IIFE 闭包序列后内联进 ``<script>``
  * ``{{APP_VERSION}}`` / ``{{STATIC_VERSION}}`` 占位符替换为真实值
  * 注入「离线预览适配层」：``file://`` 下拦截 fetch / WebSocket，给出可读
    提示而不是让人一头雾水的网络错误

边界（重要）
------------
单文件版**只有界面**。提交转写、WebSocket 实时进度、结果取回都依赖后端
API（``/api/jobs``、``/ws/progress/*``），``file://`` 下没有服务端 ⇒ 不可用。
要做真跑转写，请用服务端入口：``python -m mediascribe.web.app``。

用法
----
    python scripts/tools/build_standalone_web.py
    python scripts/tools/build_standalone_web.py --out D:\\tmp\\preview.html
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "mediascribe" / "web" / "static"

#: 模块求值顺序 = 依赖拓扑序（dom 无依赖，其余依赖 dom，jobs 依赖多模块）
MODULE_ORDER = ["dom", "theme", "status", "gpu", "auth", "jobs"]

#: 匹配 ``import { a, b } from './x.js';``（相对路径的单引号/双引号都吃）
_IMPORT_NAMED = re.compile(
    r"^import\s*\{(?P<names>[^}]*)\}\s*from\s*['\"]\./(?P<mod>\w+)\.js['\"]\s*;?\s*$",
    re.MULTILINE,
)
#: 匹配 ``import './x.js';``（仅副作用）
_IMPORT_BARE = re.compile(r"^import\s*['\"]\./(?P<mod>\w+)\.js['\"]\s*;?\s*$", re.MULTILINE)
#: 匹配文件末尾的 ``export { a, b };``
_EXPORT = re.compile(r"^export\s*\{(?P<names>[^}]*)\}\s*;?\s*$", re.MULTILINE)

#: 离线性名残留检查（转换后源码里不应再有 import/export 语句）
_LEFTOVER_MODULE_SYNTAX = re.compile(r"^\s*(import|export)\s", re.MULTILINE)


class BuildError(RuntimeError):
    """构建期错误 —— 宁可构建失败，也不要产出一个半坏的 HTML。"""


def _read_version() -> str:
    """从单一真源取版本号（mediascribe/_version.py），不重复硬编码。"""
    version_file = ROOT / "mediascribe" / "_version.py"
    if not version_file.is_file():
        raise BuildError(f"找不到版本真源: {version_file}")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', version_file.read_text(encoding="utf-8"))
    if not match:
        raise BuildError(f"{version_file} 里解析不到 __version__")
    return match.group(1)


def _static_version() -> str:
    """静态资源缓存版本（mtime 毫秒）—— 与后端 ``_static_asset_version()`` 同口径。"""
    newest = 0.0
    for path in STATIC.rglob("*"):
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
    return str(int(newest * 1000))


def _transform_module(name: str) -> str:
    """把一个 ES module 转成 IIFE 闭包。

    ``import { a } from './x.js'`` → ``const { a } = __mod_x;``
    ``import './x.js'``            → ``__mod_x;``
    ``export { a, b }``            → ``return { a, b };``
    """
    path = STATIC / "modules" / f"{name}.js"
    if not path.is_file():
        raise BuildError(f"缺少模块文件: {path}")
    code = path.read_text(encoding="utf-8")

    def _named(match: re.Match[str]) -> str:
        return f"const {{{match.group('names')}}} = __mod_{match.group('mod')};"

    def _bare(match: re.Match[str]) -> str:
        return f"__mod_{match.group('mod')};"

    def _export(match: re.Match[str]) -> str:
        return f"return {{{match.group('names')}}};"

    code = _IMPORT_NAMED.sub(_named, code)
    code = _IMPORT_BARE.sub(_bare, code)
    code = _EXPORT.sub(_export, code)

    leftover = _LEFTOVER_MODULE_SYNTAX.findall(code)
    if leftover:
        raise BuildError(
            f"模块 {name}.js 转换后仍有 import/export 语句 {leftover} —— "
            "转换规则没覆盖住，请补规则（不要产出半坏的文件）"
        )
    return f"const __mod_{name} = (function () {{\n{code}\n}})();"


def _build_js() -> str:
    """把 app.js 的入口图展开成按依赖序排列的 IIFE 序列。"""
    parts: list[str] = []
    for name in MODULE_ORDER:
        parts.append(_transform_module(name))
    return "\n\n".join(parts)


#: 离线预览适配层 —— file:// 下拦截网络调用，给出可读提示。
_STANDALONE_ADAPTER = """
// ---- 离线预览适配层（standalone build 注入） ----
// file:// 下没有后端：把网络调用拦下来并给出可读提示，
// 而不是让用户看到 "Failed to fetch" / "WebSocket connection failed"。
(function () {
  if (location.protocol !== "file:") return;
  var TIP = "离线预览版：界面可交互，提交转写需启动服务端（python -m mediascribe.web.app）";
  var origFetch = window.fetch;
  window.fetch = function (input) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    if (/^(https?:)?\\/\\//.test(url) || url.charAt(0) === "/") {
      return Promise.reject(new Error(TIP));
    }
    return origFetch.apply(this, arguments);
  };
  var OrigWS = window.WebSocket;
  function StandaloneWS() { throw new Error(TIP); }
  StandaloneWS.prototype = OrigWS.prototype;
  window.WebSocket = StandaloneWS;
})();
""".strip()

#: 页面顶部的离线预览横幅（样式在下面追加）
_BANNER_HTML = (
    '<div class="standalone-banner">离线预览版 · 界面可交互；提交转写 / 实时进度需启动服务端</div>'
)

_BANNER_CSS = """
/* ---- 离线预览横幅（standalone build 注入） ---- */
.standalone-banner {
  max-width: 880px;
  margin: 16px auto 0;
  padding: 9px 16px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--fill);
  color: var(--fg-2);
  font-size: 12.5px;
  text-align: center;
}
""".strip()


def build(out_path: Path) -> Path:
    """生成单文件 HTML，返回输出路径。"""
    index_html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    js = _build_js()
    version = _read_version()
    static_version = _static_version()

    # 1) CSS：<link> → 内联 <style>
    link_pattern = re.compile(
        r'<link rel="stylesheet" href="/static/styles\.css\?v=\{\{STATIC_VERSION\}\}">'
    )
    html, n_link = link_pattern.subn(
        lambda _: f"<style>\n{css}\n{_BANNER_CSS}\n</style>", index_html
    )
    if n_link != 1:
        raise BuildError(f"styles.css 的 <link> 匹配到 {n_link} 处，预期 1 处")

    # 2) JS：<script type="module" src=...> → 内联 <script>
    script_pattern = re.compile(
        r'<script type="module" src="/static/app\.js\?v=\{\{STATIC_VERSION\}\}"></script>'
    )
    html, n_script = script_pattern.subn(
        lambda _: f"<script>\n{_STANDALONE_ADAPTER}\n\n{js}\n</script>", html
    )
    if n_script != 1:
        raise BuildError(f"app.js 的 <script> 匹配到 {n_script} 处，预期 1 处")

    # 3) 占位符 → 真实值
    html = html.replace("{{APP_VERSION}}", version)
    html = html.replace("{{STATIC_VERSION}}", static_version)
    if "{{" in html or "}}" in html:
        left = re.findall(r"\{\{[A-Z_]+\}\}", html)
        raise BuildError(f"仍有未替换的占位符: {left}")

    # 4) 注入离线预览横幅（放在 <header> 之后）
    html, n_banner = re.subn(r"(</header>)", r"\1\n  " + _BANNER_HTML, html, count=1)
    if n_banner != 1:
        raise BuildError("找不到 </header>，横幅未注入")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "output" / "web-standalone" / "mediascribe-web-standalone.html",
        help="输出路径（默认 output/web-standalone/mediascribe-web-standalone.html）",
    )
    args = parser.parse_args(argv)

    try:
        out = build(args.out)
    except BuildError as exc:
        print(f"构建失败: {exc}", file=sys.stderr)
        return 1

    size = out.stat().st_size
    print(f"✅ 单文件 HTML 已生成: {out}")
    print(f"   大小 {size / 1024:.1f} KB · 双击即可在浏览器打开")
    print("   注意: 仅界面可用, 提交转写需启动服务端 (python -m mediascribe.web.app)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
