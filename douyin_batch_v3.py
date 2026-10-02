#!/usr/bin/env python3
"""弃用壳 — 本脚本已收编为 ``mediascribe.batch_cli``（2026-10-03）。

根级散文件曾是 entry point / py-modules / 动态加载三层耦合的根源；
现在保留本壳仅为兼容旧文档里的 ``python douyin_batch_v3.py ...`` 用法，
下一版本将删除。请改用:

    mediascribe-batch ...            # pip 安装后的 entry point
    python -m mediascribe archive ...  # 等价子命令
"""

import sys
import warnings

warnings.warn(
    "douyin_batch_v3.py 已弃用 — 请改用 `mediascribe-batch` 或 "
    "`python -m mediascribe archive`（模块现为 mediascribe.batch_cli）",
    DeprecationWarning,
    stacklevel=2,
)

from mediascribe.batch_cli import build_parser, main  # noqa: E402,F401

if __name__ == "__main__":
    sys.exit(main())
