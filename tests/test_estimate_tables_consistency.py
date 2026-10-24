"""
产品侧与基准侧的**资源估算表一致性**测试。

背景
----
转录资源估算（显存/内存需求）在两处各有一份：

* **产品侧** ``mediascribe/runtime_guard.py`` —— 运行时降档决策用
* **基准侧** ``scripts/bench/asr/bench_asr.py`` —— 基准的降档规划用

两份若各自演进，迟早漂移 ⇒ 会出现「基准说跑得下、产品却降档」这种
**无法解释的差异**。本测试把两者的公共档位锁在一起。

命名约定的差异（测试里做映射）
------------------------------
=============  =====================  ======================
维度           基准侧                  产品侧
=============  =====================  ======================
engine 名      ``faster_whisper``      ``faster-whisper``（CLI 名）
engine 名      ``openai_whisper``      ``whisper``
device/精度    精度（int8/float16…）   设备（cuda/cpu）
=============  =====================  ======================

⇒ 断言的是「**同一档位在两边的估算值必须相等**」。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "bench" / "asr"))

import bench_asr  # noqa: E402

from mediascribe.runtime_guard import (  # noqa: E402
    RAM_ESTIMATE_MB as GUARD_RAM,
)
from mediascribe.runtime_guard import (  # noqa: E402
    VRAM_ESTIMATE_MB as GUARD_VRAM,
)

#: 基准侧 engine 名 → 产品侧 engine 名
ENGINE_ALIAS = {
    "faster_whisper": "faster-whisper",
    "openai_whisper": "whisper",
    "whisperx": "whisperx",
    "whisper_cpp": "whisper-cpp",
}

#: 各引擎在 CUDA 侧的「默认精度」（bench 的 key 里的 precision）
CUDA_PRECISION = {
    "faster_whisper": "int8_float16",
    "openai_whisper": "float16",
}

#: 各引擎在 CPU 侧的「默认精度」
CPU_PRECISION = {
    "faster_whisper": "int8",
    "openai_whisper": "float32",
}


class TestEstimateTablesAgree(unittest.TestCase):
    """公共档位的估算值必须一致。"""

    def _bench_vram(self, engine: str, model: str) -> int | None:
        prec = CUDA_PRECISION.get(engine)
        if prec is None:
            return None
        return bench_asr.VRAM_ESTIMATE_MB.get((engine, model, prec))

    def _bench_ram(self, engine: str, model: str) -> int | None:
        prec = CPU_PRECISION.get(engine)
        if prec is None:
            return None
        return bench_asr.RAM_ESTIMATE_MB.get((engine, model, prec))

    def test_vram_estimates_match(self):
        checked = 0
        for bench_engine, product_engine in ENGINE_ALIAS.items():
            for (p_engine, family, device), value in GUARD_VRAM.items():
                if device != "cuda" or p_engine != product_engine:
                    continue
                bench_value = self._bench_vram(bench_engine, family)
                if bench_value is None:
                    continue
                self.assertEqual(
                    bench_value,
                    value,
                    msg=(
                        f"显存估算不一致：基准 {bench_engine}/{family} = {bench_value} MiB，"
                        f"产品 {product_engine}/{family} = {value} MiB"
                    ),
                )
                checked += 1
        self.assertGreater(checked, 0, msg="没有任何公共档位可比 —— 映射可能失效了")

    def test_ram_estimates_match(self):
        checked = 0
        for bench_engine, product_engine in ENGINE_ALIAS.items():
            for (p_engine, family, device), value in GUARD_RAM.items():
                if device != "cpu" or p_engine != product_engine:
                    continue
                bench_value = self._bench_ram(bench_engine, family)
                if bench_value is None:
                    continue
                self.assertEqual(
                    bench_value,
                    value,
                    msg=(
                        f"内存估算不一致：基准 {bench_engine}/{family} = {bench_value} MiB，"
                        f"产品 {product_engine}/{family} = {value} MiB"
                    ),
                )
                checked += 1
        self.assertGreater(checked, 0, msg="没有任何公共档位可比 —— 映射可能失效了")

    def test_whisper_cpp_only_in_product_side(self):
        """whisper.cpp 的 GGML 内存需求只有产品侧有（基准侧不跑 C++ 引擎）。

        这不是不一致，是**范围差异** —— 明确写出来，避免以后误判为漂移。
        """
        self.assertIn(
            ("whisper-cpp", "tiny", "cpu"),
            GUARD_RAM,
            msg="产品侧应记录 whisper.cpp 的内存需求（实测 291 MiB）",
        )
        self.assertNotIn(
            ("whisper_cpp", "tiny", "int8"),
            bench_asr.RAM_ESTIMATE_MB,
            msg="基准侧不应有 whisper.cpp 条目（它不跑该引擎）",
        )


if __name__ == "__main__":
    unittest.main()
