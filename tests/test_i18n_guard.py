"""用户可见字符串 i18n 门禁（AGENTS.md 硬规则："用户可见字符串必须走 t()"）。

2026-10-29 对抗性审查 P1-4：该规则曾名存实亡（全仓 170 处 CJK print/raise
不走 t()），当日已把审查点名面（__main__ / runtime_guard / whisper_cpp /
model_weights / verify / pipeline 粤语警告 / web validation）全部转换。
本测试用 AST 扫描防止回潮：

* 扫描 ``mediascribe/`` 下所有 ``print(<含中文的字面量/f-string>)`` 与
  ``raise XxxError(<含中文的字面量/f-string>)``；
* 命中点必须落在 :data:`_BASELINE`（存量债清单，**只允许缩小**）里；
* :data:`_EXEMPT_MODULES` 是结构性豁免（i18n 词表本身、LLM prompt 等
  功能性中文，不是 UI 文案）。

给既有基线文件新增 CJK print/raise 会直接失败 —— 要么走 t()，要么先
把该文件从存量债里清掉。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PKG_ROOT = Path(__file__).parent.parent / "mediascribe"

# 结构性豁免：中文是数据/词表/prompt，不是用户可见 UI 文案
_EXEMPT_MODULES = {
    "mediascribe/i18n.py",
}

# 存量债基线（相对 mediascribe/ 的 posix 路径 → 命中数）。
# 2026-10-29 建立时 30 个文件 170 处；审查点名面当日清偿至 12 个文件 45 处。
# 只允许缩小，不允许回潮。
_BASELINE = {
    "audio_utils.py": 4,
    "config.py": 1,
    "cross_check.py": 5,
    "doctor.py": 12,
    "downloaders/douyin.py": 4,
    "downloaders/ytdlp.py": 4,
    "llm_post_process.py": 2,
    "pipeline_stages.py": 4,
    "retry.py": 3,
    "transcribers/faster_whisper.py": 2,
    "transcribers/whisper.py": 3,
    "web/security.py": 1,
}


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def _node_has_cjk(node: ast.expr) -> bool:
    """True when a string literal (or the literal parts of an f-string) contains CJK."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and _has_cjk(node.value)
    if isinstance(node, (ast.JoinedStr, ast.FormattedValue)):
        return any(_node_has_cjk(child) for child in ast.iter_child_nodes(node))
    return False


def _scan_file(path: Path) -> int:
    """Count CJK-bearing print()/raise literals in one file (AST walk)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return 0
    hits = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if fname == "print":
                for arg in node.args:
                    if _node_has_cjk(arg):
                        hits += 1
                        break
        if isinstance(node, ast.Raise) and node.exc is not None:
            if any(_node_has_cjk(sub) for sub in ast.walk(node.exc)):
                hits += 1
    return hits


def _scan_package() -> dict:
    counts: dict = {}
    for path in sorted(PKG_ROOT.rglob("*.py")):
        rel = path.relative_to(PKG_ROOT).as_posix()
        if f"mediascribe/{rel}" in _EXEMPT_MODULES:
            continue
        n = _scan_file(path)
        if n:
            counts[rel] = n
    return counts


class TestUserVisibleStringsGoThroughT(unittest.TestCase):
    def test_no_new_cjk_print_or_raise(self):
        actual = _scan_package()
        expected = dict(_BASELINE)
        new_violations = {k: v for k, v in actual.items() if k not in expected or v > expected[k]}
        self.assertEqual(
            new_violations,
            {},
            "检测到基线之外的 CJK print/raise —— 用户可见字符串必须走 "
            "mediascribe.i18n.t()。若为存量债清偿，请同步缩小 _BASELINE。",
        )

    def test_baseline_entries_still_exist(self):
        """基线里已清偿的文件应从 _BASELINE 删除（防清单腐烂）。"""
        actual = _scan_package()
        stale = [k for k in _BASELINE if k not in actual]
        self.assertEqual(
            stale,
            [],
            f"以下文件已无违规，请把它们从 _BASELINE 中移除: {stale}",
        )


if __name__ == "__main__":
    unittest.main()
