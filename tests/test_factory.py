"""mediascribe.transcribers.factory 的测试 —— 迁入自 tests/test_coverage_gaps_2.py(按被测模块归位)。

TestFactoryAndChunked 跨 factory/chunked 两个模块, 按主要 import 归入本文件。"""

from __future__ import annotations

import pytest

###########################################################################
# 迁入自 tests/test_coverage_gaps_2.py —— TestFactoryAndChunked(mediascribe.transcribers.factory)
###########################################################################


class TestFactoryAndChunked:
    """主要覆盖 mediascribe.transcribers.factory;末尾的
    test_chunked_probe_duration_unsupported_path_raises 跨模块覆盖
    mediascribe.transcribers.chunked —— 按主要 import 归入 factory 的测试文件。"""

    def test_factory_creates_known_engines(self):
        from mediascribe.transcribers.factory import get_transcriber

        # The default is whisperx.  We just assert the factory returns
        # a non-None instance and that the requested model is honoured.
        t = get_transcriber(name="whisper", model="tiny")
        assert t is not None
        assert t.model_name == "tiny"

    def test_factory_unknown_engine_raises(self):
        from mediascribe.transcribers.factory import get_transcriber

        with pytest.raises(ValueError):
            get_transcriber(name="definitely-not-an-engine", model="tiny")

    def test_factory_fallback_chain(self):
        from mediascribe.transcribers.factory import (
            DEFAULT_FALLBACK_CHAIN,
            get_transcriber_with_fallback,
        )

        assert isinstance(DEFAULT_FALLBACK_CHAIN, tuple)
        # Asking for an unsupported engine falls back through the chain.
        t = get_transcriber_with_fallback(preferred="whisper", model="tiny")
        assert t is not None

    def test_chunked_probe_duration_unsupported_path_raises(self, tmp_path):
        from mediascribe.transcribers.chunked import probe_duration

        # ``probe_duration`` falls back to ffprobe for non-WAV inputs.
        # We don't have ffprobe in the test env, so the function must
        # raise a clean RuntimeError instead of crashing with an
        # OSError from subprocess.
        mp3 = tmp_path / "x.mp3"
        mp3.write_bytes(b"junk")
        with pytest.raises(RuntimeError):
            probe_duration(mp3)
