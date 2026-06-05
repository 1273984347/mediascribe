"""
Regression test: WeChat MP detector must accept both URL forms.

The detector previously required ``/s?`` in the URL, which rejected
the more common ``/s/<id>`` form found in the wild.  This test pins
both shapes to ``wechat_mp``.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


class TestWechatMpDetector(unittest.TestCase):
    """Verify the platform detector recognises both WeChat URL shapes."""

    def test_s_question_mark_form(self):
        from video2text.inputs import parse_source
        url = "https://mp.weixin.qq.com/s?__biz=MzA&mid=123&idx=1"
        ref = parse_source(url)
        self.assertEqual(ref.kind, "wechat_mp")
        self.assertEqual(ref.url, url)

    def test_s_slash_id_form(self):
        from video2text.inputs import parse_source
        url = "https://mp.weixin.qq.com/s/abc123def456?__biz=MzB&mid=456"
        ref = parse_source(url)
        self.assertEqual(ref.kind, "wechat_mp")

    def test_s_slash_no_query_still_detected(self):
        # The /s/ substring alone is enough to disambiguate from
        # any other mp.weixin.qq.com path (e.g. /cgi-bin/...).
        from video2text.inputs import parse_source
        url = "https://mp.weixin.qq.com/s/abc123def456"
        ref = parse_source(url)
        self.assertEqual(ref.kind, "wechat_mp")

    def test_unrelated_mp_url_still_video(self):
        # A non-article path on the same host must NOT be classified
        # as wechat_mp.
        from video2text.inputs import parse_source
        url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
        ref = parse_source(url)
        self.assertNotEqual(ref.kind, "wechat_mp")


if __name__ == "__main__":
    unittest.main()
