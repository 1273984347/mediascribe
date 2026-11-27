"""
模型权重清单（``mediascribe.model_weights``）的单元测试。

要点：探测结果必须与「引擎实际能不能用」一致；缺权重时必须给出**可操作**
的预置指引（而不是 HuggingFace 的原始异常）。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mediascribe.model_weights import (  # noqa: E402
    ENGINE_FASTER,
    ENGINE_WHISPER,
    ENGINE_WHISPER_CPP,
    ENGINE_WHISPERX,
    check_weights,
    hf_hub_cache_dir,
    known_weight_pairs,
    missing_hint,
    openai_whisper_cache_dir,
    weight_spec,
    whisper_cpp_models_dir,
)


def setUpModule():
    from mediascribe.i18n import set_language

    set_language("zh")


def tearDownModule():
    from mediascribe.i18n import set_language

    set_language("en")


class TestDirectoryResolution(unittest.TestCase):
    def test_hf_cache_env_override(self):
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": "/tmp/hfcache"}):
            self.assertEqual(hf_hub_cache_dir(), Path("/tmp/hfcache"))

    def test_hf_home_fallback(self):
        env = {k: v for k, v in os.environ.items() if k not in ("HF_HUB_CACHE", "HF_HOME")}
        with mock.patch.dict(os.environ, env, clear=True):
            resolved = hf_hub_cache_dir()
        self.assertEqual(resolved.parts[-2:], ("huggingface", "hub"))

    def test_hf_home_env_used(self):
        env = {k: v for k, v in os.environ.items() if k != "HF_HUB_CACHE"}
        env["HF_HOME"] = "/tmp/hfhome"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(hf_hub_cache_dir(), Path("/tmp/hfhome") / "hub")

    def test_openai_cache_env_override(self):
        with mock.patch.dict(os.environ, {"WHISPER_CACHE_DIR": "/tmp/wcache"}):
            self.assertEqual(openai_whisper_cache_dir(), Path("/tmp/wcache"))

    def test_cpp_models_env_override(self):
        with mock.patch.dict(os.environ, {"MEDIASCRIBE_WHISPER_CPP_MODELS": "/tmp/cpp"}):
            self.assertEqual(whisper_cpp_models_dir(), Path("/tmp/cpp"))


class TestWeightSpec(unittest.TestCase):
    def test_known_engines(self):
        for engine in (ENGINE_FASTER, ENGINE_WHISPER, ENGINE_WHISPERX, ENGINE_WHISPER_CPP):
            spec = weight_spec(engine, "small")
            self.assertIsNotNone(spec, msg=f"{engine} 应有权重规格")
            self.assertIn("small", spec.location)

    def test_unknown_engine(self):
        self.assertIsNone(weight_spec("nope", "small"))

    def test_whisperx_uses_hf_cache(self):
        """whisperx 走 faster-whisper 后端 ⇒ 权重位置相同。"""
        self.assertEqual(
            weight_spec(ENGINE_WHISPERX, "small").kind,
            weight_spec(ENGINE_FASTER, "small").kind,
        )

    def test_hint_is_actionable(self):
        """缺权重指引必须给出**具体路径或命令**，不能是空话。"""
        for engine in (ENGINE_FASTER, ENGINE_WHISPER, ENGINE_WHISPER_CPP):
            hint = missing_hint(engine, "small")
            self.assertTrue(hint.strip(), msg=f"{engine} 缺指引")
            self.assertTrue(
                any(token in hint for token in ("http", "缓存", "放到", "预置")),
                msg=f"{engine} 指引不可操作: {hint[:80]}",
            )

    def test_known_pairs_non_empty(self):
        pairs = known_weight_pairs()
        self.assertGreater(len(pairs), 0)
        self.assertIn((ENGINE_FASTER, "small"), pairs)
        self.assertIn((ENGINE_WHISPER_CPP, "tiny"), pairs)


class TestCheckWeights(unittest.TestCase):
    def test_missing_hf_weight_detected(self):
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": "/nonexistent/hf"}):
            status = check_weights(ENGINE_FASTER, "medium")
        self.assertFalse(status.ok)
        self.assertIn("medium", status.message())

    def test_missing_openai_weight_detected(self):
        with mock.patch.dict(os.environ, {"WHISPER_CACHE_DIR": "/nonexistent/w"}):
            status = check_weights(ENGINE_WHISPER, "large-v3")
        self.assertFalse(status.ok)

    def test_present_openai_weight_detected(self):
        with tempfile_dir() as d:
            (Path(d) / "small.pt").write_bytes(b"x" * 1024)
            with mock.patch.dict(os.environ, {"WHISPER_CACHE_DIR": d}):
                status = check_weights(ENGINE_WHISPER, "small")
        self.assertTrue(status.ok, msg=status.message())

    def test_present_hf_weight_detected(self):
        with tempfile_dir() as d:
            rev = Path(d) / "models--Systran--faster-whisper-small" / "snapshots" / "abc123"
            rev.mkdir(parents=True)
            (rev / "config.json").write_text("{}", encoding="utf-8")
            with mock.patch.dict(os.environ, {"HF_HUB_CACHE": d}):
                status = check_weights(ENGINE_FASTER, "small")
        self.assertTrue(status.ok, msg=status.message())

    def test_unknown_engine_status(self):
        status = check_weights("nope", "small")
        self.assertFalse(status.ok)
        self.assertIn("未知引擎", status.hint)


class TestModelPreparation(unittest.TestCase):
    def _status(self, *, ok):
        from mediascribe.model_weights import WeightStatus

        return WeightStatus(
            engine=ENGINE_FASTER,
            model="small",
            ok=ok,
            location="fake-cache",
            hint="provision model",
        )

    def test_cached_model_is_ready_without_download(self):
        from mediascribe.model_weights import ensure_model_ready

        status = self._status(ok=True)
        with mock.patch("mediascribe.model_weights.check_weights", return_value=status):
            result = ensure_model_ready(ENGINE_FASTER, "small", allow_download=False)

        self.assertIs(result, status)

    def test_missing_model_requires_explicit_download_authorization(self):
        from mediascribe.model_weights import ModelNotReadyError, ensure_model_ready

        with mock.patch(
            "mediascribe.model_weights.check_weights", return_value=self._status(ok=False)
        ):
            with self.assertRaises(ModelNotReadyError) as exc_info:
                ensure_model_ready(ENGINE_FASTER, "small", allow_download=False)

        self.assertEqual(exc_info.exception.code, "MODEL_NOT_READY")

    def test_missing_model_can_be_downloaded_when_authorized(self):
        from mediascribe.model_weights import ensure_model_ready

        status = self._status(ok=False)
        with mock.patch("mediascribe.model_weights.check_weights", return_value=status):
            result = ensure_model_ready(ENGINE_FASTER, "small", allow_download=True)

        self.assertIs(result, status)

    def test_whisper_cpp_never_auto_downloads(self):
        from mediascribe.model_weights import ModelNotReadyError, WeightStatus, ensure_model_ready

        status = WeightStatus(
            engine=ENGINE_WHISPER_CPP,
            model="small",
            ok=False,
            location="fake-cache",
            hint="provision model",
        )
        with mock.patch("mediascribe.model_weights.check_weights", return_value=status):
            with self.assertRaises(ModelNotReadyError) as exc_info:
                ensure_model_ready(ENGINE_WHISPER_CPP, "small", allow_download=True)

        self.assertEqual(exc_info.exception.code, "MODEL_NOT_READY")

    def test_error_codes_are_stable(self):
        from mediascribe.model_weights import ModelDownloadError, ModelLoadError

        self.assertEqual(ModelDownloadError("x").code, "MODEL_DOWNLOAD_FAILED")
        self.assertEqual(ModelLoadError("x").code, "MODEL_LOAD_FAILED")

    def test_faster_whisper_prepare_loads_model(self):
        from mediascribe.transcribers.faster_whisper import FasterWhisperTranscriber

        transcriber = FasterWhisperTranscriber("small", "cpu")
        with mock.patch.object(transcriber, "_ensure_model", return_value="MODEL") as load:
            self.assertEqual(transcriber.prepare(), "MODEL")
        load.assert_called_once()

    def test_whisper_prepare_loads_model(self):
        from mediascribe.transcribers.whisper import WhisperTranscriber

        transcriber = WhisperTranscriber("small", "cpu")
        with mock.patch.object(transcriber, "_ensure_model", return_value="MODEL") as load:
            self.assertEqual(transcriber.prepare(), "MODEL")
        load.assert_called_once()

    def test_whisper_cpp_prepare_resolves_runtime_and_weights(self):
        from mediascribe.transcribers.whisper_cpp import WhisperCppTranscriber

        transcriber = WhisperCppTranscriber("small", "cpu")
        with (
            mock.patch.object(transcriber, "_resolve_binary", return_value=Path("whisper-cli")),
            mock.patch.object(transcriber, "_resolve_model", return_value=Path("ggml-small.bin")),
        ):
            transcriber.prepare()

    def test_whisperx_prepare_loads_main_model(self):
        from mediascribe.transcribers import whisperx as wx

        fake_module = mock.MagicMock()
        transcriber = wx.WhisperXTranscriber("small", "cpu")
        with (
            mock.patch.dict(sys.modules, {"whisperx": fake_module}),
            mock.patch.object(wx, "_get_cached_model", return_value="MODEL") as load,
        ):
            self.assertEqual(transcriber.prepare(language="zh"), "MODEL")
        load.assert_called_once_with(fake_module, "small", "cpu", "zh", None)


class TestLoadFailureGuidance(unittest.TestCase):
    """加载失败时必须给出带指引的错误，而不是 HuggingFace 原始异常。"""

    def test_faster_whisper_error_mentions_preset(self):
        from mediascribe.transcribers import faster_whisper as fw

        class FakeWhisperModel:
            def __init__(self, *a, **kw):
                if kw.get("local_files_only"):
                    raise RuntimeError(
                        "Cannot find an appropriate cached snapshot folder for the specified model"
                    )
                raise OSError("networking is disabled")

        fake_module = mock.MagicMock()
        fake_module.WhisperModel = FakeWhisperModel
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            with self.assertRaises(RuntimeError) as cm:
                fw._load_whisper_model("medium", "cpu", "int8")
        msg = str(cm.exception)
        self.assertIn("medium", msg)
        self.assertIn("预置", msg)
        self.assertEqual(getattr(cm.exception, "code", None), "MODEL_DOWNLOAD_FAILED")

    def test_faster_whisper_non_cache_load_failure_is_not_missing_weight(self):
        from mediascribe.transcribers import faster_whisper as fw

        class FakeWhisperModel:
            def __init__(self, *a, **kw):
                raise RuntimeError("corrupted model binary")

        fake_module = mock.MagicMock()
        fake_module.WhisperModel = FakeWhisperModel
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            with self.assertRaises(RuntimeError) as cm:
                fw._load_whisper_model("medium", "cpu", "int8")

        self.assertEqual(getattr(cm.exception, "code", None), "MODEL_LOAD_FAILED")


def tempfile_dir():
    import tempfile

    return tempfile.TemporaryDirectory()


if __name__ == "__main__":
    unittest.main()
