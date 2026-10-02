"""--cross-check 双模型交叉校对测试(diff/报告/端到端接线)"""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mediascribe.cross_check import build_report, diff_transcripts, run_cross_check
from mediascribe.models import SourceRef, TranscriptResult


def test_diff_identical_with_timestamps_and_whitespace():
    main = "**[00:12]** 大家可以去找到我们的助教老师。\n\n第二段。"
    cross = "大家可以去找到我们的助教老师。 第二段。"
    assert diff_transcripts(main, cross) == []


def test_diff_ignores_punctuation_only_replace():
    main = "他说,今天我们要讲幸福。"
    cross = "他说,今天我们要讲幸福."
    assert diff_transcripts(main, cross) == []


def test_diff_finds_proper_noun_divergence():
    main = "大家可以去找到我们的追悼老师通过置顶链接领取"
    cross = "大家可以去找到我们的注价老师通过置顶链接领取"
    divs = diff_transcripts(main, cross)
    # 字符级 diff 精准定位: 公共后缀"老师"不进分歧块
    assert len(divs) == 1
    assert divs[0].main == "追悼"
    assert divs[0].cross == "注价"
    assert "找到我们的" in divs[0].context


def test_diff_detects_number_divergence():
    # 数字分歧必须保留(46/48 分档位是审校关键信息); 字符级精准到差位数字
    divs = diff_transcripts("那恭喜你就是喜提46分", "那恭喜你就是喜提48分")
    assert len(divs) == 1
    assert divs[0].main == "6" and divs[0].cross == "8"


def test_diff_skips_tiny_insert_delete():
    # ASR 分段粒度差异造成的 1 字插入/删除跳过
    assert diff_transcripts("我们继续往下讲", "我们继续往下讲啦") == []


def test_build_report_contains_models_and_divergences():
    divs = diff_transcripts("主稿写冯骥才", "对照写冯继才")
    report = build_report(divs, main_model="large-v3", cross_model="small")
    assert "large-v3" in report
    assert "small" in report
    assert "骥" in report and "继" in report
    assert "分歧 ≠ 主稿有错" in report


def test_build_report_empty_divergences():
    report = build_report([], main_model="large-v3", cross_model="small")
    assert "无需人工比对" in report


def test_report_grades_noise_only_as_counts():
    # 纯虚词/语气词差异(的↔地)只计数不逐条 — 人工复核清单不被噪音淹没
    divs = diff_transcripts("我们不断的相逢", "我们不断地相逢")
    assert len(divs) == 1  # 单字 replace 进 diff, 但分级为噪音
    report = build_report(divs, main_model="large-v3", cross_model="small")
    assert "虚词/语气词差异 1 处只计数" in report
    assert "无实词分歧" in report


def test_report_lists_content_and_counts_noise_together():
    main = "冯骥才说的对他走了"
    cross = "冯继才说对她走了"
    divs = diff_transcripts(main, cross)
    report = build_report(divs, main_model="large-v3", cross_model="small")
    assert "骥" in report and "继" in report  # 实词分歧详列
    assert "虚词/语气词差异" in report  # 的↔地/他↔她 只计数
    assert "实词分歧(1 处" in report


def test_third_person_pronoun_is_noise():
    # 他↔它 ASR 无法区分性别, 归噪音
    divs = diff_transcripts("说他很好", "说它很好")
    report = build_report(divs, main_model="m", cross_model="c")
    assert "只计数" in report


def test_homophone_content_chars_stay_listed():
    # 在↔再 是同音实字(改语义), 保守起见不在噪音集合, 仍逐条列出
    divs = diff_transcripts("此在的意义", "此再的意义")
    report = build_report(divs, main_model="m", cross_model="c")
    assert "实词分歧(1 处, 逐条复核)" in report
    assert "在" in report and "再" in report


class _StubTranscriber:
    name = "faster-whisper"

    def __init__(self, text):
        self._text = text
        self.calls = []

    def transcribe(self, audio_path, *, prompt=None, progress=None, **kwargs):
        self.calls.append({"audio": audio_path, "prompt": prompt, **kwargs})
        return {"text": self._text}


class _StubPipeline:
    def __init__(self, text, model_name="large-v3"):
        self.settings = SimpleNamespace(model=model_name, engine="faster-whisper")
        self._text = text

    def _create_transcriber(self, settings, model=None):
        assert settings.model == model
        return _StubTranscriber(self._text)


def _make_result(tmp, text, model="large-v3"):
    audio = Path(tmp) / "audio.mp3"
    audio.write_bytes(b"x")
    return TranscriptResult(
        source=SourceRef(raw_input="https://v.douyin.com/x/", kind="douyin"),
        engine="faster-whisper",
        model=model,
        text=text,
        audio_path=audio,
        transcript_path=Path(tmp) / "douyin_123.md",
        metadata={"model": model},
        language="zh",
    )


def test_run_cross_check_same_model_skips():
    with tempfile.TemporaryDirectory() as tmp:
        result = _make_result(tmp, "主稿", model="small")
        assert run_cross_check(_StubPipeline("x"), result, "small") is None


def test_run_cross_check_writes_report_and_reuses_audio():
    with tempfile.TemporaryDirectory() as tmp:
        result = _make_result(tmp, "大家可以去找到我们的追悼老师领取")
        stub = _StubTranscriber("大家可以去找到我们的注价老师领取")
        pipeline = mock.MagicMock()
        pipeline.settings = SimpleNamespace(model="large-v3", engine="faster-whisper")
        pipeline._create_transcriber.return_value = stub

        report_path = run_cross_check(pipeline, result, "small")

        assert report_path == Path(tmp) / "douyin_123.crosscheck.md"
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert "追悼" in content and "注价" in content
        # 同名 JSON 完整清单同步落盘
        json_path = Path(tmp) / "douyin_123.crosscheck.json"
        assert json_path.exists()
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["content_count"] == 1 and payload["divergences"][0]["main"] == "追悼"
        # 复用同一条音频, 且语言/prompt 透传
        assert stub.calls[0]["audio"] == result.audio_path
        assert stub.calls[0]["language"] == "zh"


def test_json_report_untruncated_beyond_max_divergences():
    """md 截断到 MAX_DIVERGENCES 处, JSON 必须含全部分歧(2026-10-02 复盘)。"""
    with tempfile.TemporaryDirectory() as tmp:
        main = "".join(f"词语{i}甲" for i in range(120))
        cross = "".join(f"词语{i}乙" for i in range(120))
        result = _make_result(tmp, main)
        pipeline = mock.MagicMock()
        pipeline.settings = SimpleNamespace(model="large-v3", engine="faster-whisper")
        pipeline._create_transcriber.return_value = _StubTranscriber(cross)

        report_path = run_cross_check(pipeline, result, "small")

        payload = json.loads(
            report_path.with_name(report_path.stem + ".json").read_text(encoding="utf-8")
        )
        assert payload["total"] == payload["content_count"]
        assert payload["total"] > 100  # 超过 md 截断上限
        assert payload["divergences"][-1]["index"] == payload["total"]
        # md 仍按上限截断并指向 json
        md = report_path.read_text(encoding="utf-8")
        assert "实词分歧略" in md and ".crosscheck.json" in md
