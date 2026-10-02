"""douyin_batch.logger 的测试 —— 迁入自 tests/test_coverage_gaps_2.py(按被测模块归位)。"""

from __future__ import annotations

###########################################################################
# 迁入自 tests/test_coverage_gaps_2.py —— TestLoggerExtras(douyin_batch.logger)
###########################################################################


class TestLoggerExtras:
    def test_get_logger_returns_same_instance(self):
        from douyin_batch.logger import get_logger

        a = get_logger()
        b = get_logger()
        assert a is b

    def test_logger_set_level_and_quiet(self, tmp_path):
        from douyin_batch.logger import get_logger

        log = get_logger()
        # The console handler is at index 0.
        log.set_level("DEBUG")
        log.set_quiet(True)
        log.set_quiet(False)
        log.set_level("WARNING")

    def test_logger_add_file_handler(self, tmp_path):
        from douyin_batch.logger import get_logger

        log = get_logger()
        log_file = tmp_path / "subdir" / "log.txt"
        log.add_file_handler(log_file)
        # The parent dir was created on demand.
        assert log_file.parent.exists()
        log.info("hello from coverage test")
        assert "hello from coverage test" in log_file.read_text(encoding="utf-8")
