#!/usr/bin/env python3
"""
运行所有单元测试
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# 运行测试
import unittest

# 自动发现测试
loader = unittest.TestLoader()
suite = loader.discover(
    start_dir=str(Path(__file__).parent / "douyin_batch" / "tests"),
    pattern="test_*.py",
    top_level_dir=str(Path(__file__).parent),
)

runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)

sys.exit(0 if result.wasSuccessful() else 1)
