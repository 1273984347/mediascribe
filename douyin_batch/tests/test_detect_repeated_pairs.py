"""v3.4.3 2 连重复句检测(detect_repeated_pairs)与 seed 术语库数据卫生测试"""

from pathlib import Path

from mediascribe.post_process import (
    collapse_hallucination_repeats,
    detect_repeated_pairs,
)


class TestDetectRepeatedPairs:
    def test_flags_exact_two_long_units(self):
        # 2 连 ASR 伪影(2026-10-02 三集连续出现): 恰好 2 连且归一化长度 ≥6
        text = "我今天很难过,我今天很难过,这没有任何办法。"
        pairs = detect_repeated_pairs(text)
        assert len(pairs) == 1
        assert "我今天很难过" in pairs[0]

    def test_short_spoken_emphasis_not_flagged(self):
        # 口语强调("活在当下"4 字)不告警, 防噪音
        text = "我们要活在当下,活在当下,这才是最重要的。"
        assert detect_repeated_pairs(text) == []

    def test_three_plus_runs_not_flagged(self):
        # ≥3 连由 collapse 自动收敛, 检测器只管恰好 2 连
        text = "他说的就是这句话,他说的就是这句话,他说的就是这句话,结束了。"
        assert detect_repeated_pairs(text) == []

    def test_collapsed_text_survivors_are_detectable(self):
        # 管线实际路径: 先收敛 ≥3 连, 残留的 2 连应被检测到
        raw = (
            "真正的话说一遍就够了。真正的话说一遍就够了。真正的话说一遍就够了。"
            "但是我还要说,但是我还要说,完了。"
        )
        text, removed = collapse_hallucination_repeats(raw)
        assert removed >= 1
        pairs = detect_repeated_pairs(text)
        assert len(pairs) == 1
        assert "但是我还要说" in pairs[0]

    def test_real_episode_adjacent_dup(self):
        # 真实案例(人的寿命一集): "是因为我自己没有办法去考北大" 相邻 2 连
        text = (
            "我不会产出这个期待,是因为我自己没有办法去考北大,"
            "是因为我自己没有办法去考北大,所以说我希望你去考北大。"
        )
        pairs = detect_repeated_pairs(text)
        assert len(pairs) == 1
        assert "没有办法去考北大" in pairs[0]

    def test_empty_and_single(self):
        assert detect_repeated_pairs("") == []
        assert detect_repeated_pairs("只有一句话。") == []

    def test_same_dup_across_lines_reported_once(self):
        text = "第一行重复内容,第一行重复内容。\n第二行:第一行重复内容,第一行重复内容。"
        pairs = detect_repeated_pairs(text)
        assert len(pairs) == 1


class TestSeedTermsDataHygiene:
    """seed 脚本数据卫生: wrong/right 非空、不同、无重复对。"""

    def test_seed_terms_are_wellformed(self):
        import importlib.util

        script = Path(__file__).resolve().parents[2] / "scripts" / "seed_learned_terms.py"
        spec = importlib.util.spec_from_file_location("seed_learned_terms", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        terms = mod.SAFE_TERMS
        assert len(terms) > 50  # 10-02 批已扩充
        seen = set()
        for wrong, right in terms:
            assert wrong and right, f"空项: {(wrong, right)}"
            assert wrong != right, f"wrong==right: {wrong}"
            key = (wrong, right)
            assert key not in seen, f"重复对: {key}"
            seen.add(key)
