"""
whisper.cpp 引擎（``mediascribe.transcribers.whisper_cpp``）的单元测试。

重点三件事：
1. **二进制探测不能误命中系统文件** —— 实测 ``shutil.which("main.exe")`` 在
   Windows 上会返回 ``C:\\Windows\\system32\\main.CPL``（控制面板），
   曾导致引擎被误判为「可用」。
2. 缺二进制 / 缺权重时必须给出**可操作的预置指引**。
3. 转写流程：参数拼装正确、JSON 解析出 segments、简体 prompt 已注入。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mediascribe.transcribers import whisper_cpp as cpp  # noqa: E402
from mediascribe.transcribers.factory import get_transcriber  # noqa: E402
from mediascribe.zh_normalize import SIMPLIFIED_ZH_PROMPT  # noqa: E402


def setUpModule():
    from mediascribe.i18n import set_language

    set_language("zh")


def tearDownModule():
    from mediascribe.i18n import set_language

    set_language("en")


class TestBinaryDetection(unittest.TestCase):
    def test_system_main_cpl_is_rejected(self):
        """Windows 的 main.CPL 不是 whisper.cpp —— 必须被挡住。"""
        system_cpl = Path(r"C:\Windows\System32\main.CPL")
        if not system_cpl.exists():
            self.skipTest("本机无 System32/main.CPL")
        self.assertFalse(cpp._is_plausible_binary(system_cpl))

    def test_tiny_file_rejected(self):
        with mock.patch.object(Path, "is_file", return_value=True):
            with mock.patch.object(Path, "stat") as st:
                st.return_value.st_size = 1024
                self.assertFalse(cpp._is_plausible_binary(Path("x.exe")))

    def test_find_binary_none_when_absent(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(cpp.shutil, "which", return_value=None):
                with mock.patch.object(cpp, "_BIN_SEARCH_DIRS", ()):
                    self.assertIsNone(cpp.find_binary())

    def test_find_binary_respects_env(self):
        fake = Path(__file__).resolve()
        with mock.patch.dict("os.environ", {"MEDIASCRIBE_WHISPER_CPP_BIN": str(fake)}):
            with mock.patch.object(cpp, "_is_plausible_binary", return_value=True):
                self.assertEqual(cpp.find_binary(), fake)


class TestModelDetection(unittest.TestCase):
    def test_missing_model(self):
        with mock.patch.object(cpp, "models_dir", return_value=Path("/nonexistent/cpp")):
            self.assertIsNone(cpp.find_model("tiny"))

    def test_present_model(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ggml-tiny.bin").write_bytes(b"x" * 100)
            with mock.patch.object(cpp, "models_dir", return_value=Path(d)):
                found = cpp.find_model("tiny")
                self.assertIsNotNone(found)
                self.assertTrue(found.name.endswith(".bin"))

    def test_missing_vad_model(self):
        with mock.patch.object(cpp, "models_dir", return_value=Path("/nonexistent/cpp")):
            self.assertIsNone(cpp.find_vad_model())


class TestTranscriberErrors(unittest.TestCase):
    def test_missing_binary_message_has_preset_hint(self):
        t = cpp.WhisperCppTranscriber(model="tiny")
        with mock.patch.object(cpp, "find_binary", return_value=None):
            with self.assertRaises(RuntimeError) as cm:
                t._resolve_binary()
        msg = str(cm.exception)
        self.assertIn("预置", msg)
        self.assertIn("MEDIASCRIBE_WHISPER_CPP_BIN", msg)

    def test_missing_model_message_has_preset_hint(self):
        t = cpp.WhisperCppTranscriber(model="tiny")
        with mock.patch.object(cpp, "find_model", return_value=None):
            with self.assertRaises(RuntimeError) as cm:
                t._resolve_model()
        self.assertIn("ggml-tiny.bin", str(cm.exception))


class TestTranscribeFlow(unittest.TestCase):
    """用 mock 跑完整流程：参数拼装 + JSON 解析。"""

    def _run(self, language="zh", vad=False, extra=None):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            out_prefix = Path(cmd[cmd.index("-of") + 1])
            payload = {
                "transcription": [
                    {
                        "text": " 各位觀眾大家好。",
                        "offsets": {"from": 0, "to": 2500},
                    }
                ]
            }
            Path(str(out_prefix) + ".json").write_text(json.dumps(payload), encoding="utf-8")
            return mock.MagicMock(returncode=0, stdout="", stderr="")

        t = cpp.WhisperCppTranscriber(model="tiny", device="cpu", binary=Path("fake.exe"))
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"RIFF0000WAVEfmt ")
            with mock.patch.object(cpp.subprocess, "run", side_effect=fake_run):
                with mock.patch.object(cpp, "find_vad_model", return_value=None):
                    with mock.patch.object(cpp, "find_model", return_value=Path("ggml-tiny.bin")):
                        with mock.patch.object(cpp, "_to_wav_16k", return_value=True):
                            res = t.transcribe(audio, language=language)
        return captured["cmd"], res

    def test_wav_input_still_goes_through_16k_transcode(self):
        """P2 修正:.wav 也不许绕过 16k mono 转码(44.1k 立体声直录会劣化)。"""
        t = cpp.WhisperCppTranscriber(model="tiny", device="cpu", binary=Path("fake.exe"))
        called = {}
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"RIFF0000WAVEfmt ")

            def fake_transcode(src, dst):
                called["src"] = src
                return True

            def fake_run(cmd, **kwargs):
                # ffmpeg 转码与 whisper-cli 都走 subprocess.run:
                # 只给 whisper-cli(-of)写 JSON, ffmpeg 调用直接成功返回
                if "-of" in [str(x) for x in cmd]:
                    out_prefix = Path(cmd[cmd.index("-of") + 1])
                    Path(str(out_prefix) + ".json").write_text(
                        json.dumps({"transcription": []}), encoding="utf-8"
                    )
                return mock.MagicMock(returncode=0, stdout="", stderr="")

            with mock.patch.object(cpp.subprocess, "run", side_effect=fake_run):
                with mock.patch.object(cpp, "find_vad_model", return_value=None):
                    with mock.patch.object(cpp, "find_model", return_value=Path("ggml-tiny.bin")):
                        with mock.patch.object(cpp, "_to_wav_16k", side_effect=fake_transcode):
                            t.transcribe(audio, language="zh")
        self.assertEqual(called["src"], audio)

    def test_command_contains_model_and_language(self):
        cmd, res = self._run(language="zh")
        self.assertIn("-m", cmd)
        self.assertIn("-l", cmd)
        self.assertIn("zh", cmd)
        self.assertIn("-oj", cmd)

    def test_simplified_prompt_injected(self):
        cmd, _ = self._run(language="zh")
        self.assertIn("--prompt", cmd)
        self.assertIn(SIMPLIFIED_ZH_PROMPT, cmd)

    def test_no_prompt_for_english(self):
        cmd, _ = self._run(language="en")
        self.assertNotIn("--prompt", cmd)

    def test_segments_parsed(self):
        _, res = self._run(language="zh")
        self.assertEqual(len(res["segments"]), 1)
        seg = res["segments"][0]
        self.assertAlmostEqual(seg["start"], 0.0)
        self.assertAlmostEqual(seg["end"], 2.5)
        self.assertEqual(res["model"], "tiny")


class TestFactoryIntegration(unittest.TestCase):
    def test_whisper_cpp_is_valid_engine(self):
        t = get_transcriber("whisper-cpp", model="tiny", device="cpu")
        self.assertEqual(t.name, "whisper-cpp")

    def test_unknown_engine_message_lists_whisper_cpp(self):
        with self.assertRaises(ValueError) as cm:
            get_transcriber("nope")
        self.assertIn("whisper-cpp", str(cm.exception))

    # 2026-10-29: factory 的内存压力回退链已删（产品零调用，且与
    # runtime_guard 的总内存口径矛盾）—— 引擎切换决策单点在 runtime_guard,
    # 行为由 tests/test_runtime_guard.py 钉死。


class TestVadDefaultAndDllDetection(unittest.TestCase):
    """VAD 默认关闭（实测：干净音频上 VAD 反而恶化）+ 运行时 DLL 缺失检测。"""

    def test_vad_defaults_to_off(self):
        """实测依据：干净中文 vad=False 7.38% vs vad=True 19.67%。"""
        t = cpp.WhisperCppTranscriber(model="tiny")
        self.assertFalse(t.vad, msg="VAD 应默认关闭（管线层已有 VAD，引擎层重复处理会恶化）")

    def test_missing_runtime_dlls_detected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            binary = Path(d) / "whisper-cli.exe"
            binary.write_bytes(b"x" * 200000)
            (Path(d) / "ggml-base.dll").write_bytes(b"x" * 100)
            missing = cpp.missing_runtime_dlls(binary)
            self.assertIn("whisper.dll", missing)

    def test_no_dlls_means_static_build(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            binary = Path(d) / "whisper-cli.exe"
            binary.write_bytes(b"x" * 200000)
            self.assertEqual(cpp.missing_runtime_dlls(binary), [])

    def test_binary_error_mentions_dll(self):
        t = cpp.WhisperCppTranscriber(model="tiny")
        with mock.patch.object(cpp, "find_binary", return_value=None):
            with self.assertRaises(RuntimeError) as cm:
                t._resolve_binary()
        self.assertIn("DLL", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
