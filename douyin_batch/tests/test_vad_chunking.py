"""Tests for v3.2.0a VAD-aware chunking."""
from __future__ import annotations

import sys
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _make_wav(path: Path, *, duration_s: float, rate: int = 16000,
              nchannels: int = 1, sampwidth: int = 2) -> None:
    """Write a silent PCM16 mono/stereo WAV file of the given length."""
    nframes = int(duration_s * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(nchannels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        # All-zero samples = pure silence (or sine if we want VAD to fire)
        w.writeframes(b"\x00" * nframes * nchannels * sampwidth)


def _make_sine_wav(path: Path, *, duration_s: float, rate: int = 16000,
                   freq_hz: int = 440) -> None:
    """Write a 440 Hz sine wave (should trigger VAD)."""
    import math
    import struct

    nframes = int(duration_s * rate)
    samples = []
    for i in range(nframes):
        val = int(0.3 * 32767 * math.sin(2 * math.pi * freq_hz * i / rate))
        samples.append(struct.pack("<h", val))
    pcm = b"".join(samples)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


# ---------------------------------------------------------------------------
# audio_utils.detect_speech_segments — tested via a fake webrtcvad
# ---------------------------------------------------------------------------


class TestDetectSpeechSegments(unittest.TestCase):
    """Tests for audio_utils.detect_speech_segments() with mocked webrtcvad."""

    def setUp(self) -> None:
        self.tmp = Path(self._make_tmp())
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.wav = self.tmp / "test.wav"
        _make_wav(self.wav, duration_s=1.0)

    def _make_tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="v2t_vad_")

    def test_module_loaded_without_webrtcvad(self):
        from mediascribe import audio_utils

        # Force the predicate to False and verify vad_available reflects it.
        with mock.patch.object(audio_utils, "_is_webrtcvad_available", return_value=False):
            self.assertFalse(audio_utils.vad_available())

    def test_detect_raises_when_webrtcvad_missing(self):
        from mediascribe import audio_utils

        with self.assertRaises(ImportError) as ctx:
            audio_utils.detect_speech_segments(self.wav)
        self.assertIn("webrtcvad", str(ctx.exception))

    def test_detect_with_mocked_webrtcvad(self):
        from mediascribe import audio_utils

        # Pretend webrtcvad is installed and reports speech on the
        # first 5 frames and silence thereafter.
        fake_webrtcvad = mock.MagicMock()
        fake_webrtcvad.Vad.return_value.is_speech.side_effect = (
            [True] * 5 + [False] * 27
        )
        with mock.patch.dict(sys.modules, {"webrtcvad": fake_webrtcvad}), \
             mock.patch.object(audio_utils, "_is_webrtcvad_available", return_value=True):
            segs = audio_utils.detect_speech_segments(
                self.wav, aggressiveness=2, min_speech_seconds=0.05,
            )
        # 1.0 s file at 30 ms/frame = 33 frames; 5 speech frames
        # covers 0-0.18 s; after 0.3 s padding (min_silence_seconds)
        # becomes (0.0, 0.48 s). After merge / filter:
        self.assertEqual(len(segs), 1)
        s, e = segs[0]
        self.assertAlmostEqual(s, 0.0, places=1)
        # ~5 frames * 0.03s = 0.15s, padded 0.3s on each side
        # but clamped to file end
        self.assertGreater(e, 0.4)
        self.assertLessEqual(e, 1.0)

    def test_detect_no_speech_returns_empty(self):
        from mediascribe import audio_utils

        fake_webrtcvad = mock.MagicMock()
        fake_webrtcvad.Vad.return_value.is_speech.return_value = False
        with mock.patch.dict(sys.modules, {"webrtcvad": fake_webrtcvad}), \
             mock.patch.object(audio_utils, "_is_webrtcvad_available", return_value=True):
            segs = audio_utils.detect_speech_segments(self.wav)
        self.assertEqual(segs, [])

    def test_detect_rejects_non_wav(self):
        from mediascribe import audio_utils

        mp3 = self.tmp / "x.mp3"
        mp3.write_bytes(b"fake mp3")
        fake_webrtcvad = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"webrtcvad": fake_webrtcvad}), \
             mock.patch.object(audio_utils, "_is_webrtcvad_available", return_value=True):
            with self.assertRaises(ValueError) as ctx:
                audio_utils.detect_speech_segments(mp3)
        self.assertIn("WAV", str(ctx.exception))

    def test_detect_rejects_stereo(self):
        from mediascribe import audio_utils

        stereo = self.tmp / "stereo.wav"
        _make_wav(stereo, duration_s=0.5, nchannels=2)
        fake_webrtcvad = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"webrtcvad": fake_webrtcvad}), \
             mock.patch.object(audio_utils, "_is_webrtcvad_available", return_value=True):
            with self.assertRaises(ValueError) as ctx:
                audio_utils.detect_speech_segments(stereo)
        self.assertIn("mono", str(ctx.exception))

    def test_detect_rejects_wrong_sample_rate(self):
        from mediascribe import audio_utils

        bad_rate = self.tmp / "bad.wav"
        _make_wav(bad_rate, duration_s=0.5, rate=22050)  # not supported
        fake_webrtcvad = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"webrtcvad": fake_webrtcvad}), \
             mock.patch.object(audio_utils, "_is_webrtcvad_available", return_value=True):
            with self.assertRaises(ValueError) as ctx:
                audio_utils.detect_speech_segments(bad_rate)
        self.assertIn("sample rate", str(ctx.exception))

    def test_merges_nearby_speech_regions(self):
        from mediascribe import audio_utils

        # 33 frames: speech [0..2] silence [3..4] speech [5..29] silence [30..32]
        # Without merge, that's 2 regions. With merge (gap=3 frames < min_silence=0.3s
        # = 10 frames), the two regions merge into one.
        pattern = [True] * 3 + [False] * 2 + [True] * 25 + [False] * 3
        fake_webrtcvad = mock.MagicMock()
        fake_webrtcvad.Vad.return_value.is_speech.side_effect = pattern
        with mock.patch.dict(sys.modules, {"webrtcvad": fake_webrtcvad}), \
             mock.patch.object(audio_utils, "_is_webrtcvad_available", return_value=True):
            segs = audio_utils.detect_speech_segments(
                self.wav,
                min_speech_seconds=0.05,
                min_silence_seconds=0.3,  # 0.3s = 10 frames
            )
        self.assertEqual(len(segs), 1)


# ---------------------------------------------------------------------------
# chunked._slice_long — pure helper
# ---------------------------------------------------------------------------


class TestSliceLong(unittest.TestCase):
    def test_short_segment_returns_one(self):
        from mediascribe.transcribers.chunked import _slice_long

        out = _slice_long(10.0, 25.0, chunk_seconds=60, overlap_seconds=5)
        self.assertEqual(out, [(10.0, 25.0)])

    def test_long_segment_sub_splits(self):
        from mediascribe.transcribers.chunked import _slice_long

        # 0-200s, chunk 60s, overlap 5s → stride 55s
        # windows: (0, 60), (55, 115), (110, 170), (165, 200)
        out = _slice_long(0.0, 200.0, chunk_seconds=60, overlap_seconds=5)
        self.assertEqual(out, [
            (0.0, 60.0),
            (55.0, 115.0),
            (110.0, 170.0),
            (165.0, 200.0),
        ])

    def test_exact_chunk_boundary(self):
        from mediascribe.transcribers.chunked import _slice_long

        out = _slice_long(0.0, 60.0, chunk_seconds=60, overlap_seconds=5)
        self.assertEqual(out, [(0.0, 60.0)])

    def test_offset_start(self):
        from mediascribe.transcribers.chunked import _slice_long

        out = _slice_long(100.0, 250.0, chunk_seconds=60, overlap_seconds=5)
        # stride 55; cursor 100, 155, 210, 250 (clamp)
        self.assertEqual(out, [
            (100.0, 160.0),
            (155.0, 215.0),
            (210.0, 250.0),
        ])


# ---------------------------------------------------------------------------
# chunked._vad_segmentation_available — guard predicate
# ---------------------------------------------------------------------------


class TestVadSegmentationAvailable(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self._make_tmp())
        self.tmp.mkdir(parents=True, exist_ok=True)

    def _make_tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="v2t_vad_avail_")

    def test_returns_false_when_webrtcvad_missing(self):
        from mediascribe import audio_utils
        from mediascribe.transcribers import chunked

        wav = self.tmp / "a.wav"
        _make_wav(wav, duration_s=0.5)
        with mock.patch.object(audio_utils, "_is_webrtcvad_available", return_value=False):
            self.assertFalse(chunked._vad_segmentation_available(wav))

    def test_returns_false_for_non_wav(self):
        from mediascribe import audio_utils
        from mediascribe.transcribers.chunked import _vad_segmentation_available

        mp3 = self.tmp / "a.mp3"
        mp3.write_bytes(b"x")
        with mock.patch.object(audio_utils, "_is_webrtcvad_available", return_value=True):
            self.assertFalse(_vad_segmentation_available(mp3))

    def test_returns_true_for_compatible_wav(self):
        from mediascribe import audio_utils
        from mediascribe.transcribers.chunked import _vad_segmentation_available

        wav = self.tmp / "a.wav"
        _make_wav(wav, duration_s=0.5)
        with mock.patch.object(audio_utils, "_is_webrtcvad_available", return_value=True):
            self.assertTrue(_vad_segmentation_available(wav))

    def test_returns_false_for_stereo(self):
        from mediascribe import audio_utils
        from mediascribe.transcribers.chunked import _vad_segmentation_available

        stereo = self.tmp / "s.wav"
        _make_wav(stereo, duration_s=0.5, nchannels=2)
        with mock.patch.object(audio_utils, "_is_webrtcvad_available", return_value=True):
            self.assertFalse(_vad_segmentation_available(stereo))


# ---------------------------------------------------------------------------
# ChunkedTranscriber with use_vad — full integration (mocked ffmpeg)
# ---------------------------------------------------------------------------


class TestChunkedWithVad(unittest.TestCase):
    """Verify ChunkedTranscriber passes use_vad through to split_audio."""

    def setUp(self) -> None:
        self.tmp = Path(self._make_tmp())
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.wav = self.tmp / "input.wav"
        _make_sine_wav(self.wav, duration_s=30.0)  # 30 s of "speech"

    def _make_tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="v2t_vad_tx_")

    def test_use_vad_false_uses_fixed_windows(self):
        from mediascribe.transcribers.chunked import ChunkedTranscriber

        inner = mock.MagicMock()
        inner.transcribe.return_value = mock.MagicMock(text="hi", segments=[])
        inner.name = "fake"
        with mock.patch("mediascribe.transcribers.chunked.shutil.which", return_value="ffmpeg"), \
             mock.patch("mediascribe.transcribers.chunked.subprocess.run"), \
             mock.patch("mediascribe.transcribers.chunked._vad_segmentation_available", return_value=True), \
             mock.patch(
                 "mediascribe.transcribers.chunked.probe_duration",
                 return_value=30.0,
             ):
            tx = ChunkedTranscriber(inner, chunk_seconds=10, overlap_seconds=0, use_vad=False)
            result = tx.transcribe(str(self.wav), output_dir=str(self.tmp))
        # 3 fixed windows (10s each over 30s, no overlap)
        self.assertEqual(len(result.chunks), 3)
        self.assertEqual(inner.transcribe.call_count, 3)

    def test_use_vad_true_calls_vad_and_aligns(self):
        from mediascribe.transcribers.chunked import ChunkedTranscriber

        inner = mock.MagicMock()
        inner.transcribe.return_value = mock.MagicMock(text="x", segments=[])
        inner.name = "fake"
        with mock.patch("mediascribe.transcribers.chunked.shutil.which", return_value="ffmpeg"), \
             mock.patch("mediascribe.transcribers.chunked.subprocess.run"), \
             mock.patch("mediascribe.transcribers.chunked.probe_duration", return_value=30.0), \
             mock.patch("mediascribe.transcribers.chunked._vad_segmentation_available", return_value=True), \
             mock.patch(
                 "mediascribe.transcribers.chunked._windows_from_vad",
                 return_value=[(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)],
             ) as wfv:
            tx = ChunkedTranscriber(inner, chunk_seconds=10, overlap_seconds=0, use_vad=True)
            result = tx.transcribe(str(self.wav), output_dir=str(self.tmp))
        wfv.assert_called_once()
        self.assertEqual(len(result.chunks), 3)
        for cr in result.chunks:
            self.assertGreaterEqual(cr.chunk.start, 0.0)
            self.assertLessEqual(cr.chunk.end, 30.0)

    def test_vad_falls_back_when_vad_unavailable(self):
        from mediascribe.transcribers.chunked import ChunkedTranscriber

        inner = mock.MagicMock()
        inner.transcribe.return_value = mock.MagicMock(text="x", segments=[])
        inner.name = "fake"
        with mock.patch("mediascribe.transcribers.chunked.shutil.which", return_value="ffmpeg"), \
             mock.patch("mediascribe.transcribers.chunked.subprocess.run"), \
             mock.patch("mediascribe.transcribers.chunked.probe_duration", return_value=30.0), \
             mock.patch("mediascribe.transcribers.chunked._vad_segmentation_available", return_value=False):
            tx = ChunkedTranscriber(inner, chunk_seconds=10, overlap_seconds=0, use_vad=True)
            result = tx.transcribe(str(self.wav), output_dir=str(self.tmp))
        self.assertEqual(len(result.chunks), 3)

    def test_vad_falls_back_when_detect_raises(self):
        from mediascribe.transcribers.chunked import ChunkedTranscriber

        inner = mock.MagicMock()
        inner.transcribe.return_value = mock.MagicMock(text="x", segments=[])
        inner.name = "fake"
        with mock.patch("mediascribe.transcribers.chunked.shutil.which", return_value="ffmpeg"), \
             mock.patch("mediascribe.transcribers.chunked.subprocess.run"), \
             mock.patch("mediascribe.transcribers.chunked.probe_duration", return_value=30.0), \
             mock.patch("mediascribe.transcribers.chunked._vad_segmentation_available", return_value=True), \
             mock.patch(
                 "mediascribe.transcribers.chunked._windows_from_vad",
                 side_effect=RuntimeError("vad boom"),
             ), \
             mock.patch("builtins.print") as mprint:
            tx = ChunkedTranscriber(inner, chunk_seconds=10, overlap_seconds=0, use_vad=True)
            result = tx.transcribe(str(self.wav), output_dir=str(self.tmp))
        self.assertEqual(len(result.chunks), 3)
        printed = " ".join(str(c.args[0]) for c in mprint.call_args_list)
        self.assertIn("VAD split failed", printed)


if __name__ == "__main__":
    unittest.main()
