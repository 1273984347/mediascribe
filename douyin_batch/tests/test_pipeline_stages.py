"""
Pipeline stage framework (v3.2.0b) — unit tests.

目标:

1. 5 个 :class:`Stage` 子类 (``ParseSourceStage`` /
   ``DownloadStage`` / ``ExtractAudioStage`` / ``TranscribeStage`` /
   ``AssembleStage``) 在 mock 下各跑一遍,确认 ``should_run`` /
   ``run`` / ``run_with_progress`` 行为正确。
2. :func:`default_chain` 顺序与每个 stage 的 ``name`` 字段。
3. ``PipelineContext`` 的 ``emit_progress`` / ``check_cancel`` /
   ``cancel_event`` 状态机。
4. ``Pipeline.transcribe`` 走 stage chain 后,返回值与 v3.2.0a
   ``TranscriptResult`` 完全兼容(用本地音频 fixture 跑全链)。
5. ``resolve_device`` / ``gpu_health`` 在 torch 不可用时安全 fallback。
"""

from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from mediascribe.config import Settings
from mediascribe.downloaders.base import DownloadResult
from mediascribe.inputs import parse_source
from mediascribe.models import SourceRef
from mediascribe.pipeline import Pipeline, gpu_health, resolve_device
from mediascribe.pipeline_stages import (
    URL_KINDS,
    VIDEO_KINDS,
    AssembleStage,
    DownloadStage,
    ExtractAudioStage,
    ParseSourceStage,
    PipelineCancelled,
    PipelineContext,
    Stage,
    TranscribeStage,
    default_chain,
)
from mediascribe.transcribers import Transcriber  # noqa: F401


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _fake_settings(tmp: Path) -> Settings:
    return Settings(workspace_root=tmp)


def _audio_fake_source(tmp: Path) -> SourceRef:
    """构造一个 ``kind=audio`` 的本地 :class:`SourceRef``,跳过下载。"""
    # 始终把真实文件写到 mkdtemp 出来的目录,避开 Windows 沙箱对
    # ``D:\\tmp`` 的写入限制。``tmp`` 参数仍保留签名兼容性,但不直接写。
    import tempfile

    safe = Path(tempfile.mkdtemp(prefix="v2t-stage-"))
    p = safe / "hello.wav"
    p.write_bytes(b"RIFFfake")
    return SourceRef(
        raw_input=str(p),
        kind="audio",
        url=None,
        path=p,
        bv=None,
    )


def _url_fake_source() -> SourceRef:
    return SourceRef(
        raw_input="https://example.com/v",
        kind="youtube",
        url="https://example.com/v",
        path=None,
        bv=None,
    )


def _fake_transcriber(name: str = "fake") -> mock.MagicMock:
    t = mock.MagicMock(name=f"Transcriber<{name}>")
    t.name = name
    t.transcribe.return_value = {
        "text": "hello world",
        "model": "fake-tiny",
        "language": "en",
        "segments": [],
        "speaker_diarization": False,
    }
    return t


# ---------------------------------------------------------------------------
# PipelineContext
# ---------------------------------------------------------------------------
class TestPipelineContext(unittest.TestCase):
    def test_emit_progress_clamps_to_0_100(self):
        ctx = PipelineContext(
            settings=_fake_settings(Path("/tmp")),
            source_input="x",
        )
        seen: list[tuple[str, float]] = []

        def cb(stage, pct):
            seen.append((stage, pct))

        ctx.progress_cb = cb
        ctx.emit_progress("download", -5)
        ctx.emit_progress("transcribe", 250)
        ctx.emit_progress("assemble", 42)
        self.assertEqual(seen, [("download", 0.0), ("transcribe", 100.0), ("assemble", 42.0)])

    def test_emit_progress_swallows_exceptions(self):
        ctx = PipelineContext(
            settings=_fake_settings(Path("/tmp")),
            source_input="x",
        )
        ctx.progress_cb = lambda *_: (_ for _ in ()).throw(RuntimeError("boom"))
        # 必须不抛
        ctx.emit_progress("download", 10)

    def test_check_cancel_returns_false_by_default(self):
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        self.assertFalse(ctx.check_cancel())

    def test_check_cancel_returns_true_when_event_set(self):
        ctx = PipelineContext(
            settings=_fake_settings(Path("/tmp")),
            source_input="x",
        )
        ev = threading.Event()
        ev.set()
        ctx.cancel_event = ev
        self.assertTrue(ctx.check_cancel())


# ---------------------------------------------------------------------------
# ParseSourceStage
# ---------------------------------------------------------------------------
class TestParseSourceStage(unittest.TestCase):
    def test_should_run_when_source_is_none(self):
        s = ParseSourceStage()
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        self.assertTrue(s.should_run(ctx))
        ctx.source = parse_source("https://example.com")
        self.assertFalse(s.should_run(ctx))

    def test_run_sets_source(self):
        s = ParseSourceStage()
        ctx = PipelineContext(
            settings=_fake_settings(Path("/tmp")),
            source_input="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        ctx = s.run(ctx)
        self.assertIsInstance(ctx.source, SourceRef)
        self.assertEqual(ctx.source.kind, "youtube")


# ---------------------------------------------------------------------------
# DownloadStage
# ---------------------------------------------------------------------------
class TestDownloadStage(unittest.TestCase):
    def test_should_run_skips_audio_source(self):
        s = DownloadStage(downloader=None)
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        ctx.source = _audio_fake_source(Path("/tmp"))
        self.assertFalse(s.should_run(ctx))

    def test_should_run_skips_when_source_is_none(self):
        s = DownloadStage(downloader=None)
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        self.assertFalse(s.should_run(ctx))

    def test_should_run_runs_for_url_kinds(self):
        s = DownloadStage(downloader=None)
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        ctx.source = _url_fake_source()
        self.assertTrue(s.should_run(ctx))

    def test_run_uses_static_downloader(self):
        fake_dl = mock.MagicMock(name="StaticDownloader")
        fake_dl.download.return_value = DownloadResult(
            source=_url_fake_source(),
            video_path=Path("/tmp/v.mp4"),
            title="from-static",
            metadata={"uploader": "x"},
        )
        s = DownloadStage(downloader=fake_dl)
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        ctx.source = _url_fake_source()
        ctx = s.run(ctx)
        fake_dl.download.assert_called_once()
        self.assertEqual(ctx.base_name, "from-static")
        self.assertEqual(ctx.video_path, Path("/tmp/v.mp4"))


# ---------------------------------------------------------------------------
# ExtractAudioStage
# ---------------------------------------------------------------------------
class TestExtractAudioStage(unittest.TestCase):
    def test_should_run_skips_audio_source(self):
        """audio 源走 ExtractAudioStage.short-circuit(把 source.path 当
        audio_path),不再被完全 skip。"""
        s = ExtractAudioStage()
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        ctx.source = _audio_fake_source(Path("/tmp"))
        self.assertTrue(s.should_run(ctx))

    def test_should_run_runs_for_video_kinds(self):
        s = ExtractAudioStage()
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        ctx.source = _url_fake_source()
        ctx.video_path = Path("/tmp/v.mp4")
        self.assertTrue(s.should_run(ctx))

    def test_run_extracts_audio(self):
        s = ExtractAudioStage()
        ctx = PipelineContext(
            settings=_fake_settings(Path("/tmp")),
            source_input="x",
        )
        ctx.source = _url_fake_source()
        ctx.video_path = Path("/tmp/v.mp4")
        ctx.base_name = "yt-test"
        with mock.patch(
            "mediascribe.pipeline_stages.extract_audio",
            return_value=Path("/tmp/audio/yt-test.wav"),
        ) as ea:
            ctx = s.run(ctx)
        ea.assert_called_once()
        self.assertEqual(ctx.audio_path, Path("/tmp/audio/yt-test.wav"))


# ---------------------------------------------------------------------------
# TranscribeStage
# ---------------------------------------------------------------------------
class TestTranscribeStage(unittest.TestCase):
    def test_should_run_requires_audio_path(self):
        s = TranscribeStage(transcriber=_fake_transcriber())
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        self.assertFalse(s.should_run(ctx))

    def test_run_invokes_transcriber(self):
        t = _fake_transcriber()
        s = TranscribeStage(transcriber=t)
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        ctx.audio_path = Path("/tmp/a.wav")
        ctx = s.run(ctx)
        t.transcribe.assert_called_once()
        self.assertEqual(ctx.text, "hello world")
        self.assertEqual(ctx.transcription["model"], "fake-tiny")

    def test_run_raises_when_text_empty(self):
        t = _fake_transcriber()
        t.transcribe.return_value = {"text": "  "}
        s = TranscribeStage(transcriber=t)
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        ctx.audio_path = Path("/tmp/a.wav")
        with self.assertRaises(RuntimeError):
            s.run(ctx)


# ---------------------------------------------------------------------------
# AssembleStage
# ---------------------------------------------------------------------------
class TestAssembleStage(unittest.TestCase):
    def _ctx(self) -> PipelineContext:
        ctx = PipelineContext(
            settings=_fake_settings(Path("/tmp")),
            source_input="https://example.com/v",
        )
        ctx.source = _url_fake_source()
        ctx.transcription = {
            "text": "hello world",
            "model": "tiny",
            "language": "en",
            "segments": [],
            "speaker_diarization": False,
        }
        ctx.text = "hello world"
        ctx.audio_path = Path("/tmp/a.wav")
        ctx.video_path = Path("/tmp/v.mp4")
        ctx.base_name = "yt-test"
        return ctx

    def _stage(self) -> AssembleStage:
        return AssembleStage(
            resolve_output_path=lambda name, out: Path(f"{name}.md"),
            resolve_metadata_path=lambda tp: tp.with_suffix(".json"),
            build_markdown=lambda *a, **kw: "# md\n",
        )

    def test_should_run_requires_text(self):
        s = self._stage()
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        self.assertFalse(s.should_run(ctx))
        ctx.text = "x"
        self.assertTrue(s.should_run(ctx))

    def test_run_writes_files_and_result(self):
        s = self._stage()
        ctx = self._ctx()
        # v3.2.0e+: AssembleStage 改走 _atomic_write_text 而非 Path.write_text,
        # 测试契约相应更新。mock 该 helper 验证落盘调用。
        with mock.patch("mediascribe.pipeline_stages._atomic_write_text") as wt:
            ctx = s.run(ctx)
        self.assertIsNotNone(ctx.transcript_path)
        self.assertIsNotNone(ctx.metadata_path)
        self.assertIsNotNone(ctx.result)
        self.assertEqual(ctx.result.text, "hello world")
        self.assertEqual(ctx.result.engine, "unknown")  # ctx 没设 engine_name
        # metadata 是 dict
        self.assertIn("engine", ctx.metadata)
        self.assertEqual(ctx.metadata["language"], "en")
        wt.assert_called()  # _atomic_write_text 被调用

    def test_run_uses_engine_name_from_ctx(self):
        s = self._stage()
        ctx = self._ctx()
        ctx.engine_name = "whisperx"
        with mock.patch("mediascribe.pipeline_stages._atomic_write_text"):
            ctx = s.run(ctx)
        self.assertEqual(ctx.result.engine, "whisperx")
        self.assertEqual(ctx.metadata["engine"], "whisperx")


# ---------------------------------------------------------------------------
# default_chain
# ---------------------------------------------------------------------------
class TestDefaultChain(unittest.TestCase):
    def test_default_chain_order(self):
        chain = default_chain(
            transcriber=_fake_transcriber(),
            downloader=None,
            downloader_getter=None,
            resolve_output_path=lambda *a, **kw: Path("/tmp/x.md"),
            resolve_metadata_path=lambda p: p.with_suffix(".json"),
            build_markdown=lambda *a, **kw: "# md\n",
        )
        names = [s.name for s in chain]
        self.assertEqual(
            names,
            ["parse", "download", "extract_audio", "transcribe", "assemble"],
        )
        # 每个都是 Stage 子类
        for s in chain:
            self.assertIsInstance(s, Stage)


# ---------------------------------------------------------------------------
# Stage.run_with_progress
# ---------------------------------------------------------------------------
class TestRunWithProgress(unittest.TestCase):
    def test_emits_zero_then_hundred(self):
        seen: list[tuple[str, float]] = []

        class EchoStage(Stage):
            name = "echo"

            def should_run(self, ctx):
                return True

            def run(self, ctx):
                return ctx

        s = EchoStage()
        ctx = PipelineContext(
            settings=_fake_settings(Path("/tmp")),
            source_input="x",
            progress_cb=lambda stage, pct: seen.append((stage, pct)),
        )
        s.run_with_progress(ctx)
        self.assertEqual(seen, [("echo", 0.0), ("echo", 100.0)])

    def test_emits_hundred_even_when_run_raises(self):
        seen: list[tuple[str, float]] = []

        class BrokenStage(Stage):
            name = "broken"

            def should_run(self, ctx):
                return True

            def run(self, ctx):
                raise RuntimeError("boom")

        s = BrokenStage()
        ctx = PipelineContext(
            settings=_fake_settings(Path("/tmp")),
            source_input="x",
            progress_cb=lambda stage, pct: seen.append((stage, pct)),
        )
        with self.assertRaises(RuntimeError):
            s.run_with_progress(ctx)
        self.assertEqual(seen, [("broken", 0.0), ("broken", 100.0)])


# ---------------------------------------------------------------------------
# Pipeline.transcribe 走 stage chain
# ---------------------------------------------------------------------------
class TestPipelineTranscribeUsesChain(unittest.TestCase):
    def test_transcribe_local_audio_end_to_end(self):
        """本地 wav 走完整 chain,result 应该是 TranscriptResult。"""
        tmp = Path(self._temp_dir())
        try:
            settings = _fake_settings(tmp)
            transcriber = _fake_transcriber()
            pipeline = Pipeline(settings=settings, transcriber=transcriber)

            audio = tmp / "hello.wav"
            audio.write_bytes(b"RIFFfake")
            result = pipeline.transcribe(str(audio))

            self.assertEqual(result.text, "hello world")
            self.assertEqual(result.engine, "fake")
            self.assertIsNotNone(result.transcript_path)
            self.assertTrue(result.transcript_path.exists())
            self.assertTrue(result.metadata_path.exists())
            # metadata JSON parse 成功
            data = json.loads(result.metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(data["engine"], "fake")
            self.assertEqual(data["model"], "fake-tiny")
        finally:
            self._cleanup(tmp)

    def test_transcribe_progress_callback_fires(self):
        """stage chain 跑通后,progress_cb 至少被 emit 多次
        (extract_audio / transcribe / assemble 各两次: 0% + 100%)。
        """
        tmp = Path(self._temp_dir())
        try:
            settings = _fake_settings(tmp)
            transcriber = _fake_transcriber()
            pipeline = Pipeline(settings=settings, transcriber=transcriber)

            audio = tmp / "hello.wav"
            audio.write_bytes(b"RIFFfake")
            # 直接走 _run_stage_chain(不通过 Pipeline.transcribe 公共入口)
            seen: list[tuple[str, float]] = []
            ctx = PipelineContext(settings=settings, source_input=str(audio))
            ctx.source = parse_source(str(audio))
            ctx.engine_name = "fake"
            ctx.progress_cb = lambda s, p: seen.append((s, p))
            pipeline._run_stage_chain(ctx)
            names = [n for n, _ in seen]
            self.assertIn("extract_audio", names)
            self.assertIn("transcribe", names)
            self.assertIn("assemble", names)
            for stage in ("extract_audio", "transcribe", "assemble"):
                pairs = [p for n, p in seen if n == stage]
                self.assertIn(0.0, pairs)
                self.assertIn(100.0, pairs)
        finally:
            self._cleanup(tmp)

    def _temp_dir(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="v2t-stage-")

    def _cleanup(self, tmp: Path) -> None:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# P1-1 — _create_transcriber 必须先 resolve_device
# ---------------------------------------------------------------------------
class TestCreateTranscriberResolvesDevice(unittest.TestCase):
    """``"auto"`` 不能原样透传给引擎: faster-whisper 拿不到
    int8_float16(CUDA 加速静默失效),whisperx 把 "auto" 传进
    ``.to(device)`` 直接崩。"""

    def _torch_cuda(self):
        fake_torch = mock.MagicMock()
        fake_torch.cuda.is_available.return_value = True
        return fake_torch

    def test_faster_whisper_gets_resolved_device(self):
        with mock.patch.dict("sys.modules", {"torch": self._torch_cuda()}), mock.patch(
            "mediascribe.pipeline.FasterWhisperTranscriber"
        ) as fwt:
            fwt.return_value = "SENTINEL"
            s = _fake_settings(Path("/tmp"))
            s.engine = "faster-whisper"
            s.device = "auto"
            t = Pipeline(settings=s, transcriber=None)._create_transcriber(s)
            self.assertEqual(t, "SENTINEL")
            self.assertEqual(fwt.call_args.kwargs["device"], "cuda")

    def test_whisperx_gets_resolved_device_not_auto(self):
        with mock.patch.dict("sys.modules", {"torch": self._torch_cuda()}), mock.patch(
            "mediascribe.pipeline.WhisperXTranscriber"
        ) as wx:
            wx.return_value = "SENTINEL"
            s = _fake_settings(Path("/tmp"))
            s.engine = "whisperx"
            s.device = "auto"
            Pipeline(settings=s, transcriber=None)._create_transcriber(s)
            self.assertEqual(wx.call_args.kwargs["device"], "cuda")

    def test_explicit_device_passthrough(self):
        with mock.patch.dict("sys.modules", {"torch": self._torch_cuda()}), mock.patch(
            "mediascribe.pipeline.WhisperTranscriber"
        ) as wt:
            wt.return_value = "SENTINEL"
            s = _fake_settings(Path("/tmp"))
            s.device = "cuda:0"
            Pipeline(settings=s, transcriber=None)._create_transcriber(s)
            self.assertEqual(wt.call_args.kwargs["device"], "cuda:0")

    def test_engine_none_device_resolves(self):
        """device=None 也走归一化(torch 不可用 → cpu)。"""
        with mock.patch.dict("sys.modules", {"torch": None}), mock.patch(
            "mediascribe.pipeline.WhisperTranscriber"
        ) as wt:
            wt.return_value = "SENTINEL"
            s = _fake_settings(Path("/tmp"))
            s.device = None
            Pipeline(settings=s, transcriber=None)._create_transcriber(s)
            self.assertEqual(wt.call_args.kwargs["device"], "cpu")


# ---------------------------------------------------------------------------
# resolve_device / gpu_health
# ---------------------------------------------------------------------------
class TestResolveDevice(unittest.TestCase):
    def test_explicit_device_returned_as_is(self):
        self.assertEqual(resolve_device("cpu"), "cpu")
        self.assertEqual(resolve_device("cuda:0"), "cuda:0")
        self.assertEqual(resolve_device("metal"), "metal")

    def test_auto_falls_back_to_cpu_when_torch_missing(self):
        with mock.patch.dict("sys.modules", {"torch": None}):
            self.assertEqual(resolve_device("auto"), "cpu")

    def test_auto_returns_cuda_when_torch_says_so(self):
        fake_torch = mock.MagicMock()
        fake_torch.cuda.is_available.return_value = True
        with mock.patch.dict("sys.modules", {"torch": fake_torch}):
            self.assertEqual(resolve_device("auto"), "cuda")

    def test_auto_returns_metal_when_mps_available(self):
        fake_torch = mock.MagicMock()
        fake_torch.cuda.is_available.return_value = False
        fake_torch.backends.mps.is_available.return_value = True
        with mock.patch.dict("sys.modules", {"torch": fake_torch}):
            self.assertEqual(resolve_device("auto"), "metal")

    def test_auto_returns_rocm_when_hip_version_present(self):
        fake_torch = mock.MagicMock()
        fake_torch.cuda.is_available.return_value = False
        fake_torch.backends.mps.is_available.return_value = False
        # torch.version.hip = "5.6" 模拟 ROCm
        with mock.patch.dict("sys.modules", {"torch": fake_torch}):
            self.assertEqual(resolve_device(None), "rocm")


class TestGpuHealth(unittest.TestCase):
    def test_no_torch_returns_default_cpu_info(self):
        with mock.patch.dict("sys.modules", {"torch": None}):
            info = gpu_health()
        self.assertEqual(info["device"], "cpu")
        self.assertFalse(info["available"])

    def test_with_torch_no_cuda_returns_cpu(self):
        fake_torch = mock.MagicMock()
        fake_torch.cuda.is_available.return_value = False
        fake_torch.backends.mps.is_available.return_value = False
        with mock.patch.dict("sys.modules", {"torch": fake_torch}):
            info = gpu_health()
        self.assertFalse(info["available"])
        self.assertEqual(info["device"], "cpu")

    def test_with_cuda_returns_full_info(self):
        fake_torch = mock.MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.current_device.return_value = 0
        fake_props = mock.MagicMock()
        fake_props.name = "RTX 4090"
        fake_torch.cuda.get_device_properties.return_value = fake_props
        fake_torch.cuda.mem_get_info.return_value = (1024 * 1024 * 1024, 4 * 1024 * 1024 * 1024)
        with mock.patch.dict("sys.modules", {"torch": fake_torch}):
            info = gpu_health()
        self.assertTrue(info["available"])
        self.assertEqual(info["device"], "cuda")
        self.assertEqual(info["name"], "RTX 4090")
        self.assertEqual(info["vram_total_mb"], 4096)
        self.assertEqual(info["vram_used_mb"], 3072)


# ---------------------------------------------------------------------------
# Kind 白名单 sanity
# ---------------------------------------------------------------------------
class TestKindWhitelists(unittest.TestCase):
    def test_url_kinds_includes_main_platforms(self):
        for k in ("bilibili", "douyin", "tiktok", "youtube", "xiaohongshu"):
            self.assertIn(k, URL_KINDS)

    def test_video_kinds_is_superset_of_url_kinds(self):
        self.assertTrue(URL_KINDS.issubset(VIDEO_KINDS))
        self.assertIn("video", VIDEO_KINDS)


# ---------------------------------------------------------------------------
# v3.2.0e 取消机制 — raise_if_cancelled + run_with_progress cancel 语义
# ---------------------------------------------------------------------------
class TestCancelMechanism(unittest.TestCase):
    """锁定 v3.2.0e 取消语义,防止回归。

    契约:
    * ``raise_if_cancelled`` 在 event 置位时抛 ``PipelineCancelled``,
      未置位 / 未设 ``cancel_event`` 时为 no-op。
    * ``run_with_progress`` 收到 ``PipelineCancelled`` 时跳过 100% emit
      (取消 = 显式未完成,只 emit 0.0 起始);普通 ``Exception`` 仍 emit 100%
      (避免前端进度条卡 99%)。
    """

    def _ctx_with_cancel(self) -> tuple[PipelineContext, threading.Event]:
        ctx = PipelineContext(
            settings=_fake_settings(Path("/tmp")),
            source_input="x",
        )
        ev = threading.Event()
        ctx.cancel_event = ev
        return ctx, ev

    # -- raise_if_cancelled -------------------------------------------------
    def test_raise_if_cancelled_noop_when_event_unset(self):
        ctx, ev = self._ctx_with_cancel()
        ev.clear()  # 未置位
        ctx.raise_if_cancelled()  # 必须不抛

    def test_raise_if_cancelled_noop_when_event_none(self):
        ctx = PipelineContext(settings=_fake_settings(Path("/tmp")), source_input="x")
        # cancel_event 默认 None
        ctx.raise_if_cancelled()  # 必须不抛

    def test_raise_if_cancelled_raises_when_event_set(self):
        ctx, ev = self._ctx_with_cancel()
        ev.set()
        with self.assertRaises(PipelineCancelled):
            ctx.raise_if_cancelled()

    # -- run_with_progress cancel 语义 -------------------------------------
    def test_run_with_progress_skips_hundred_on_cancel(self):
        """``PipelineCancelled`` 时只 emit 0.0(开始),跳过 100%(完成)。

        取消态由 ``JobProgress.cancelled`` 事件单独通知前端,
        per-stage 进度值不携带取消信号(``emit_progress`` 会把
        负值 clamp 到 0,所以用 -1.0 不可行)。
        """
        seen: list[tuple[str, float]] = []

        class CancelStage(Stage):
            name = "cancel_me"

            def should_run(self, ctx):
                return True

            def run(self, ctx):
                # 模拟 stage 在阻塞点检测到取消
                if ctx.check_cancel():
                    raise PipelineCancelled()
                return ctx

        ctx, ev = self._ctx_with_cancel()
        ev.set()  # 取消已请求
        ctx.progress_cb = lambda s, p: seen.append((s, p))
        s = CancelStage()
        with self.assertRaises(PipelineCancelled):
            s.run_with_progress(ctx)
        # 只 emit 0.0(开始),不 emit 100%(取消 = 跳过完成信号)
        self.assertEqual(seen, [("cancel_me", 0.0)])

    def test_run_with_progress_still_emits_hundred_on_runtime_error(self):
        """回归保护:普通异常仍 emit 100%(锁定既有契约不被 cancel 逻辑破坏)。"""
        seen: list[tuple[str, float]] = []

        class BrokenStage(Stage):
            name = "broken"

            def should_run(self, ctx):
                return True

            def run(self, ctx):
                raise RuntimeError("boom")

        ctx, _ = self._ctx_with_cancel()  # cancel_event 存在但未置位
        ctx.progress_cb = lambda s, p: seen.append((s, p))
        with self.assertRaises(RuntimeError):
            Stage.run_with_progress(BrokenStage(), ctx)
        self.assertEqual(seen, [("broken", 0.0), ("broken", 100.0)])

    # -- 端到端:stage run 在取消时抛 PipelineCancelled ----------------------
    def test_transcribe_stage_raises_cancel_before_asr(self):
        """TranscribeStage.run 在 ASR 前检测到取消 → 抛 PipelineCancelled。"""
        t = _fake_transcriber()
        s = TranscribeStage(transcriber=t)
        ctx, ev = self._ctx_with_cancel()
        ev.set()
        ctx.audio_path = Path("/tmp/a.wav")
        with self.assertRaises(PipelineCancelled):
            s.run(ctx)
        t.transcribe.assert_not_called()  # ASR 未被触发

    def test_download_stage_raises_cancel_before_network(self):
        """DownloadStage.run 在下载前检测到取消 → 抛 PipelineCancelled。"""
        fake_dl = mock.MagicMock(name="DL")
        s = DownloadStage(downloader=fake_dl)
        ctx, ev = self._ctx_with_cancel()
        ev.set()
        ctx.source = _url_fake_source()
        with self.assertRaises(PipelineCancelled):
            s.run(ctx)
        fake_dl.download.assert_not_called()  # 网络未被触发

    def test_extract_audio_stage_raises_cancel_before_ffmpeg(self):
        """ExtractAudioStage.run 在 ffmpeg 前检测到取消 → 抛 PipelineCancelled。"""
        s = ExtractAudioStage()
        ctx, ev = self._ctx_with_cancel()
        ev.set()
        ctx.source = _url_fake_source()  # kind="youtube"
        ctx.video_path = Path("/tmp/v.mp4")
        with mock.patch("mediascribe.pipeline_stages.extract_audio") as fake_extract:
            with self.assertRaises(PipelineCancelled):
                s.run(ctx)
            fake_extract.assert_not_called()

    def test_assemble_stage_raises_cancel_before_post_process(self):
        """AssembleStage.run 在落盘前检测到取消 → 抛 PipelineCancelled。"""
        s = AssembleStage(
            resolve_output_path=lambda name, out: Path("/tmp/out.md"),
            resolve_metadata_path=lambda p: Path("/tmp/out.json"),
            build_markdown=lambda *a, **kw: "",
        )
        ctx, ev = self._ctx_with_cancel()
        ev.set()
        ctx.source = _url_fake_source()
        ctx.text = "hello"
        ctx.transcription = {"text": "hello"}
        with mock.patch.object(s, "_post_process") as fake_pp:
            with self.assertRaises(PipelineCancelled):
                s.run(ctx)
            fake_pp.assert_not_called()


# ---------------------------------------------------------------------------
# _atomic_write_text helper (F-16)
# ---------------------------------------------------------------------------
class TestAtomicWriteText(unittest.TestCase):
    """v3.2.0e+: 验证 ``_atomic_write_text`` helper 的核心契约。

    覆盖: tmp 名含 PID + UUID8 (避免并发碰撞) / 写失败时清理 tmp /
    原子替换语义 (不存在半写文件) / parent 自动创建。
    """

    def test_writes_content_to_target(self):
        import tempfile

        from mediascribe.pipeline_stages import _atomic_write_text

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.md"
            _atomic_write_text(p, "hello world", encoding="utf-8")
            self.assertEqual(p.read_text(encoding="utf-8"), "hello world")

    def test_creates_parent_dir(self):
        import tempfile

        from mediascribe.pipeline_stages import _atomic_write_text

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "deep" / "nested" / "out.md"
            _atomic_write_text(p, "x")
            self.assertTrue(p.exists())
            self.assertEqual(p.read_text(encoding="utf-8"), "x")

    def test_no_tmp_left_after_success(self):
        import tempfile

        from mediascribe.pipeline_stages import _atomic_write_text

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.md"
            _atomic_write_text(p, "x")
            tmp_files = list(Path(d).glob("*.tmp"))
            self.assertEqual(tmp_files, [], f"残留 tmp 文件: {tmp_files}")

    def test_no_tmp_left_after_failure(self):
        """写失败 (mock write_text 抛异常) 时 tmp 必须被清理。"""
        import tempfile

        from mediascribe.pipeline_stages import _atomic_write_text

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.md"
            # 拦截 os.replace 抛异常模拟"原子替换失败"
            with mock.patch(
                "mediascribe.pipeline_stages.os.replace", side_effect=OSError("disk full")
            ):
                with self.assertRaises(OSError):
                    _atomic_write_text(p, "x")
            tmp_files = list(Path(d).glob("*.tmp"))
            self.assertEqual(tmp_files, [], f"失败后残留 tmp: {tmp_files}")

    def test_replaces_existing_file_atomically(self):
        import tempfile

        from mediascribe.pipeline_stages import _atomic_write_text

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.md"
            p.write_text("OLD", encoding="utf-8")
            _atomic_write_text(p, "NEW", encoding="utf-8")
            self.assertEqual(p.read_text(encoding="utf-8"), "NEW")


if __name__ == "__main__":
    unittest.main()
