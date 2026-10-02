"""URL 规范化保留多 P 参数 ?p=N 的回归测试

背景:B 站多 P 合集 URL 形如 /video/BVxxx?p=N,规范化时若把 query
剥掉,所有分 P 都会静默落到 P1(转录 37 集全是第一集的内容)。
"""

import unittest

from mediascribe.url_utils import normalize_bilibili_url, normalize_url


class TestBilibiliPageParamPreserved(unittest.TestCase):
    def test_normalize_bilibili_url_keeps_p(self):
        url, bvid = normalize_bilibili_url("https://www.bilibili.com/video/BV15ocBzQEJJ?p=12")
        self.assertEqual(bvid, "BV15ocBzQEJJ")
        self.assertEqual(url, "https://www.bilibili.com/video/BV15ocBzQEJJ?p=12")

    def test_normalize_bilibili_url_drops_p1(self):
        """p=1 等价于默认页,直接去掉"""
        url, _ = normalize_bilibili_url("https://www.bilibili.com/video/BV15ocBzQEJJ?p=1")
        self.assertEqual(url, "https://www.bilibili.com/video/BV15ocBzQEJJ")

    def test_normalize_bilibili_url_without_p(self):
        url, bvid = normalize_bilibili_url("https://www.bilibili.com/video/BV15ocBzQEJJ/")
        self.assertEqual(url, "https://www.bilibili.com/video/BV15ocBzQEJJ")
        self.assertEqual(bvid, "BV15ocBzQEJJ")

    def test_normalize_url_keeps_p(self):
        url = normalize_url("https://www.bilibili.com/video/BV15ocBzQEJJ?p=3&vd_source=x")
        self.assertEqual(url, "https://www.bilibili.com/video/BV15ocBzQEJJ?p=3")

    def test_other_query_params_still_dropped(self):
        """只保留 p,其余跟踪参数照旧清理"""
        url, _ = normalize_bilibili_url(
            "https://www.bilibili.com/video/BV15ocBzQEJJ/?share_source=copy_web&vd_source=x&p=5"
        )
        self.assertEqual(url, "https://www.bilibili.com/video/BV15ocBzQEJJ?p=5")


if __name__ == "__main__":
    unittest.main()
