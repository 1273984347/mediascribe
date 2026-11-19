"""Unit tests for the plugin system.

P2-3(2026-11-20 架构评审): 本包不再注册任何 entry-points ——
examples/ 插件是参考实现,不随安装自动激活。本文件改为验证:

1. 默认发现结果**不含**示例插件(vimeo/tcn 不在生产解析路径上);
2. 发现**机制**本身仍工作(fake entry-point → registry 装载/缓存);
3. 坏插件被隔离(一个加载失败不影响整个 registry)。
"""

import importlib.metadata
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mediascribe.plugins import registry  # noqa: E402


def _fake_ep(name: str, value: str, group: str):
    return importlib.metadata.EntryPoint(name=name, value=value, group=group)


def _patched_entry_points(eps):
    """patch importlib.metadata.entry_points 返回 EntryPoint 对象列表。

    registry 的兼容分支:无 ``select`` 属性的 list 走 ``ep.group`` 逐个过滤。"""
    return mock.patch.object(registry.importlib.metadata, "entry_points", return_value=eps)


class TestNoExamplePluginsByDefault(unittest.TestCase):
    """P2-3: 示例插件不得随安装自动激活。"""

    def setUp(self):
        registry.clear_cache()

    def tearDown(self):
        registry.clear_cache()

    def test_vimeo_not_registered(self):
        from mediascribe.plugins import list_downloaders

        self.assertNotIn("vimeo", list_downloaders(), "示例插件被自动激活了(P2-3 契约被破坏)")

    def test_tcn_not_registered(self):
        from mediascribe.plugins import list_url_transformers

        self.assertNotIn("tcn", list_url_transformers(), "示例插件被自动激活了(P2-3 契约被破坏)")

    def test_get_downloader_unknown_returns_none(self):
        from mediascribe.plugins import get_downloader

        self.assertIsNone(get_downloader("vimeo"))
        self.assertIsNone(get_downloader("__no_such_plugin__"))


class TestDiscoveryMechanism(unittest.TestCase):
    """发现机制本身仍工作 —— 用 importable 的真类做 fake entry-point。"""

    def setUp(self):
        registry.clear_cache()

    def tearDown(self):
        registry.clear_cache()

    def test_fake_entry_point_is_discovered(self):
        ep = _fake_ep(
            "fake-dl",
            "mediascribe.downloaders.base:Downloader",
            "mediascribe.downloaders",
        )
        with _patched_entry_points([ep]):
            from mediascribe.plugins import get_downloader, list_downloaders

            self.assertIn("fake-dl", list_downloaders())
            self.assertIs(get_downloader("fake-dl"), registry.Downloader)

    def test_result_is_cached_until_clear(self):
        ep = _fake_ep(
            "fake-dl",
            "mediascribe.downloaders.base:Downloader",
            "mediascribe.downloaders",
        )
        with _patched_entry_points([ep]):
            from mediascribe.plugins import list_downloaders

            first = list_downloaders()
            second = list_downloaders()
            self.assertEqual(first, second)
        # 缓存生效后,即使 entry_points 换成空的,结果仍来自缓存
        registry.clear_cache()
        with _patched_entry_points([]):
            self.assertNotIn("fake-dl", list_downloaders())

    def test_bad_plugin_isolated(self):
        """坏插件(加载抛错)被记录为异常实例,不影响其它插件。"""
        good = _fake_ep(
            "fake-dl",
            "mediascribe.downloaders.base:Downloader",
            "mediascribe.downloaders",
        )
        bad = _fake_ep(
            "bad-dl",
            "no.such.module:NoSuchThing",
            "mediascribe.downloaders",
        )
        with _patched_entry_points([good, bad]):
            from mediascribe.plugins import get_downloader

            self.assertIs(get_downloader("fake-dl"), registry.Downloader)
            # get_downloader 对坏插件返回 None(隔离);原始记录在缓存里是异常实例
            recorded = registry._load_entry_points("mediascribe.downloaders")["bad-dl"]
            self.assertIsInstance(recorded, Exception)


if __name__ == "__main__":
    unittest.main()
