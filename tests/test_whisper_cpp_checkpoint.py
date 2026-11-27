from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mediascribe.checkpoint import CheckpointCapability, CheckpointCursor, CheckpointSession
from mediascribe.transcribers.whisper_cpp import WhisperCppTranscriber
from mediascribe.zh_normalize import SIMPLIFIED_ZH_PROMPT


def _record_window_commit(
    committed: list[tuple[int, float, float, str | None]],
):
    def commit(index: int, start: float, end: float, detected_language: str | None) -> None:
        committed.append((index, start, end, detected_language))

    return commit


def _official_json_runner(commands: list[list[str]], languages: list[str | None]):
    def run(command, **_kwargs):
        index = len(commands)
        commands.append(command)
        out_prefix = Path(command[command.index("-of") + 1])
        payload = {
            "result": {"language": languages[index]},
            "transcription": [],
        }
        Path(str(out_prefix) + ".json").write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return run


def test_cpp_window_adapter_offsets_segments_and_marks_complete_window(tmp_path: Path):
    transcriber = WhisperCppTranscriber(model="tiny", device="cpu", binary=Path("fake.exe"))
    committed: list[dict] = []
    windows: list[tuple[int, float, float, str | None]] = []
    session = CheckpointSession(
        capability=CheckpointCapability("whisper-cpp", "window", 60, 2),
        prior_segments=[{"text": "old", "start": 0.0, "end": 10.0}],
        cursor=CheckpointCursor("window", 58.0, 10.0, "old", next_window_index=1),
        segment_cb=committed.append,
        window_committed_cb=_record_window_commit(windows),
    )

    def fake_runner(audio, **kwargs):
        kwargs["session"].start_window(1, 58.0, 118.0)
        result = kwargs["transcribe_window"](tmp_path / "window.wav", 60.0)
        for segment in result["segments"]:
            kwargs["session"].accept(
                {**segment, "start": segment["start"] + 58.0, "end": segment["end"] + 58.0}
            )
        kwargs["session"].commit_window(1, 58.0, 118.0, "zh")
        return {"text": result["text"], "segments": kwargs["session"].segments, "language": "zh"}

    with (
        mock.patch.object(transcriber, "_resolve_binary", return_value=Path("fake.exe")),
        mock.patch.object(transcriber, "_resolve_model", return_value=Path("ggml-tiny.bin")),
        mock.patch.object(
            transcriber,
            "_run_binary_on_wav",
            return_value=(
                [{"text": "new", "start": 1.0, "end": 3.0}],
                "zh",
            ),
        ) as run_binary,
        mock.patch(
            "mediascribe.transcribers.whisper_cpp._to_wav_16k",
            side_effect=AssertionError("checkpoint windows are already 16 kHz mono WAV"),
        ),
        mock.patch(
            "mediascribe.transcribers.whisper_cpp.run_windowed_transcription",
            side_effect=fake_runner,
        ),
    ):
        result = transcriber.transcribe(
            tmp_path / "audio.wav",
            language="zh",
            checkpoint_session=session,
        )

    assert [segment["text"] for segment in result["segments"]] == ["old", "new"]
    assert result["segments"][-1]["start"] == 59.0
    assert result["segments"][-1]["end"] == 61.0
    assert committed == result["segments"][1:]
    assert windows == [(1, 58.0, 118.0, "zh")]
    assert run_binary.call_args.kwargs["window_duration"] == 60.0


def test_cpp_multi_window_auto_language_is_reused_and_committed(tmp_path: Path):
    transcriber = WhisperCppTranscriber(model="tiny", device="cpu", binary=Path("fake.exe"))
    windows_committed: list[tuple[int, float, float, str | None]] = []
    session = CheckpointSession(
        capability=CheckpointCapability("whisper-cpp", "window", 60, 2),
        prior_segments=[],
        cursor=None,
        segment_cb=None,
        window_committed_cb=_record_window_commit(windows_committed),
    )
    windows = [
        mock.MagicMock(index=0, start=0.0, end=60.0, path=tmp_path / "0.wav"),
        mock.MagicMock(index=1, start=58.0, end=118.0, path=tmp_path / "1.wav"),
    ]

    commands: list[list[str]] = []

    with (
        mock.patch.object(transcriber, "_resolve_binary", return_value=Path("fake.exe")),
        mock.patch.object(transcriber, "_resolve_model", return_value=Path("ggml-tiny.bin")),
        mock.patch(
            "mediascribe.transcribers.whisper_cpp.subprocess.run",
            side_effect=_official_json_runner(commands, ["en", "en"]),
        ),
        mock.patch("mediascribe.checkpoint.probe_duration_seconds", return_value=118.0),
        mock.patch("mediascribe.checkpoint.iter_checkpoint_windows", return_value=iter(windows)),
    ):
        result = transcriber.transcribe(tmp_path / "audio.wav", checkpoint_session=session)

    assert ["-l" in command for command in commands] == [False, True]
    assert [command[command.index("-l") + 1] for command in commands[1:]] == ["en"]
    assert result["language"] == "en"
    assert windows_committed == [(0, 0.0, 60.0, "en"), (1, 58.0, 118.0, "en")]


def test_cpp_resume_passes_durable_detected_language_to_window(tmp_path: Path):
    transcriber = WhisperCppTranscriber(model="tiny", device="cpu", binary=Path("fake.exe"))
    windows_committed: list[tuple[int, float, float, str | None]] = []
    session = CheckpointSession(
        capability=CheckpointCapability("whisper-cpp", "window", 60, 2),
        prior_segments=[],
        cursor=CheckpointCursor(
            "window", 58.0, 0.0, "", next_window_index=1, detected_language="zh"
        ),
        segment_cb=None,
        window_committed_cb=_record_window_commit(windows_committed),
    )
    window = mock.MagicMock(index=1, start=58.0, end=118.0, path=tmp_path / "1.wav")

    commands: list[list[str]] = []

    with (
        mock.patch.object(transcriber, "_resolve_binary", return_value=Path("fake.exe")),
        mock.patch.object(transcriber, "_resolve_model", return_value=Path("ggml-tiny.bin")),
        mock.patch(
            "mediascribe.transcribers.whisper_cpp.subprocess.run",
            side_effect=_official_json_runner(commands, ["zh"]),
        ),
        mock.patch("mediascribe.checkpoint.probe_duration_seconds", return_value=118.0),
        mock.patch("mediascribe.checkpoint.iter_checkpoint_windows", return_value=iter([window])),
    ):
        result = transcriber.transcribe(tmp_path / "audio.wav", checkpoint_session=session)

    assert commands[0][commands[0].index("-l") + 1] == "zh"
    assert result["language"] == "zh"
    assert windows_committed == [(1, 58.0, 118.0, "zh")]


def test_cpp_effective_decode_params_cache_binary_sha256_by_stat(tmp_path: Path):
    binary = tmp_path / "whisper-cli"
    binary.write_bytes(b"binary-v1")
    transcriber = WhisperCppTranscriber(
        model="tiny", device="cpu", binary=binary, vad=True, threads=3
    )
    expected_v1 = hashlib.sha256(b"binary-v1").hexdigest()
    real_open = Path.open

    with (
        mock.patch.object(transcriber, "_resolve_binary", return_value=binary),
        mock.patch.object(Path, "open", autospec=True, side_effect=real_open) as opened,
    ):
        first = transcriber.effective_decode_params(
            language="zh", initial_prompt=SIMPLIFIED_ZH_PROMPT
        )
        second = transcriber.effective_decode_params(
            language="zh", initial_prompt=SIMPLIFIED_ZH_PROMPT
        )
        assert (
            sum(
                call.kwargs.get("mode", call.args[1] if len(call.args) > 1 else None) == "rb"
                for call in opened.call_args_list
            )
            == 1
        )

        binary.write_bytes(b"binary-version-2")
        changed = transcriber.effective_decode_params(
            language="zh", initial_prompt=SIMPLIFIED_ZH_PROMPT
        )
        assert (
            sum(
                call.kwargs.get("mode", call.args[1] if len(call.args) > 1 else None) == "rb"
                for call in opened.call_args_list
            )
            == 2
        )

    expected_v2 = hashlib.sha256(b"binary-version-2").hexdigest()
    assert first == second
    assert first == {
        "engine": "whisper-cpp",
        "model": "tiny",
        "device": "cpu",
        "language": "zh",
        "initial_prompt": SIMPLIFIED_ZH_PROMPT,
        "vad": True,
        "threads": 3,
        "binary_sha256": expected_v1,
        "window_seconds": 60,
        "overlap_seconds": 2,
    }
    assert changed["binary_sha256"] == expected_v2
