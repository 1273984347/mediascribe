"""FasterWhisperTranscriber 尾部覆盖守卫测试

背景: 实测偶发非确定性尾部截断(末段结束比音频末尾早 60-170s,
同代码同模型同音频不可复现)。守卫在转录后探测缺口, 超阈值即对
尾部切片无 VAD 重转并合并。
"""

import unittest
from unittest import mock

from mediascribe.transcribers import faster_whisper as fw
from mediascribe.transcribers.faster_whisper import (
    FasterWhisperTranscriber,
    tail_rescue_needed,
)


class _FakeSeg:
    def __init__(self, text, start, end):
        self.text = text
        self.start = start
        self.end = end


class _FakeInfo:
    language = "zh"


class _FakeModel:
    """按调用次序返回预置段列表的假 WhisperModel"""

    def __init__(self, calls):
        self.calls = calls  # list[list[_FakeSeg]]
        self.kwargs_log = []

    def transcribe(self, path, **kwargs):
        self.kwargs_log.append(kwargs)
        segs = self.calls.pop(0) if self.calls else []
        return iter(segs), _FakeInfo()


def _make_transcriber(model):
    tr = FasterWhisperTranscriber("small", device="cpu")
    tr._model = model
    return tr


class TestTailRescueNeeded(unittest.TestCase):
    def test_gap_over_threshold(self):
        segs = [{"end": 100.0}]
        self.assertTrue(tail_rescue_needed(segs, 131.0))

    def test_gap_under_threshold(self):
        segs = [{"end": 100.0}]
        self.assertFalse(tail_rescue_needed(segs, 130.0))

    def test_empty_segments_or_no_duration(self):
        self.assertFalse(tail_rescue_needed([], 579.0))
        self.assertFalse(tail_rescue_needed([{"end": 1.0}], None))

    def test_malformed_segment(self):
        self.assertFalse(tail_rescue_needed([{"nope": 1}], 579.0))


class TestTailGuardIntegration(unittest.TestCase):
    def _run(self, model, duration):
        tr = _make_transcriber(model)
        with mock.patch.object(
            fw, "_probe_duration_seconds", return_value=duration
        ), mock.patch.object(fw, "_extract_tail_wav", return_value=True) as ex:
            result = tr.transcribe("fake.wav", language="zh")
        return result, ex

    def test_truncated_tail_gets_rescued(self):
        model = _FakeModel(
            [
                [_FakeSeg("正文", 0.0, 500.0)],  # 主转录: 截断在 500s
                [
                    _FakeSeg("重叠区", 2.0, 9.0),
                    _FakeSeg("收尾语", 11.0, 79.0),
                ],  # 尾部切片(offset 490)
            ]
        )
        result, extract = self._run(model, 579.0)
        # 守卫触发: 第二次调用, 且切片起点 = 500-10 = 490
        self.assertEqual(len(model.kwargs_log), 2)
        self.assertFalse(model.kwargs_log[1]["vad_filter"])
        extract.assert_called_once()
        self.assertEqual(extract.call_args[0][1], 490.0)
        # 合并: 重叠段(490+2=492 < 500-0.5)被过滤, 只追加收尾语
        self.assertEqual(result["segments"][-1]["end"], 490.0 + 79.0)
        self.assertIn("收尾语", result["text"])

    def test_no_gap_skips_rescue(self):
        model = _FakeModel([[_FakeSeg("完整", 0.0, 578.0)]])
        result, extract = self._run(model, 579.0)
        self.assertEqual(len(model.kwargs_log), 1)
        extract.assert_not_called()
        self.assertEqual(len(result["segments"]), 1)

    def test_extract_failure_keeps_main_result(self):
        tr = _make_transcriber(_FakeModel([[_FakeSeg("正文", 0.0, 500.0)]]))
        with mock.patch.object(
            fw, "_probe_duration_seconds", return_value=579.0
        ), mock.patch.object(fw, "_extract_tail_wav", return_value=False):
            result = tr.transcribe("fake.wav", language="zh")
        self.assertEqual(len(result["segments"]), 1)
        self.assertEqual(result["text"], " 正文".strip())

    def test_rescue_exception_keeps_main_result(self):
        model = _FakeModel([[_FakeSeg("正文", 0.0, 500.0)]])
        tr = _make_transcriber(model)

        def boom(*a, **k):
            raise RuntimeError("slice boom")

        with mock.patch.object(
            fw, "_probe_duration_seconds", return_value=579.0
        ), mock.patch.object(fw, "_extract_tail_wav", side_effect=boom):
            result = tr.transcribe("fake.wav", language="zh")
        self.assertEqual(len(result["segments"]), 1)


if __name__ == "__main__":
    unittest.main()
