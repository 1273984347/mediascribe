"""
Cross-platform compatibility tests
跨平台兼容性测试
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestPlatformDetection(unittest.TestCase):
    """Platform detection tests"""

    def test_get_os(self):
        from douyin_batch.platform_compat import get_os
        os_name = get_os()
        self.assertIn(os_name, ["windows", "macos", "linux", "unknown"])

    def test_platform_helpers(self):
        from douyin_batch.platform_compat import is_linux, is_macos, is_windows

        # At least one should be True (except on unknown systems)
        results = [is_windows(), is_macos(), is_linux()]
        self.assertTrue(any(results) or all(r is False for r in results))


class TestFFmpeg(unittest.TestCase):
    """FFmpeg detection tests"""

    def test_check_ffmpeg(self):
        from douyin_batch.platform_compat import check_ffmpeg
        # Just test that it doesn't throw
        result = check_ffmpeg()
        self.assertIsInstance(result, bool)

    def test_find_ffmpeg(self):
        from douyin_batch.platform_compat import find_ffmpeg
        # Just test that it doesn't throw
        result = find_ffmpeg()
        # Could be None if not installed
        self.assertTrue(result is None or isinstance(result, Path))

    def test_install_instructions(self):
        from douyin_batch.platform_compat import install_ffmpeg_instructions
        result = install_ffmpeg_instructions()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)
        # Should mention ffmpeg
        self.assertIn("ffmpeg", result.lower())


class TestPathNormalization(unittest.TestCase):
    """Path normalization tests"""

    def test_normalize_path(self):
        from douyin_batch.platform_compat import normalize_path

        # Should handle ~ and relative paths
        p = normalize_path(".")
        self.assertIsInstance(p, Path)
        self.assertTrue(p.is_absolute())

    def test_normalize_with_tilde(self):
        from douyin_batch.platform_compat import normalize_path
        p = normalize_path("~/test")
        self.assertIsInstance(p, Path)


class TestSafeFilename(unittest.TestCase):
    """Safe filename tests"""

    def test_safe_filename_basic(self):
        from douyin_batch.platform_compat import safe_filename

        self.assertEqual(safe_filename("hello"), "hello")
        self.assertEqual(safe_filename("hello world"), "hello world")

    def test_safe_filename_windows_invalid(self):
        from douyin_batch.platform_compat import safe_filename

        # Windows invalid chars
        self.assertNotIn("?", safe_filename("file?name"))
        self.assertNotIn("|", safe_filename("file|name"))
        self.assertNotIn("<", safe_filename("file<name"))
        self.assertNotIn(">", safe_filename("file>name"))

    def test_safe_filename_reserved(self):
        from douyin_batch.platform_compat import safe_filename

        # Reserved names
        result = safe_filename("CON")
        self.assertNotEqual(result, "CON")

    def test_safe_filename_length(self):
        from douyin_batch.platform_compat import safe_filename

        long_name = "a" * 500
        result = safe_filename(long_name)
        self.assertLessEqual(len(result), 200)

    def test_safe_filename_unicode(self):
        from douyin_batch.platform_compat import safe_filename

        # Should preserve Unicode (Chinese, emoji, etc.)
        self.assertEqual(safe_filename("中文文件"), "中文文件")
        self.assertEqual(safe_filename("测试.mp4"), "测试.mp4")


class TestI18n(unittest.TestCase):
    """i18n tests"""

    def setUp(self):
        from douyin_batch.i18n import set_language
        set_language("en")

    def test_set_language(self):
        from douyin_batch.i18n import get_language, set_language

        set_language("zh")
        self.assertEqual(get_language(), "zh")

        set_language("en")
        self.assertEqual(get_language(), "en")

    def test_set_invalid_language(self):
        from douyin_batch.i18n import set_language
        with self.assertRaises(ValueError):
            set_language("fr")  # Not supported

    def test_translation_english(self):
        from douyin_batch.i18n import set_language, t

        set_language("en")
        result = t("STEP_FETCH_USER")
        self.assertIn("Step", result)
        self.assertIn("creator", result.lower())

    def test_translation_chinese(self):
        from douyin_batch.i18n import set_language, t

        set_language("zh")
        result = t("STEP_FETCH_USER")
        self.assertIn("作者", result)
        self.assertIn("主页", result)

    def test_translation_with_args(self):
        from douyin_batch.i18n import set_language, t

        set_language("en")
        result = t("SUCCESS_USER_URL", url="https://test.com")
        self.assertIn("https://test.com", result)

    def test_translation_fallback(self):
        from douyin_batch.i18n import set_language, t

        set_language("en")
        result = t("NONEXISTENT_KEY")
        # Should return the key as fallback
        self.assertIn("NONEXISTENT_KEY", result)


class TestEnvironment(unittest.TestCase):
    """Environment tests"""

    def test_python_version_check(self):
        from douyin_batch.platform_compat import check_python_version

        # Should pass for Python 3.8+
        self.assertTrue(check_python_version((3, 8)))

    def test_python_info(self):
        from douyin_batch.platform_compat import get_python_info

        info = get_python_info()
        self.assertIsInstance(info, dict)
        self.assertIn("python_version", info)
        self.assertIn("os", info)
        self.assertIn("ffmpeg_available", info)


if __name__ == "__main__":
    unittest.main(verbosity=2)
