"""build_markdown 正文来源回归测试(v3.4.3 修复: 后处理文本曾被 segments 分支丢弃)"""

from mediascribe.markdown_builder import build_markdown


class _FakeDownloaded:
    metadata = {"title": "测试标题"}


def _segments():
    return [
        {"start": 0.0, "end": 2.0, "text": "矛盾而分第一个分段"},
        {"start": 2.0, "end": 4.0, "text": "第二个分段"},
    ]


class TestBuildMarkdownBodySource:
    def test_default_mode_uses_processed_text_not_raw_segments(self):
        # 回归(2026-10-02): 此前只要 ASR 返回 segments,正文就用 raw 分段
        # 文本,把已过术语校正/收敛/LLM 润色的 text 整个丢弃
        md = build_markdown(
            "douyin_123",
            "已校正全文包含矛盾二分。",
            {"segments": _segments()},
            _FakeDownloaded(),
        )
        assert "已校正全文包含矛盾二分。" in md
        assert "矛盾而分" not in md

    def test_timestamps_mode_keeps_raw_segments_with_prefix(self):
        md = build_markdown(
            "douyin_123",
            "已校正全文。",
            {"segments": _segments()},
            _FakeDownloaded(),
            timestamps=True,
        )
        # 相邻 segment 会被聚成一个段落组, 前缀取组首 start
        assert "**[00:00]** 矛盾而分第一个分段第二个分段" in md
        assert "已校正全文。" not in md

    def test_no_segments_falls_back_to_processed_text(self):
        md = build_markdown("douyin_123", "纯文本已校正全文。", None, _FakeDownloaded())
        assert "纯文本已校正全文。" in md
