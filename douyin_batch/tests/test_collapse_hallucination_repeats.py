"""幻觉重复收敛(collapse_hallucination_repeats)与 banner 附注测试"""

import unittest

from mediascribe.post_process import collapse_hallucination_repeats


class TestCollapseHallucinationRepeats(unittest.TestCase):
    def test_collapse_run_of_five_with_variant_tail(self):
        text = (
            "第一句,这个世界是怎样存在的,这个世界是怎样存在的,这个世界是怎样存在的,"
            "这个世界是怎样存在的,这个世界是怎样存在的呢?结尾语。"
        )
        new, n = collapse_hallucination_repeats(text)
        self.assertEqual(n, 3)
        self.assertIn("这个世界是怎样存在的,这个世界是怎样存在的呢?结尾语。", new)
        self.assertIn("第一句,", new)

    def test_two_repeats_kept_as_verbal_emphasis(self):
        text = "老师强调,对的对的,这是口语强调,没问题。"
        new, n = collapse_hallucination_repeats(text)
        self.assertEqual(n, 0)
        self.assertEqual(new, text)

    def test_truncated_loop_tail(self):
        text = "我们眼前有一朵花," * 9 + "我们眼前有一"
        new, n = collapse_hallucination_repeats(text)
        self.assertEqual(n, 8)
        self.assertEqual(new, "我们眼前有一朵花,我们眼前有一")

    def test_multiline_processed_independently(self):
        text = "第一段内容。\n第二段,我们坚持下去了,我们坚持下去了,我们坚持下去了,我们坚持下去了,继续。"
        new, n = collapse_hallucination_repeats(text)
        self.assertEqual(n, 3)
        self.assertTrue(new.startswith("第一段内容。\n第二段,我们坚持下去了,继续。"))

    def test_short_sentences_not_touched(self):
        # "好的"不足 4 字, 保护规则放行(防误伤口语短词强调)
        text = "啊,啊,啊,啊,啊,好的,好的,好的,好的,这不能算幻觉。"
        new, n = collapse_hallucination_repeats(text)
        self.assertEqual(n, 0)

    def test_empty_text(self):
        self.assertEqual(collapse_hallucination_repeats(""), ("", 0))


if __name__ == "__main__":
    unittest.main()
