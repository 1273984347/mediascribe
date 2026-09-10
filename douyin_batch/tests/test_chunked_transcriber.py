"""
Unit tests for the chunked-transcriber module.

These tests use a fake inner ``Transcriber`` that just records
which chunk paths it was called with and returns a deterministic
text.  The ffmpeg dependency is bypassed by providing pre-made
chunk files via the ``split_audio`` substitute.
"""
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class FakeInner:
    """A Transcriber double that pretends to transcribe each chunk.

    Follows the base-class contract: a single positional argument plus
    keyword options, returning a dict like the built-in engines.
    """

    name = "fake"

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def transcribe(
        self,
        audio_path: str,
        *,
        prompt: Optional[str] = None,
        progress: Optional[Any] = None,
        language: Optional[str] = None,
        **kwargs: Any,
    ) -> dict:
        self.calls.append(
            (str(audio_path), {"prompt": prompt, "language": language})
        )
        # Pretend we have one 2-second segment per chunk.
        text = f"text for {Path(audio_path).stem}"
        return {
            "text": text,
            "segments": [{"start": 0.0, "end": 2.0, "text": text}],
            "language": language,
            "model": "fake-model",
        }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestMergeHelpers(unittest.TestCase):

    def test_merge_texts_skips_empty(self):
        from video2text.transcribers.chunked import Chunk, ChunkResult, merge_texts
        r1 = ChunkResult(chunk=Chunk(0, 0, 10, Path("a")), text="hello", segments=[])
        r2 = ChunkResult(chunk=Chunk(1, 10, 20, Path("b")), text="", segments=[])
        r3 = ChunkResult(chunk=Chunk(2, 20, 30, Path("c")), text="world", segments=[])
        self.assertEqual(merge_texts([r1, r2, r3]), "hello\n\nworld")

    def test_merge_segments_sorts_by_start(self):
        from video2text.transcribers.chunked import Chunk, ChunkResult, merge_segments
        r1 = ChunkResult(
            chunk=Chunk(0, 0, 10, Path("a")),
            text="", segments=[{"start": 5.0, "end": 6.0, "text": "B"}],
        )
        r2 = ChunkResult(
            chunk=Chunk(1, 10, 20, Path("b")),
            text="", segments=[{"start": 0.0, "end": 1.0, "text": "A"}],
        )
        merged = merge_segments([r1, r2])
        self.assertEqual([s["text"] for s in merged], ["A", "B"])

    def test_merge_segments_drops_overlap_duplicates(self):
        """P1-2: 重叠区被转写两次的 segment 必须按全局时间线去重。"""
        from video2text.transcribers.chunked import (
            Chunk,
            ChunkResult,
            merge_segments,
        )
        # chunk0 窗口 (0, 20)，chunk1 窗口 (15, 35)：15-20 的重叠区
        # 内的 "dup" 被两个 chunk 各转写一次。
        r1 = ChunkResult(
            chunk=Chunk(0, 0.0, 20.0, Path("a")),
            text="",
            segments=[
                {"start": 0.0, "end": 5.0, "text": "A"},
                {"start": 15.0, "end": 20.0, "text": "dup"},
            ],
        )
        r2 = ChunkResult(
            chunk=Chunk(1, 15.0, 35.0, Path("b")),
            text="",
            segments=[
                {"start": 15.0, "end": 20.0, "text": "dup"},  # 与 r1 重复
                {"start": 20.0, "end": 25.0, "text": "C"},    # 首尾相接，保留
            ],
        )
        merged = merge_segments([r1, r2])
        self.assertEqual([s["text"] for s in merged], ["A", "dup", "C"])

    def test_merge_texts_dedupes_overlap_via_segments(self):
        """P1-2: 有 segments 时基于去重后的时间线拼接，输出无重复句。"""
        from video2text.transcribers.chunked import (
            Chunk,
            ChunkResult,
            merge_texts,
        )
        r1 = ChunkResult(
            chunk=Chunk(0, 0.0, 20.0, Path("a")),
            text="A dup",
            segments=[
                {"start": 0.0, "end": 5.0, "text": "A"},
                {"start": 15.0, "end": 20.0, "text": "dup"},
            ],
        )
        r2 = ChunkResult(
            chunk=Chunk(1, 15.0, 35.0, Path("b")),
            text="dup C",
            segments=[
                {"start": 15.0, "end": 20.0, "text": "dup"},
                {"start": 20.0, "end": 25.0, "text": "C"},
            ],
        )
        self.assertEqual(merge_texts([r1, r2]), "A\ndup\nC")


class TestChunkedTranscriber(unittest.TestCase):

    def setUp(self):
        # Build a synthetic 60-second WAV.  We never call ffmpeg;
        # we patch ``split_audio`` to return fake chunks pointing at
        # this file.
        import struct
        import wave
        tmp = Path("test_chunked_src.wav")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(struct.pack("<" + "h" * (16000 * 60), *([0] * (16000 * 60))))
        self.src = tmp
        self.tmpdir = Path("test_chunked_out")
        self.tmpdir.mkdir(exist_ok=True)

    def tearDown(self):
        if self.src.exists():
            self.src.unlink()
        if self.tmpdir.exists():
            for f in self.tmpdir.glob("**/*"):
                try:
                    f.unlink()
                except Exception:
                    pass
            for d in sorted(self.tmpdir.glob("**"), reverse=True):
                if d.is_dir():
                    try:
                        d.rmdir()
                    except Exception:
                        pass
            try:
                self.tmpdir.rmdir()
            except Exception:
                pass

    def test_chunks_created_and_merged(self):
        from video2text.transcribers.chunked import (
            Chunk,
            ChunkedTranscriber,
            split_audio,
        )
        # Patch split_audio: 3 chunks of 20s each
        chunks = [
            Chunk(0, 0.0, 20.0, self.src),
            Chunk(1, 20.0, 40.0, self.src),
            Chunk(2, 40.0, 60.0, self.src),
        ]
        # Monkey-patch split_audio for the duration of the test
        import video2text.transcribers.chunked as m
        original = m.split_audio
        m.split_audio = lambda *a, **kw: chunks
        try:
            inner = FakeInner()
            ct = ChunkedTranscriber(inner, chunk_seconds=20, overlap_seconds=2)
            result = ct.transcribe(
                str(self.src), output_dir=str(self.tmpdir), language="zh"
            )
        finally:
            m.split_audio = original
        self.assertEqual(len(inner.calls), 3)
        # 基类契约：内层转录器按关键字收到 language / prompt
        for _path, kw in inner.calls:
            self.assertEqual(kw["language"], "zh")
            self.assertIsNone(kw["prompt"])
        # 最终输出写在 output_dir 下（<stem>.md），chunk 中间文件已清理
        out_md = self.tmpdir / "test_chunked_src.md"
        self.assertTrue(out_md.exists())
        self.assertEqual(list(self.tmpdir.glob("chunk_*")), [])
        # Three chunks of "text for <stem>"
        merged_text = out_md.read_text(encoding="utf-8")
        self.assertIn("text for", merged_text)
        # 返回值与内置转录器兼容：dict 访问
        self.assertEqual(result["text"], merged_text)
        # Sidecar JSON is written
        sidecar = out_md.with_suffix(out_md.suffix + ".chunks.json")
        self.assertTrue(sidecar.exists())
        import json
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(meta["chunk_count"], 3)
        self.assertEqual(meta["engine"], "fake")
        # Segments were offset by chunk start
        starts = sorted(s["start"] for s in result.segments)
        self.assertEqual(starts[0], 0.0)  # chunk 0: 0.0 + 0.0
        self.assertEqual(starts[1], 20.0)  # chunk 1: 20.0 + 0.0
        self.assertEqual(starts[2], 40.0)  # chunk 2: 40.0 + 0.0

    def test_missing_inner_failure_recorded(self):
        import video2text.transcribers.chunked as m
        from video2text.transcribers.chunked import (
            Chunk,
            ChunkedTranscriber,
            split_audio,
        )
        bad_chunk = Chunk(0, 0.0, 10.0, Path("does_not_exist.wav"))
        good_chunk = Chunk(1, 10.0, 20.0, self.src)
        original_split_audio = m.split_audio
        m.split_audio = lambda *a, **kw: [bad_chunk, good_chunk]
        try:
            class RaisingOnMissing:
                name = "boom"
                def transcribe(self, audio_path, *a, **kw):
                    # Raise ONLY when the chunk file is missing; this
                    # simulates the real-world "ffmpeg truncated this
                    # chunk to zero bytes" recovery.
                    p = Path(audio_path)
                    if not p.exists():
                        raise RuntimeError("kaboom: file missing")
                    text = f"text for {p.stem}"
                    return {
                        "text": text,
                        "segments": [{"start": 0.0, "end": 1.0, "text": text}],
                    }

            ct = ChunkedTranscriber(RaisingOnMissing())
            out_md = self.tmpdir / "test_chunked_src.md"
            result = ct.transcribe(str(self.src), output_dir=str(self.tmpdir))
        finally:
            m.split_audio = original_split_audio
        # Good chunk produced text
        merged = out_md.read_text(encoding="utf-8")
        self.assertIn("text for", merged)
        # At least one error recorded (for the missing chunk)
        self.assertTrue(any(c.error for c in result.chunks))

    def test_temp_dir_removed_after_transcribe(self):
        """P1-5: 未传 output_dir 时，本次创建的临时 chunk 目录整体清理。"""
        import video2text.transcribers.chunked as m
        from video2text.transcribers.chunked import (
            Chunk,
            ChunkedTranscriber,
        )
        created: List[Path] = []

        def fake_split(src, *, out_dir=None, **kw):
            d = Path(out_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / "chunk_000.wav").write_bytes(b"x")
            (d / "chunk_001.wav").write_bytes(b"x")
            created.append(d)
            return [
                Chunk(0, 0.0, 10.0, d / "chunk_000.wav"),
                Chunk(1, 10.0, 20.0, d / "chunk_001.wav"),
            ]

        original = m.split_audio
        m.split_audio = fake_split
        try:
            ct = ChunkedTranscriber(FakeInner())
            result = ct.transcribe(str(self.src))
        finally:
            m.split_audio = original
        self.assertEqual(len(created), 1)
        # 临时 chunk 目录（连同失败 chunk 的残留文件）被整体删除
        self.assertFalse(created[0].exists())
        # 未落盘：output_path 为空
        self.assertIsNone(result["output_path"])
        self.assertIsNone(result["sidecar_path"])


class TestProbeDuration(unittest.TestCase):

    def test_wav_fallback(self):
        import struct
        import wave
        tmp = Path("test_probe.wav")
        try:
            with wave.open(str(tmp), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(struct.pack("<" + "h" * 16000, *([0] * 16000)))
            from video2text.transcribers.chunked import probe_duration
            # 1 second of silence
            self.assertAlmostEqual(probe_duration(tmp), 1.0, places=1)
        finally:
            if tmp.exists():
                tmp.unlink()


if __name__ == "__main__":
    unittest.main()
