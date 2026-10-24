"""Tests for the WhisperX fallback chain (round 6 / task G)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


class TestTranscriberFactory(unittest.TestCase):
    def test_get_transcriber_whisper(self):
        from mediascribe.transcribers import (
            WhisperTranscriber,
            get_transcriber,
        )

        t = get_transcriber("whisper", model="tiny")
        self.assertIsInstance(t, WhisperTranscriber)
        self.assertEqual(t.model_name, "tiny")

    def test_get_transcriber_unknown_raises(self):
        from mediascribe.transcribers import get_transcriber

        with self.assertRaises(ValueError):
            get_transcriber("does-not-exist")

    def test_fallback_chain_order(self):
        from mediascribe.transcribers import DEFAULT_FALLBACK_CHAIN

        # whisperx first (most accurate), whisper last (always available).
        # whisper-cpp 垫底（外部二进制 + GGML 权重需预置，是最后回退）——
        # 2026-10-23 选型落地新增。
        self.assertEqual(
            list(DEFAULT_FALLBACK_CHAIN),
            ["whisperx", "faster-whisper", "whisper", "whisper-cpp"],
        )

    def test_memory_pressure_promotes_whisper_cpp(self):
        """内存紧张时 whisper.cpp 应排到 faster-whisper 之前（它最省内存）。"""
        from unittest import mock

        from mediascribe.transcribers.factory import _chain_for_device

        with mock.patch("mediascribe.transcribers.factory._memory_pressure", return_value=True):
            chain = _chain_for_device("cpu")
        self.assertLess(chain.index("whisper-cpp"), chain.index("faster-whisper"))

    def test_fallback_picks_available(self):
        """When the preferred engine is missing, fallback to next."""
        from mediascribe.transcribers import (
            WhisperTranscriber,
            get_transcriber_with_fallback,
        )

        # whisperx and faster-whisper are optional; on this host only
        # one (or none) is likely installed. We assert that the
        # returned instance is some Transcriber subclass — at minimum
        # WhisperTranscriber.
        t = get_transcriber_with_fallback("whisperx", model="tiny")
        self.assertTrue(hasattr(t, "transcribe"))
        # On dev hosts without whisperx, we expect to fall through to
        # faster-whisper or whisper. Verify the chain resolves.
        from mediascribe.transcribers import list_available_engines

        avail = list_available_engines()
        self.assertIn("whisper", avail)  # hard dep

    def test_fallback_preferred_added_to_chain(self):
        from mediascribe.transcribers import get_transcriber_with_fallback

        # Even if "faster-whisper" isn't in the default chain, passing
        # it as preferred should put it first.
        t = get_transcriber_with_fallback("faster-whisper", model="tiny")
        self.assertTrue(hasattr(t, "transcribe"))

    def test_engine_available_optional(self):
        from mediascribe.transcribers.factory import _engine_available

        # whisper is hard dep → always available
        self.assertTrue(_engine_available("whisper"))
        # whisperx may or may not be installed
        result = _engine_available("whisperx")
        self.assertIsInstance(result, bool)


class TestEngineAvailability(unittest.TestCase):
    def test_list_available_includes_whisper(self):
        from mediascribe.transcribers import list_available_engines

        engines = list_available_engines()
        self.assertIsInstance(engines, list)
        self.assertIn("whisper", engines)

    def test_chain_resolves_even_on_minimal_install(self):
        """Even on a host with only `whisper`, the factory should
        return *some* transcriber (because whisper is a hard dep)."""
        from mediascribe.transcribers import get_transcriber_with_fallback

        t = get_transcriber_with_fallback()
        self.assertIsNotNone(t)


if __name__ == "__main__":
    unittest.main()
