"""mediascribe.verify 单元测试 — 第三采样验证(2026-10-08 复盘产出)。

全部离线: 转录器注入 fake, 音频用假路径, 不碰 GPU/网络。
"""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mediascribe import verify as V
from mediascribe.verify import (
    _ThirdTextIndex,
    build_verify_report,
    collect_evidence,
    ensure_audio,
    evidence_for,
    extract_transcript_body,
    locate_artifacts,
    run_verify,
)


def _make_workspace(tmp: Path, stem: str = "douyin_test") -> Path:
    """搭一个最小工作区: transcripts/<stem>.md + metadata/<stem>.json + 音频。"""
    (tmp / "transcripts").mkdir(parents=True, exist_ok=True)
    (tmp / "metadata").mkdir(parents=True, exist_ok=True)
    (tmp / "audio").mkdir(parents=True, exist_ok=True)
    transcript = tmp / "transcripts" / f"{stem}.md"
    transcript.write_text(
        "<!-- post-process: raw -->\n\n"
        f"# {stem}\n\n## 基本信息\n\n- 平台: 抖音\n\n## 转录内容\n\n"
        "意识到自己不重要的时候,你会悄悄退场吗?\n\n第二段正文。\n",
        encoding="utf-8",
    )
    audio = tmp / "audio" / f"{stem}.wav"
    audio.write_bytes(b"RIFF....")
    meta = {
        "audio_path": str(audio),
        "model": "large-v3",
        "source": {
            "raw_input": "https://v.douyin.com/abc/",
            "url": "https://www.douyin.com/video/123",
        },
    }
    (tmp / "metadata" / f"{stem}.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return transcript


class TestExtractTranscriptBody(unittest.TestCase):
    def test_standard_raw_layout(self):
        text = (
            "<!-- comment -->\n# title\n\n## 基本信息\n\n- 平台: 抖音\n\n"
            "## 转录内容\n\n第一段。\n\n第二段。\n"
        )
        body = extract_transcript_body(text)
        self.assertIn("第一段。", body)
        self.assertIn("第二段。", body)
        self.assertNotIn("平台", body)
        self.assertNotIn("comment", body)

    def test_review_doc_stops_at_notes(self):
        text = (
            "# 转录稿（人工审校版）\n\n## 转录内容\n\n正文句子。\n\n"
            "### 审校说明（修正要点与存疑项）\n\n- 存疑备注不应进入正文\n"
        )
        body = extract_transcript_body(text)
        self.assertIn("正文句子。", body)
        self.assertNotIn("存疑备注", body)


class TestLocateArtifacts(unittest.TestCase):
    def test_locate_in_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            transcript = _make_workspace(tmp)
            art = locate_artifacts(transcript, tmp)
            self.assertTrue(art.metadata_path.exists())
            self.assertEqual(art.metadata.get("model"), "large-v3")
            self.assertIsNotNone(art.audio_path)
            self.assertIn("douyin.com/video/123", art.source_url)

    def test_missing_metadata_raises(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "transcripts").mkdir()
            orphan = tmp / "transcripts" / "nope.md"
            orphan.write_text("x", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                locate_artifacts(orphan, tmp)


class TestEnsureAudio(unittest.TestCase):
    def _settings(self, tmp: Path) -> types.SimpleNamespace:
        return types.SimpleNamespace(audio_dir=tmp / "audio", downloads_dir=tmp / "downloads")

    def test_reuse_existing(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            transcript = _make_workspace(tmp)
            art = locate_artifacts(transcript, tmp)
            path, reused = ensure_audio(art, self._settings(tmp))
            self.assertTrue(reused)
            self.assertTrue(path.is_file())

    def test_redownload_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            transcript = _make_workspace(tmp)
            art = locate_artifacts(transcript, tmp)
            art.audio_path.unlink()

            downloaded = types.SimpleNamespace(video_path=tmp / "downloads" / "v.mp4")
            (tmp / "downloads").mkdir(exist_ok=True)
            downloaded.video_path.write_bytes(b"mp4")

            def fake_download(source, settings):
                self.assertIn("douyin.com", source.url)
                return downloaded

            extracted = {}

            def fake_extract(video, audio_dir, stem):
                extracted["stem"] = stem
                out = audio_dir / "new.wav"
                out.write_bytes(b"wav")
                return out

            with (
                mock.patch.object(
                    V,
                    "_smart_pick_downloader",
                    return_value=types.SimpleNamespace(download=fake_download),
                ),
                mock.patch.object(V, "extract_audio", side_effect=fake_extract),
            ):
                path, reused = ensure_audio(art, self._settings(tmp))
            self.assertFalse(reused)
            self.assertEqual(extracted["stem"], transcript.stem)
            self.assertEqual(path.name, "new.wav")

    def test_no_url_no_audio_raises(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            transcript = _make_workspace(tmp)
            art = locate_artifacts(transcript, tmp)
            art.audio_path.unlink()
            art.source_url = None
            with self.assertRaises(RuntimeError):
                ensure_audio(art, self._settings(tmp))


class TestEvidence(unittest.TestCase):
    def _index(self, *segments):
        return _ThirdTextIndex(
            [{"text": t, "start": s} for t, s in zip(segments, [0.0, 5.0, 10.0], strict=False)]
        )

    def test_same_as_main(self):
        third = self._index("我们看到这道材料很多同学的第一反应是审时度势", "后面一句", "再后面")
        verdict, window, ts = evidence_for(
            "很多同学的第一反应就是,这不是在考察审时度势吗?于是乎", "审时度势", "视读事", third
        )
        self.assertEqual(verdict, "同主稿")
        self.assertIn("审时度势", window)
        self.assertEqual(ts, "00:00")

    def test_same_as_cross(self):
        third = self._index("他在跟勾践说我要退场的时候勾践反而把他留下来了")
        verdict, window, _ = evidence_for("因为他在跟勾践说我要退场的时候", "勾践", "勾结", third)
        self.assertEqual(verdict, "同主稿")

    def test_third_reading(self):
        third = self._index("最后美美的也是戏题46分了因为各位")
        verdict, window, _ = evidence_for(
            "最后美美的也是喜提46分了因为各位这", "喜提", "嘻嘻嘻", third
        )
        self.assertEqual(verdict, "第三读")
        self.assertIn("46分", window)

    def test_not_located(self):
        third = self._index("完全不相干的内容没有任何共同词汇")
        verdict, window, ts = evidence_for("范蠡在帮助勾践卧薪尝胆", "范蠡", "范黎", third)
        self.assertEqual(verdict, "未定位")
        self.assertIsNone(window)

    def test_timestamp_mapping(self):
        third = self._index("零一二三四五六七八九", "拾壹拾贰这句子在第二段", "第三段")
        _, _, ts = evidence_for("这句子在第二段附近", "第二段", None, third)
        self.assertEqual(ts, "00:05")


class TestReport(unittest.TestCase):
    def test_collect_and_build(self):
        divergences = [
            {
                "index": 1,
                "type": "content",
                "main": "喜提",
                "cross": "嘻嘻",
                "context": "美美的也是喜提46分",
            },
            {
                "index": 2,
                "type": "content",
                "main": "范蠡",
                "cross": None,
                "context": "就像范蠡他在帮助勾践",
            },
        ]
        segments = [
            {"text": "最后美美的也是喜提46分了", "start": 0.0},
            {"text": "就像范蠡他在帮助勾践卧薪尝胆", "start": 9.0},
        ]
        evidences = collect_evidence(divergences, segments)
        self.assertEqual(evidences[0].verdict, "同主稿")
        self.assertEqual(evidences[0].timestamp, "00:00")
        self.assertEqual(evidences[1].verdict, "同主稿")

        report = build_verify_report(
            evidences,
            transcript_path=Path("douyin_x.md"),
            sample_path=Path("third-sample-douyin_x.md"),
            audio_path=Path("a.wav"),
            audio_reused=True,
            model="large-v3",
            main_model="large-v3",
            cross_model="crosscheck 对照稿",
        )
        self.assertIn("同主稿 2", report)
        self.assertIn("主「喜提」", report)
        self.assertIn("复用磁盘音频", report)
        self.assertIn("[00:00]", report)

    def test_empty_divergences_report(self):
        report = build_verify_report(
            [],
            transcript_path=Path("x.md"),
            sample_path=Path("s.md"),
            audio_path=Path("a.wav"),
            audio_reused=False,
            model="large-v3",
            main_model="large-v3",
            cross_model=None,
        )
        self.assertIn("无实词分歧清单", report)
        self.assertIn("验证轮重新下载", report)


class _FakeTranscriber:
    name = "fake"

    def __init__(self, segments):
        self._segments = segments
        self.calls = []

    def transcribe(self, audio_path, *, prompt=None, progress=None, language=None, **kwargs):
        self.calls.append(
            {"prompt": prompt, "language": language, "temperature": kwargs.get("temperature")}
        )
        return {"text": "".join(s["text"] for s in self._segments), "segments": self._segments}


class TestRunVerify(unittest.TestCase):
    def _fake_settings(self, tmp: Path) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            workspace_root=tmp, audio_dir=tmp / "audio", downloads_dir=tmp / "downloads"
        )

    def test_end_to_end_with_crosscheck(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            transcript = _make_workspace(tmp)
            (transcript.parent / f"{transcript.stem}.crosscheck.json").write_text(
                json.dumps(
                    [
                        {
                            "index": 1,
                            "type": "content",
                            "main": "悄悄退场",
                            "cross": "敲敲退场",
                            "context": "你会悄悄退场吗看到这道材料",
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            fake = _FakeTranscriber(
                [{"text": "意识到自己不重要的时候你会悄悄退场吗", "start": 0.0, "end": 3.0}]
            )
            report_path = run_verify(
                transcript,
                self._fake_settings(tmp),
                model="large-v3",
                transcriber=fake,
            )
            # 采样参数: 无 prompt + 多档温度
            self.assertIsNone(fake.calls[0]["prompt"])
            self.assertEqual(fake.calls[0]["temperature"], (0.0, 0.2, 0.4, 0.6, 0.8, 1.0))
            # 采样稿 + 验证报告落盘
            sample = transcript.parent / (f"third-sample-{transcript.stem}-largev3.md")
            self.assertTrue(sample.is_file())
            sample_text = sample.read_text(encoding="utf-8")
            self.assertIn("[00:00]", sample_text)
            self.assertIn("无 initial_prompt", sample_text)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("第三采样验证报告", report)
            self.assertIn("同主稿 1", report)
            self.assertIn("复用磁盘音频", report)

    def test_without_crosscheck_writes_sample_only(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            transcript = _make_workspace(tmp)
            fake = _FakeTranscriber([{"text": "内容", "start": 0.0}])
            report_path = run_verify(
                transcript, self._fake_settings(tmp), model="small", transcriber=fake
            )
            self.assertIn("无实词分歧清单", report_path.read_text(encoding="utf-8"))
            self.assertTrue(
                (transcript.parent / f"third-sample-{transcript.stem}-small.md").is_file()
            )

    def test_missing_transcript_raises(self):
        with self.assertRaises(FileNotFoundError):
            run_verify(Path("no_such.md"), self._fake_settings(Path(tempfile.gettempdir())))


class TestFasterWhisperLoadOrder(unittest.TestCase):
    """本地优先加载: 已缓存零网络; 缓存未命中回退联网。"""

    def test_local_first_then_online_fallback(self):
        from mediascribe.transcribers import faster_whisper as fw

        calls = []

        class FakeWM:
            def __init__(self, name, device=None, compute_type=None, local_files_only=False):
                calls.append(local_files_only)
                if local_files_only:
                    raise type("LocalEntryNotFoundError", (Exception,), {})(
                        "Cannot find an appropriate cached snapshot"
                    )

        import types as _t

        fake_mod = _t.SimpleNamespace(WhisperModel=FakeWM)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_mod}):
            model = fw._load_whisper_model("large-v3", "cpu", "int8")
        self.assertIsNotNone(model)
        self.assertEqual(calls, [True, False])  # 先本地后联网

    def test_local_hit_never_goes_online(self):
        from mediascribe.transcribers import faster_whisper as fw

        calls = []

        class FakeWM:
            def __init__(self, name, device=None, compute_type=None, local_files_only=False):
                calls.append(local_files_only)

        fake_mod = types.SimpleNamespace(WhisperModel=FakeWM)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_mod}):
            fw._load_whisper_model("large-v3", "cpu", "int8")
        self.assertEqual(calls, [True])

    def test_non_cache_miss_error_propagates(self):
        from mediascribe.transcribers import faster_whisper as fw

        class FakeWM:
            def __init__(self, *a, **k):
                raise ValueError("bad model name")

        fake_mod = types.SimpleNamespace(WhisperModel=FakeWM)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_mod}):
            with self.assertRaises(ValueError):
                fw._load_whisper_model("nope", "cpu", "int8")


class TestTemperaturePassthrough(unittest.TestCase):
    def test_temperature_forwarded_to_model(self):
        from mediascribe.transcribers.faster_whisper import FasterWhisperTranscriber

        recorded = []

        class FakeSeg:
            text = "x"
            start = 0.0
            end = 1.0

        class FakeModel:
            def transcribe(self, path, **kwargs):
                recorded.append(kwargs)

                def gen():
                    yield FakeSeg()

                return gen(), types.SimpleNamespace(language="zh")

        t = FasterWhisperTranscriber("large-v3", device="cpu")
        t._model = FakeModel()
        t.transcribe(Path("a.wav"), language="zh", temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0))
        self.assertEqual(recorded[0].get("temperature"), (0.0, 0.2, 0.4, 0.6, 0.8, 1.0))
        # 不传时不应带 temperature 键(走 faster-whisper 默认)
        t.transcribe(Path("a.wav"), language="zh")
        self.assertNotIn("temperature", recorded[1])


class TestCleanupHint(unittest.TestCase):
    """删除中间媒体前必须提示存疑复核路径(2026-10-08 复盘)。"""

    def test_hint_text_mentions_verify(self):
        from mediascribe.pipeline_stages import CLEANUP_REVIEW_HINT

        self.assertIn("verify", CLEANUP_REVIEW_HINT)
        self.assertIn("存疑", CLEANUP_REVIEW_HINT)

    def test_cleanup_stage_prints_hint_before_delete(self):
        import io
        from contextlib import redirect_stdout

        from mediascribe.pipeline_stages import CleanupStage

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "audio").mkdir()
            wav = tmp / "audio" / "a.wav"
            wav.write_bytes(b"x")
            ctx = types.SimpleNamespace(
                settings=types.SimpleNamespace(
                    cleanup_media=True,
                    workspace_root=tmp,
                    audio_dir=tmp / "audio",
                    downloads_dir=tmp / "downloads",
                ),
                audio_path=wav,
                video_path=None,
                result=object(),
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                CleanupStage().run(ctx)
            out = buf.getvalue()
            self.assertIn("清理提示", out)
            self.assertIn("已清理中间媒体", out)
            self.assertFalse(wav.exists())


if __name__ == "__main__":
    unittest.main()
