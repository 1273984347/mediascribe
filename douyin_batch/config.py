"""转发壳 — BatchConfig 已收编为 :mod:`mediascribe.batch_config`(2026-10-03)。

douyin_batch 包降级为批处理编排层, 平行实现的 cache/config/progress
统一收进 mediascribe 包; 本壳仅为兼容旧 import 路径而保留,
下一版本计划删除。
"""

from mediascribe.batch_config import BatchConfig  # noqa: F401

__all__ = ["BatchConfig"]
