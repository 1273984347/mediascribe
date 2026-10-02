"""端到端成稿断言(v3.4.3 教训固化): stub 管线跑到落盘, 后处理必须进入交付物。

5b521b6 之前, build_markdown 在 segments 存在时用 raw 分段拼正文,
术语校正/重复收敛/LLM 润色整条链从未进入成稿 — 单测全过但交付物
没有。本文件把"成稿可见"固化为集成断言。
"""

from pathlib import Path

from mediascribe.config import Settings
from mediascribe.pipeline import Pipeline


class _StubTranscriber:
    """恒定输出的假转写器 — raw 文本故意含术语错误 + segments。"""

    name = "stub"

    def transcribe(self, audio_path, *, prompt=None, progress=None, language=None, **kw):
        text = "矛盾而分是我们讨论的话题。"
        return {
            "text": text,
            "segments": [{"start": 0.0, "end": 1.0, "text": text}],
            "model": "stub",
            "language": "zh",
        }


def test_terms_reach_written_markdown(tmp_path, monkeypatch):
    # 学习术语在临时映射里注入(不依赖本机 learned_terms.json, CI 无状态)。
    # 注意: 包属性 `mediascribe.learn` 被 __init__ 导入的同名函数遮蔽,
    # `import mediascribe.learn as m` 会绑到函数 — 必须走 sys.modules。
    import sys

    import mediascribe.learn  # noqa: F401  确保子模块已加载

    learn_mod = sys.modules["mediascribe.learn"]
    monkeypatch.setattr(learn_mod, "get_learned_terms", lambda path=None: {"矛盾而分": "矛盾二分"})
    settings = Settings(workspace_root=tmp_path / "ws")
    pipeline = Pipeline(settings, transcriber=_StubTranscriber())
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"fake audio bytes")

    result = pipeline.transcribe(str(audio))

    md = Path(result.transcript_path).read_text(encoding="utf-8")
    assert "矛盾二分" in md, "成稿必须包含术语替换后的正确词"
    assert "矛盾而分" not in md, "成稿不得残留 raw 术语错误(segments 存在也不行)"
    assert result.metadata["repeated_pairs_suspect"] == []
    assert result.metadata["engine"] == "stub"
