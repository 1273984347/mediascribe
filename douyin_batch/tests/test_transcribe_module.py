"""Tests for transcribe.py — TranscriberPool + transcribe_audio()."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class TestTranscriberPoolSingleton(unittest.TestCase):
    """TranscriberPool must be a singleton with lazy, idempotent init."""

    def setUp(self) -> None:
        # Reset singleton so each test gets a fresh pool
        from douyin_batch import transcribe

        transcribe.TranscriberPool._instance = None

    def tearDown(self) -> None:
        from douyin_batch import transcribe

        transcribe.TranscriberPool._instance = None

    def test_singleton_same_instance(self):
        from douyin_batch.transcribe import TranscriberPool

        with mock.patch("mediascribe.Pipeline") as PipeCls, mock.patch(
            "mediascribe.Settings"
        ) as SetCls:
            SetCls.return_value = mock.MagicMock(name="Settings")
            PipeCls.return_value = mock.MagicMock(name="Pipeline")
            a = TranscriberPool()
            b = TranscriberPool()
        self.assertIs(a, b)
        # Pipeline constructed only once
        PipeCls.assert_called_once()

    def test_init_is_idempotent(self):
        from douyin_batch.transcribe import TranscriberPool

        with mock.patch("mediascribe.Pipeline") as PipeCls, mock.patch(
            "mediascribe.Settings"
        ) as SetCls:
            SetCls.return_value = mock.MagicMock(name="Settings")
            PipeCls.return_value = mock.MagicMock(name="Pipeline")
            pool = TranscriberPool()
            pool.pipeline = mock.MagicMock(name="pipeline_override")
            # second call should NOT reinit pipeline
            again = TranscriberPool()
        self.assertIs(pool, again)
        # pipeline reference preserved
        self.assertIs(again.pipeline, pool.pipeline)
        # Pipeline() called only once
        PipeCls.assert_called_once()

    def test_singleton_via_new_skips_init(self):
        from douyin_batch import transcribe

        # Pre-set an instance; new() should return it without calling __init__
        sentinel = object()
        transcribe.TranscriberPool._instance = sentinel
        with mock.patch("mediascribe.Pipeline") as PipeCls:
            result = transcribe.TranscriberPool()
        self.assertIs(result, sentinel)
        PipeCls.assert_not_called()


class TestTranscriberPoolTranscribe(unittest.TestCase):
    """Tests for TranscriberPool.transcribe()."""

    def setUp(self) -> None:
        from douyin_batch import transcribe

        transcribe.TranscriberPool._instance = None

    def tearDown(self) -> None:
        from douyin_batch import transcribe

        transcribe.TranscriberPool._instance = None

    def _make_pool(self, pipeline_mock):
        from douyin_batch.transcribe import TranscriberPool

        pool = TranscriberPool.__new__(TranscriberPool)
        pool.pipeline = pipeline_mock
        pool.settings = mock.MagicMock()
        pool._initialized = True
        return pool

    def test_transcribe_returns_transcript_path(self):
        from douyin_batch.transcribe import TranscriberPool

        fake_result = mock.MagicMock()
        fake_result.transcript_path = Path("/tmp/out.md")
        pipe = mock.MagicMock()
        pipe.transcribe.return_value = fake_result
        pool = self._make_pool(pipe)
        result = pool.transcribe(Path("/tmp/in.wav"))
        self.assertEqual(result, Path("/tmp/out.md"))
        pipe.transcribe.assert_called_once()
        # Second positional arg is language="zh"
        args, kwargs = pipe.transcribe.call_args
        self.assertEqual(kwargs.get("language"), "zh")
        self.assertEqual(Path(args[0]), Path("/tmp/in.wav"))

    def test_transcribe_returns_none_on_exception(self):
        from douyin_batch.transcribe import TranscriberPool

        pipe = mock.MagicMock()
        pipe.transcribe.side_effect = RuntimeError("model crashed")
        pool = self._make_pool(pipe)
        # capture the log output on failure
        with mock.patch("douyin_batch.transcribe.logger") as mock_log:
            result = pool.transcribe(Path("/tmp/bad.wav"))
        self.assertIsNone(result)
        # logger should have been called with an error message
        mock_log.error.assert_called()
        msg = mock_log.error.call_args.args[0]
        self.assertIn("转录失败", msg)
        self.assertIn("model crashed", str(mock_log.error.call_args.args[1]))


class TestTranscribeAudio(unittest.TestCase):
    """Tests for the transcribe_audio() module-level helper."""

    def setUp(self) -> None:
        from douyin_batch import transcribe

        transcribe.TranscriberPool._instance = None

    def tearDown(self) -> None:
        from douyin_batch import transcribe

        transcribe.TranscriberPool._instance = None

    def test_transcribe_audio_uses_pool(self):
        from douyin_batch import transcribe

        fake_result = mock.MagicMock()
        fake_result.transcript_path = Path("/tmp/x.md")
        with mock.patch.object(
            transcribe.TranscriberPool, "transcribe", return_value=fake_result.transcript_path
        ) as mock_tx:
            out = transcribe.transcribe_audio(Path("/tmp/x.wav"))
        self.assertEqual(out, Path("/tmp/x.md"))
        mock_tx.assert_called_once_with(Path("/tmp/x.wav"))

    def test_transcribe_audio_returns_none_on_failure(self):
        from douyin_batch import transcribe

        with mock.patch.object(
            transcribe.TranscriberPool, "transcribe", return_value=None
        ) as mock_tx:
            out = transcribe.transcribe_audio(Path("/tmp/bad.wav"))
        self.assertIsNone(out)
        mock_tx.assert_called_once()


if __name__ == "__main__":
    unittest.main()
