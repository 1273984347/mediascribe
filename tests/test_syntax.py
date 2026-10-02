"""Syntax check: every .py file in the project must parse.

此前是模块级脚本(import 即全仓扫描, 见 2026-10-03 审查收敛)。
"""

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def test_all_project_files_parse():
    errors = []
    total = 0
    for py_file in PROJECT_ROOT.rglob("*.py"):
        # Skip venvs / site-packages / output / cache
        skip = any(
            part in py_file.parts
            for part in ("venv", "env", ".venv", "output", "cache", "__pycache__", "site-packages")
        )
        if skip:
            continue
        total += 1
        try:
            with open(py_file, "r", encoding="utf-8") as f:
                ast.parse(f.read(), filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError) as e:
            errors.append((py_file, e))

    assert not errors, f"{len(errors)}/{total} files have syntax errors: " + "; ".join(
        f"{p.relative_to(PROJECT_ROOT)}: {err}" for p, err in errors
    )
    # sanity: 确实扫到了足量的文件, 避免 rglob 因路径变动静默扫空
    assert total > 50, f"suspiciously few .py files scanned: {total}"
