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
from typing import Any, List, Optional

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class FakeInner:
    """A Transcriber double that pretends to transcribe each chunk."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: List[str] = []

    def transcribe(
        self,
        audio_or_video_path: str,
        output_path: str,
        *,
        language: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(audio_or_video_path)
        # Pretend we have one 2-second segment per chunk.
        text = f"text for {Path(audio_or_video_path).stem}"
        Path(output_path).write_text(text, encoding="utf-8")

        class _R:
            pass
        r = _R()
        r.text = text
        r.segments = [{"start": 0.0, "end": 2.0, "text": text}]
        return r


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
            out_md = self.tmpdir / "out.md"
            result = ct.transcribe(str(self.src), str(out_md))
        finally:
            m.split_audio = original
        self.assertEqual(len(inner.calls), 3)
        self.assertTrue(out_md.exists())
        # Three chunks of "text for <stem>"
        merged_text = out_md.read_text(encoding="utf-8")
        self.assertIn("text for", merged_text)
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
        m.split_audio = lambda *a, **kw: [bad_chunk, good_chunk]
        try:
            class RaisingOnMissing:
                name = "boom"
                def transcribe(self, audio_or_video_path, *a, **kw):
                    # Raise ONLY when the chunk file is missing; this
                    # simulates the real-world "ffmpeg truncated this
                    # chunk to zero bytes" recovery.
                    p = Path(audio_or_video_path)
                    if not p.exists():
                        raise RuntimeError("kaboom: file missing")
                    text = f"text for {p.stem}"
                    Path(a[0]).write_text(text, encoding="utf-8") if a else None
                    out = a[0] if a else None
                    Path(out).write_text(text, encoding="utf-8")
                    class _R:
                        pass
                    r = _R()
                    r.text = text
                    r.segments = [{"start": 0.0, "end": 1.0, "text": text}]
                    return r

            ct = ChunkedTranscriber(RaisingOnMissing())
            out_md = self.tmpdir / "out2.md"
            result = ct.transcribe(str(self.src), str(out_md))
        finally:
            pass
        # Good chunk produced text
        merged = out_md.read_text(encoding="utf-8")
        self.assertIn("text for", merged)
        # At least one error recorded (for the missing chunk)
        self.assertTrue(any(c.error for c in result.chunks))


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
