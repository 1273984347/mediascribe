"""v3.4.3 2 连重复句检测(detect_repeated_pairs)与 seed 术语库数据卫生测试"""

from pathlib import Path

from mediascribe.post_process import (
    collapse_hallucination_repeats,
    detect_repeated_pairs,
    detect_repeated_pairs_detailed,
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

    def test_sentence_level_dup_with_commas(self):
        # 回归(2026-10-02 刻舟求剑一集): 句级 A,A,B 且复读句含逗号 —
        # 只在短语级扫描会被逗号切碎而漏检; 须两级扫描
        text = (
            "所以,在最后的升华点的时候,我们一定能够找到它。"
            "所以,在最后的升华点的时候,我们一定能够找到它。"
            "所以,在最后的升华点的时候,我们一定要强调。"
        )
        pairs = detect_repeated_pairs(text)
        assert len(pairs) == 1
        assert "升华点" in pairs[0]

    def test_real_full_transcript_both_dups(self):
        # 真实案例(刻舟求剑一集 raw): 句级 2 连 + 尾段整句 2 连各一处
        text = (
            "所以,在最后的升华点的时候,我们一定能够找到它。"
            "所以,在最后的升华点的时候,我们一定能够找到它。"
            "所以,在最后的升华点的时候,我们一定要强调。\n"
            "正因为如此,我们才能够更加诚实地去分辨,这些改变究竟让我们更有担当,"
            "还是让我们越来越善于为冷漠寻找理由。\n"
            "正因为如此,我们才能够更加诚实地去分辨,这些改变究竟让我们更有担当,"
            "还是让我们越来越善于为冷漠寻找理由。"
        )
        pairs = detect_repeated_pairs(text)
        assert len(pairs) == 2

    def test_empty_and_single(self):
        assert detect_repeated_pairs("") == []
        assert detect_repeated_pairs("只有一句话。") == []


class TestDetailedClassification:
    """echo/artifact 启发式标注 — 用四个真实校准样本定标。"""

    def test_real_echo_second_person(self):
        # 过度坦诚一集真应答: 讲者复述学生主张再反驳(后缀重叠路径命中)
        text = (
            "有很多同学就很积极说,老师,我要把那个对自己坦诚写在第一个分论点。"
            "你要写在第一个分论点,你就偏题。"
        )
        pairs = detect_repeated_pairs_detailed(text)
        assert len(pairs) == 1
        assert pairs[0]["kind"] == "echo"

    def test_real_artifact_suo_yi(self):
        # 人的寿命一集真伪影: 后续句以"所以"承接(承接词不作 echo 信号)
        text = (
            "我不会产出这个期待,是因为我自己没有办法去考北大,"
            "是因为我自己没有办法去考北大,所以说我希望你去考北大。"
        )
        pairs = detect_repeated_pairs_detailed(text)
        assert pairs[0]["kind"] == "artifact"

    def test_real_artifact_sentence_level(self):
        # 刻舟求剑一集真伪影: 句级 A,A,B
        text = (
            "所以,在最后的升华点的时候,我们一定能够找到它。"
            "所以,在最后的升华点的时候,我们一定能够找到它。"
            "所以,在最后的升华点的时候,我们一定要强调。"
        )
        pairs = detect_repeated_pairs_detailed(text)
        assert pairs[0]["kind"] == "artifact"

    def test_real_echo_contrast_follows(self):
        # 后续句为真转折 → 倾向应答式
        text = "我之前跟你说过这件事。但是这件事你从来没听进去,这件事你从来没听进去,真可惜。"
        pairs = detect_repeated_pairs_detailed(text)
        assert pairs[0]["kind"] == "echo"

    def test_matches_plain_detector_text(self):
        text = "第一行重复内容,第一行重复内容。\n第二行:第一行重复内容,第一行重复内容。"
        plain = detect_repeated_pairs(text)
        detailed = detect_repeated_pairs_detailed(text)
        assert [p["text"] for p in detailed] == plain

    def test_same_dup_across_lines_reported_once(self):
        text = "第一行重复内容,第一行重复内容。\n第二行:第一行重复内容,第一行重复内容。"
        pairs = detect_repeated_pairs(text)
        assert len(pairs) == 1

    def test_suffix_overlap_partial_repeat_flagged(self):
        # 真实案例(人的寿命一集): 共享后缀的部分重复
        text = (
            "生命从来都不是在终点的地方被定义的,而是在终点的地方被定义的,"
            "而是在每一个今天的日升月落之中被雕刻的。"
        )
        pairs = detect_repeated_pairs(text)
        assert len(pairs) == 1
        assert "被定义" in pairs[0]

    def test_parallel_sentences_not_flagged_as_suffix_dup(self):
        # 排比/正常接续不误报: 尾部重叠 < suffix_len(8) 或短于 min_len
        assert detect_repeated_pairs("他昨天去了北京,我今天也去了北京。") == []
        assert detect_repeated_pairs("我们要活在当下,活在当下,这才是最重要的。") == []
        assert detect_repeated_pairs("我知道什么是对的,什么是对的。") == []


class TestSeedTermsDataHygiene:
    """seed 脚本数据卫生: wrong/right 非空、不同、无重复对。"""

    def test_seed_terms_are_wellformed(self):
        import importlib.util

        script = Path(__file__).resolve().parents[1] / "scripts" / "seed_learned_terms.py"
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
