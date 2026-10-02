"""mediascribe.__main__ 的测试 —— 迁入自 tests/test_coverage_gaps_2.py(按被测模块归位)。"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

###########################################################################
# 迁入自 tests/test_coverage_gaps_2.py —— TestMain(mediascribe.__main__)
###########################################################################


class TestMain:
    def test_main_module_help(self, capsys):
        from mediascribe.__main__ import main

        with mock.patch.object(sys, "argv", ["mediascribe", "--help"]):
            with pytest.raises(SystemExit) as excinfo:
                main()
        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        # help text mentions the two main subcommands
        assert "transcribe" in captured.out or "transcribe" in captured.err
