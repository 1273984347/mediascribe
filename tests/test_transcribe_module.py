"""Tests for transcribe.py — TranscriberPool + transcribe_audio()."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestTranscriberPoolInstances(unittest.TestCase):
    """P2-7(2026-11-20 评审): 去单例 —— 每次构造独立实例,config 不再被冻结。"""

    def test_instances_are_independent(self):
        from mediascribe.transcribe import TranscriberPool

        with (
            mock.patch("mediascribe.Pipeline") as PipeCls,
            mock.patch("mediascribe.Settings") as SetCls,
        ):
            SetCls.return_value = mock.MagicMock(name="Settings")
            PipeCls.side_effect = lambda *a, **k: mock.MagicMock(name="pipe")
            a = TranscriberPool()
            b = TranscriberPool()
        self.assertIsNot(a, b)
        self.assertIsNot(a.pipeline, b.pipeline)
        self.assertEqual(PipeCls.call_count, 2)

    def test_config_changes_take_effect(self):
        """单例时代被冻结的坑:第二次调用换 config 必须生效。"""
        from types import SimpleNamespace

        from mediascribe.transcribe import TranscriberPool

        with (
            mock.patch("mediascribe.Pipeline") as PipeCls,
            mock.patch("mediascribe.Settings") as SetCls,
        ):
            SetCls.return_value = mock.MagicMock(name="Settings")
            PipeCls.return_value = mock.MagicMock(name="Pipeline")
            TranscriberPool(config=SimpleNamespace(whisper_model="small", language="zh"))
            TranscriberPool(config=SimpleNamespace(whisper_model="large-v3", language="en"))
        models = [c.kwargs.get("model") for c in SetCls.call_args_list]
        self.assertEqual(models, ["small", "large-v3"])



class TestTranscriberPoolTranscribe(unittest.TestCase):
    """Tests for TranscriberPool.transcribe()."""

    def setUp(self) -> None:
        from mediascribe import transcribe


    def _make_pool(self, pipeline_mock):
        from mediascribe.transcribe import TranscriberPool

        pool = TranscriberPool.__new__(TranscriberPool)
        pool.pipeline = pipeline_mock
        pool.settings = mock.MagicMock()
        pool._initialized = True
        return pool

    def test_transcribe_returns_transcript_path(self):
        from mediascribe.transcribe import TranscriberPool

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
        from mediascribe.transcribe import TranscriberPool

        pipe = mock.MagicMock()
        pipe.transcribe.side_effect = RuntimeError("model crashed")
        pool = self._make_pool(pipe)
        # capture the log output on failure
        with mock.patch("mediascribe.transcribe.logger") as mock_log:
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
        from mediascribe import transcribe


    def test_transcribe_audio_uses_pool(self):
        from mediascribe import transcribe

        fake_result = mock.MagicMock()
        fake_result.transcript_path = Path("/tmp/x.md")
        with mock.patch.object(
            transcribe.TranscriberPool, "transcribe", return_value=fake_result.transcript_path
        ) as mock_tx:
            out = transcribe.transcribe_audio(Path("/tmp/x.wav"))
        self.assertEqual(out, Path("/tmp/x.md"))
        mock_tx.assert_called_once_with(Path("/tmp/x.wav"))

    def test_transcribe_audio_returns_none_on_failure(self):
        from mediascribe import transcribe

        with mock.patch.object(
            transcribe.TranscriberPool, "transcribe", return_value=None
        ) as mock_tx:
            out = transcribe.transcribe_audio(Path("/tmp/bad.wav"))
        self.assertIsNone(out)
        mock_tx.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class TestTranscriberPoolEngine(unittest.TestCase):
    """P1-1(2026-11-20 架构评审): 批处理线引擎维度透传。"""

    def setUp(self) -> None:
        from mediascribe import transcribe


    def test_config_engine_passthrough(self):
        from types import SimpleNamespace

        from mediascribe.transcribe import TranscriberPool

        cfg = SimpleNamespace(whisper_model="small", language="zh", engine="whisper")
        with (
            mock.patch("mediascribe.Pipeline"),
            mock.patch("mediascribe.Settings") as SetCls,
        ):
            TranscriberPool(config=cfg)
        SetCls.assert_called_once_with(model="small", engine="whisper")

    def test_engine_defaults_to_install_probe(self):
        from types import SimpleNamespace

        from mediascribe.config import default_engine
        from mediascribe.transcribe import TranscriberPool

        cfg = SimpleNamespace(whisper_model="small", language="zh")  # 无 engine 属性
        with (
            mock.patch("mediascribe.Pipeline"),
            mock.patch("mediascribe.Settings") as SetCls,
        ):
            TranscriberPool(config=cfg)
        SetCls.assert_called_once_with(model="small", engine=default_engine())
