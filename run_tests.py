#!/usr/bin/env python3
"""运行单元测试 — pytest 垫片 (与 CI 同参数)。

历史: 本入口曾用 unittest discover, 与 CI 的 pytest 双轨漂移
(2026-10-03 审查统一)。现在只是 pytest 的转发, 参数与
.github/workflows/test.yml 的 unit job 一致; 额外参数原样透传:

    python run_tests.py                  # CI 同选择
    python run_tests.py tests/test_learn.py   # 透传给 pytest
"""

import sys

# 与 CI unit job 相同的选择参数(放在用户参数之前, 可被用户覆盖)
CI_SELECTION = [
    "-m",
    "not integration and not network",
    "--ignore=tests/test_e2e_real_urls.py",
]

if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([*CI_SELECTION, "-q", *sys.argv[1:]]))
