"""
单元测试 - 覆盖核心模块
"""
import sys
import unittest
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestProgress(unittest.TestCase):
    """进度条测试"""

    def test_format_duration(self):
        from douyin_batch.progress import format_duration

        self.assertEqual(format_duration(5), "05s")
        self.assertEqual(format_duration(65), "01:05")
        self.assertEqual(format_duration(3661), "01:01:01")

    def test_format_size(self):
        from douyin_batch.progress import format_size

        self.assertEqual(format_size(100), "100.0B")
        self.assertEqual(format_size(1024), "1.0KB")
        self.assertEqual(format_size(1024 * 1024), "1.0MB")
        self.assertEqual(format_size(1024 * 1024 * 1024), "1.0GB")

    def test_progress_tracker(self):
        from douyin_batch.progress import ProgressTracker

        tracker = ProgressTracker(total=10, desc="测试")
        self.assertEqual(tracker.completed, 0)
        self.assertEqual(tracker.failed, 0)

        tracker.update(success=True, task_time=2.0)
        self.assertEqual(tracker.completed, 1)

        tracker.update(success=False, task_time=3.0)
        self.assertEqual(tracker.completed, 2)
        self.assertEqual(tracker.failed, 1)

        # 测试 ETA
        eta = tracker.get_eta()
        self.assertGreater(eta, 0)

        # 测试渲染
        rendered = tracker.render("test_task")
        self.assertIn("测试", rendered)
        self.assertIn("2/10", rendered)  # 2 个完成，10 个总


class TestConfig(unittest.TestCase):
    """配置管理测试"""

    def test_default_config(self):
        from douyin_batch.config import BatchConfig

        config = BatchConfig()
        self.assertEqual(config.max_videos, 10)
        self.assertTrue(config.headless)
        self.assertEqual(config.max_retries, 3)

    def test_to_from_dict(self):
        from douyin_batch.config import BatchConfig

        config = BatchConfig(max_videos=20, headless=False)
        data = config.to_dict()

        # 重新加载
        config2 = BatchConfig.from_dict(data)
        self.assertEqual(config2.max_videos, 20)
        self.assertFalse(config2.headless)

    def test_from_file(self):
        import tempfile

        from douyin_batch.config import BatchConfig

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json
            json.dump({"max_videos": 50, "headless": False}, f)
            tmp_path = Path(f.name)

        try:
            config = BatchConfig.from_file(tmp_path)
            self.assertEqual(config.max_videos, 50)
            self.assertFalse(config.headless)
        finally:
            tmp_path.unlink()

    def test_save_load(self):
        import tempfile

        from douyin_batch.config import BatchConfig

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)

        try:
            config = BatchConfig(max_videos=99, language="en")
            config.save(tmp_path)
            self.assertTrue(tmp_path.exists())

            loaded = BatchConfig.from_file(tmp_path)
            self.assertEqual(loaded.max_videos, 99)
            self.assertEqual(loaded.language, "en")
        finally:
            tmp_path.unlink()


class TestCache(unittest.TestCase):
    """缓存测试"""

    def setUp(self):
        import tempfile
        self.tmp_dir = Path(tempfile.mkdtemp())
        from douyin_batch.cache import ProcessCache
        self.cache = ProcessCache(cache_dir=self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_mark_and_check(self):
        # 初始状态
        self.assertFalse(self.cache.is_processed("test_id"))

        # 标记
        self.cache.mark_processed(
            video_id="test_id",
            video_url="https://test.com/video/123",
            transcript_path="output/test.md",
            success=True,
        )

        # 验证
        self.assertTrue(self.cache.is_processed("test_id"))
        record = self.cache.get_processed("test_id")
        self.assertEqual(record["url"], "https://test.com/video/123")
        self.assertTrue(record["success"])

    def test_filter_unprocessed(self):
        # 标记一个
        self.cache.mark_processed("processed_1", "https://test.com/1")

        # 创建测试数据
        videos = [
            {"video_id": "processed_1", "url": "url1"},
            {"video_id": "unprocessed_1", "url": "url2"},
            {"video_id": "unprocessed_2", "url": "url3"},
        ]

        unprocessed = self.cache.filter_unprocessed(videos)
        self.assertEqual(len(unprocessed), 2)
        self.assertNotIn("processed_1", [v["video_id"] for v in unprocessed])

    def test_user_videos_cache(self):
        user_url = "https://test.com/user/abc"
        videos = [{"video_id": "v1"}, {"video_id": "v2"}]

        self.cache.save_user_videos(user_url, videos)
        cached = self.cache.get_user_videos(user_url)
        self.assertEqual(len(cached), 2)

    def test_stats(self):
        self.cache.mark_processed("v1", "url1", success=True)
        self.cache.mark_processed("v2", "url2", success=False)
        self.cache.mark_processed("v3", "url3", success=True)

        stats = self.cache.get_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["success"], 2)
        self.assertEqual(stats["failed"], 1)


class TestRetry(unittest.TestCase):
    """重试机制测试"""

    def test_retry_success_on_third_attempt(self):
        from douyin_batch.retry import retry

        attempts = [0]

        def flaky():
            attempts[0] += 1
            if attempts[0] < 3:
                raise ValueError("Still failing")
            return "success"

        result = retry(flaky, max_retries=3, delay=0.1)
        self.assertEqual(result, "success")
        self.assertEqual(attempts[0], 3)

    def test_retry_exhausted(self):
        from douyin_batch.retry import retry

        def always_fail():
            raise ValueError("Always fails")

        with self.assertRaises(ValueError):
            retry(always_fail, max_retries=2, delay=0.1)

    def test_retry_no_retries_needed(self):
        from douyin_batch.retry import retry

        def works():
            return "immediate"

        result = retry(works, max_retries=3)
        self.assertEqual(result, "immediate")


class TestLogger(unittest.TestCase):
    """日志测试"""

    def test_logger_singleton(self):
        from douyin_batch.logger import Logger

        log1 = Logger()
        log2 = Logger()
        self.assertIs(log1, log2)

    def test_logger_levels(self):
        from douyin_batch.logger import Logger

        log = Logger()
        # 不应抛出异常
        log.debug("test debug")
        log.info("test info")
        log.warning("test warning")
        log.error("test error")
        log.success("test success")
        log.progress("test progress")


class TestURLParsing(unittest.TestCase):
    """URL解析测试"""

    def test_bv_extraction(self):
        sys.path.insert(0, ".")
        from video2text.url_utils import extract_bvid

        self.assertEqual(
            extract_bvid("https://www.bilibili.com/video/BV1Nd596vEyU"),
            "BV1Nd596vEyU",
        )
        self.assertEqual(
            extract_bvid("https://www.bilibili.com/video/BV1Nd596vEyU?p=1"),
            "BV1Nd596vEyU",
        )
        # 无效 URL 应返回 None（而非抛异常）
        self.assertIsNone(extract_bvid("invalid url"))

    def test_short_url_detection(self):
        from video2text.url_utils import is_short_url

        self.assertTrue(is_short_url("https://b23.tv/xxxxx"))
        self.assertTrue(is_short_url("https://v.douyin.com/xxxxx/"))
        self.assertFalse(is_short_url("https://www.bilibili.com/video/BVxxx"))


if __name__ == "__main__":
    print("=" * 60)
    print("运行单元测试")
    print("=" * 60)
    unittest.main(verbosity=2)
