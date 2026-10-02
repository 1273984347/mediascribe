"""mediascribe.downloaders.base 的测试 —— 迁入自 tests/test_coverage_gaps_2.py(按被测模块归位)。"""

from __future__ import annotations

import pytest

###########################################################################
# 迁入自 tests/test_coverage_gaps_2.py —— TestDownloaderBase(mediascribe.downloaders.base)
###########################################################################


class TestDownloaderBase:
    def test_base_abstract_cannot_be_instantiated(self):
        from mediascribe.downloaders.base import Downloader

        with pytest.raises(TypeError):
            Downloader()  # abstract: must override ``download``.
