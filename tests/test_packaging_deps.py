"""P2-4(2026-11-20 架构评审)— 引擎栈转 optional extra 的打包契约。

基础依赖不再含 openai-whisper / torch / torchaudio;
``whisper`` extra 收纳引擎栈;``all`` 必须包含 whisper(安装行为与
瘦身前一致);torch 不在包导入路径上(瘦身后可纯编排层安装)。
"""

from __future__ import annotations

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent

_PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _base_dep_names():
    return [d.split(">")[0].split("<")[0].strip() for d in _PYPROJECT["project"]["dependencies"]]


class TestEngineStackIsOptional(unittest.TestCase):
    def test_base_deps_have_no_engine_stack(self):
        names = _base_dep_names()
        for banned in ("openai-whisper", "torch", "torchaudio"):
            self.assertNotIn(banned, names, f"{banned} 仍在基础依赖里(P2-4 契约被破坏)")

    def test_whisper_extra_carries_engine_stack(self):
        extras = _PYPROJECT["project"]["optional-dependencies"]
        names = [d.split(">")[0].strip() for d in extras["whisper"]]
        self.assertIn("openai-whisper", names)
        self.assertIn("torch", names)
        self.assertIn("torchaudio", names)

    def test_all_extra_includes_whisper(self):
        extras = _PYPROJECT["project"]["optional-dependencies"]
        all_spec = extras["all"][0]
        self.assertIn("whisper", all_spec, "all 必须拉 whisper(保持瘦身前安装行为)")

    def test_base_deps_still_have_downloader_stack(self):
        names = _base_dep_names()
        for required in ("yt-dlp", "playwright", "requests", "ffmpeg-python"):
            self.assertIn(required, names)


class TestWhisperMissingFriendlyError(unittest.TestCase):
    """裸编排层安装(无 whisper)时,选 whisper 引擎给出带安装指引的错误。"""

    def test_missing_whisper_gives_install_hint(self):
        from mediascribe.transcribers.whisper import WhisperTranscriber

        transcriber = WhisperTranscriber(model="tiny", device="cpu")
        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "a.wav"
            audio.write_bytes(b"RIFFfake")
            # 模拟 whisper 未安装:sys.modules 塞 None → import 即 ImportError
            with mock.patch.dict(sys.modules, {"whisper": None, "torch": None}):
                with self.assertRaises(RuntimeError) as ctx:
                    transcriber.transcribe(audio)
            self.assertIn("mediascribe[whisper]", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
