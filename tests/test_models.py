"""mediascribe.models 的测试 —— 迁入自 tests/test_coverage_gaps_2.py(按被测模块归位)。"""

from __future__ import annotations

from pathlib import Path

###########################################################################
# 迁入自 tests/test_coverage_gaps_2.py —— TestModels(mediascribe.models)
###########################################################################


class TestModels:
    """Pure dataclass helpers — no external deps."""

    def test_source_ref_display_name_bilibili(self):
        from mediascribe.models import SourceRef

        s = SourceRef(raw_input="x", kind="bilibili", bv="BV1abc")
        assert s.display_name == "BV1abc"
        assert s.is_known_kind is True

    def test_source_ref_display_name_fallbacks(self):
        from mediascribe.models import SourceRef

        s = SourceRef(raw_input="raw", kind="video", path=Path("/tmp/a.mp4"))
        assert s.display_name == "a.mp4"
        s2 = SourceRef(raw_input="raw", kind="audio", url="http://x/a")
        assert s2.display_name == "http://x/a"
        s3 = SourceRef(raw_input="the raw text", kind="unknown")
        assert s3.display_name == "the raw text"

    def test_source_ref_unknown_kind(self):
        from mediascribe.models import KNOWN_SOURCE_KINDS, SourceRef

        s = SourceRef(raw_input="x", kind="something-new")
        assert s.is_known_kind is False
        # KNOWN_SOURCE_KINDS is the single source of truth.
        assert "bilibili" in KNOWN_SOURCE_KINDS
        assert "wechat_mp" in KNOWN_SOURCE_KINDS

    def test_transcript_result_defaults(self):
        from mediascribe.models import SourceRef, TranscriptResult

        t = TranscriptResult(
            source=SourceRef(raw_input="x", kind="video"),
            engine="whisper",
            model="base",
            text="hi",
            audio_path=Path("/tmp/a.wav"),
            transcript_path=Path("/tmp/t.md"),
        )
        # Optional defaults
        assert t.video_path is None
        assert t.metadata_path is None
        assert t.metadata is None
        assert t.segments is None
        assert t.language is None
        assert t.speaker_diarization is False
