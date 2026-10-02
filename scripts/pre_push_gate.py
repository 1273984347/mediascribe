#!/usr/bin/env python3
"""Pre-push 门禁 — 在 push 前本地复现 CI 的第一道关卡 (workflow-optimization O1)。

跑两件事, 任何一件失败即非零退出:
  1. ruff check .  +  ruff format --check .   (与 CI lint job 同参数)
  2. pytest 快速套件                           (与 CI unit job 同选择:
     -m "not integration and not network" --ignore=tests/test_e2e_real_urls.py)

用法:
    python scripts/pre_push_gate.py            # 全部门禁
    python scripts/pre_push_gate.py --no-lint  # 只跑测试
    python scripts/pre_push_gate.py --no-test  # 只跑 lint

接入 git(二选一):
    just push                 # gate 通过后自动 git push
    # 或把钩子装进本仓库克隆(不入库):
    #   printf '#!/bin/sh\npython scripts/pre_push_gate.py\n' > .git/hooks/pre-push && chmod +x .git/hooks/pre-push

动机: 基线数据显示 CI 首推通过率仅 71.4%(2026-09 复盘), 大头是 lint
与可本地复现的测试失败 — 推之前先在本机跑一遍, 把反馈环从分钟级
(CI 排队)压到秒级。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 与 .github/workflows/test.yml unit job 保持一致
PYTEST_ARGS = [
    "-m",
    "not integration and not network",
    "--ignore=tests/test_e2e_real_urls.py",
    "-q",
    "--tb=short",
    "-x",
]


def _run(label: str, args: list[str]) -> bool:
    print(f"\n=== {label} ===")
    print("$ " + " ".join(args))
    proc = subprocess.run(args, cwd=str(ROOT))
    if proc.returncode != 0:
        print(f"\n❌ {label} 失败 (exit {proc.returncode}) — 修复后再 push")
        return False
    print(f"✅ {label} 通过")
    return True


def main(argv: list[str]) -> int:
    do_lint = "--no-lint" not in argv
    do_test = "--no-test" not in argv
    if not do_lint and not do_test:
        print("nothing to do: --no-lint and --no-test are mutually exclusive")
        return 2

    ok = True
    if do_lint:
        ok &= _run("ruff check", [sys.executable, "-m", "ruff", "check", "."])
        ok &= _run(
            "ruff format --check",
            [sys.executable, "-m", "ruff", "format", "--check", "."],
        )
    if do_test:
        ok &= _run(
            "pytest (unit selection)",
            [sys.executable, "-m", "pytest", *PYTEST_ARGS],
        )

    if ok:
        print("\n🟢 门禁全部通过 — 可以 push")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
