"""mediascribe.__main__ 的测试 —— 迁入自 tests/test_coverage_gaps_2.py(按被测模块归位)。"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

###########################################################################
# 迁入自 tests/test_coverage_gaps_2.py —— TestMain(mediascribe.__main__)
###########################################################################


class TestMain:
    def test_main_module_help(self, capsys):
        from mediascribe.__main__ import main

        with mock.patch.object(sys, "argv", ["mediascribe", "--help"]):
            with pytest.raises(SystemExit) as excinfo:
                main()
        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        # help text mentions the two main subcommands
        assert "transcribe" in captured.out or "transcribe" in captured.err

    def test_transcribe_without_engine_returns_structured_error(self, capsys, monkeypatch):
        from mediascribe.__main__ import main

        monkeypatch.setattr("mediascribe.config.find_spec", lambda _name: None)

        assert main(["transcribe", "video.mp4"]) == 1
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "ENGINE_UNAVAILABLE" in output
        assert "Traceback" not in output

    def test_cli_prints_whisper_fallback_notice(self, capsys, monkeypatch):
        from mediascribe.__main__ import main

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "whisper" else None,
        )

        with (
            mock.patch("mediascribe.__main__.Pipeline"),
            mock.patch("mediascribe.__main__._execute_transcribe", return_value=0) as execute,
        ):
            assert main(["transcribe", "https://www.bilibili.com/video/BV1xx411c7mD"]) == 0

        settings = execute.call_args.args[2]
        assert settings.engine == "whisper"
        assert "faster-whisper" in settings.engine_notice
        captured = capsys.readouterr()
        assert "faster-whisper" in captured.out + captured.err

    def test_doctor_cli_accepts_offline(self):
        from pathlib import Path

        from mediascribe.__main__ import main

        with mock.patch("mediascribe.doctor.run_doctor", return_value=0) as run:
            assert main(["doctor", "--offline", "--workspace", "ws"]) == 0

        run.assert_called_once_with(workspace=Path("ws"), offline=True)

    def test_cli_prints_auto_selection_plan(self, capsys, monkeypatch):
        from mediascribe.__main__ import main

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "faster_whisper" else None,
        )

        with (
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
            mock.patch("mediascribe.__main__.Pipeline") as pipeline,
            mock.patch("mediascribe.__main__._execute_transcribe", return_value=0),
        ):
            assert main(["transcribe", "https://www.bilibili.com/video/BV1xx411c7mD"]) == 0

        pipeline.assert_called_once()
        settings = pipeline.call_args.args[0]
        assert settings.model == "small"
        assert settings.requested_model == "auto"
        assert settings.model_explicit is False
        out = capsys.readouterr().out + capsys.readouterr().err
        assert "Selection plan" in out or "选择计划" in out
        assert "auto" in out
        assert "faster-whisper" in out
        assert "small" in out
        assert "cpu" in out

    def test_cli_prints_explicit_selection_plan(self, capsys, monkeypatch):
        from mediascribe.__main__ import main

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "whisper" else None,
        )

        with (
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
            mock.patch("mediascribe.__main__.Pipeline") as pipeline,
            mock.patch("mediascribe.__main__._execute_transcribe", return_value=0),
        ):
            assert (
                main(
                    [
                        "transcribe",
                        "https://www.bilibili.com/video/BV1xx411c7mD",
                        "--engine",
                        "whisper",
                        "--model",
                        "base",
                        "--device",
                        "cpu",
                    ]
                )
                == 0
            )

        settings = pipeline.call_args.args[0]
        assert settings.model_explicit is True
        assert settings.requested_model == "base"
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "Selection plan" in out or "选择计划" in out
        assert "explicit" in out or "显式" in out
        assert "whisper" in out
        assert "base" in out
        assert "cpu" in out

    def test_cli_blocks_explicit_unavailable_engine_before_pipeline(self, capsys, monkeypatch):
        from mediascribe.__main__ import main

        monkeypatch.setattr("mediascribe.config.find_spec", lambda _name: None)

        with mock.patch("mediascribe.__main__.Pipeline") as pipeline:
            assert main(["transcribe", "video.mp4", "--engine", "whisperx"]) == 1

        pipeline.assert_not_called()
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "ENGINE_UNAVAILABLE" in out
        assert "whisperx" in out
        assert "Traceback" not in out

    def test_cli_blocks_explicit_unavailable_device_before_pipeline(self, capsys, monkeypatch):
        import torch

        from mediascribe.__main__ import main

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "whisper" else None,
        )
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

        with mock.patch("mediascribe.__main__.Pipeline") as pipeline:
            assert main(["transcribe", "video.mp4", "--engine", "whisper", "--device", "cuda"]) == 1

        pipeline.assert_not_called()
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "DEVICE_UNAVAILABLE" in out
        assert "cuda" in out
        assert "Traceback" not in out

    def test_cli_invalid_config_value_returns_field_error(self, tmp_path, capsys):
        import json

        from mediascribe.__main__ import main

        config = tmp_path / "config.json"
        config.write_text(json.dumps({"model": "bogus"}), encoding="utf-8")

        with mock.patch("mediascribe.__main__.Pipeline") as pipeline:
            assert main(["--config", str(config), "transcribe", "video.mp4"]) == 1

        pipeline.assert_not_called()
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "CONFIG_INVALID" in out
        assert "model" in out
        assert "Traceback" not in out

    def test_cli_invalid_device_config_returns_field_error(self, tmp_path, capsys):
        import json

        from mediascribe.__main__ import main

        config = tmp_path / "config.json"
        config.write_text(json.dumps({"engine": "whisper", "device": "tpu"}), encoding="utf-8")

        with mock.patch("mediascribe.__main__.Pipeline") as pipeline:
            assert main(["--config", str(config), "transcribe", "video.mp4"]) == 1

        pipeline.assert_not_called()
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "CONFIG_INVALID" in out
        assert "device" in out
        assert "Traceback" not in out

    def test_cli_selection_plan_uses_post_guard_values(self, capsys, monkeypatch):
        from mediascribe.__main__ import main
        from mediascribe.runtime_guard import DegradeDecision

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "faster_whisper" else None,
        )

        with (
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
            mock.patch("mediascribe.pipeline.WhisperCppTranscriber"),
            mock.patch(
                "mediascribe.pipeline.ensure_model_ready",
                return_value=mock.MagicMock(ok=True),
            ),
            mock.patch(
                "mediascribe.pipeline.plan_resources",
                return_value=DegradeDecision(
                    engine="whisper-cpp",
                    model="large-v3",
                    device="cpu",
                    changed=True,
                    reasons=["memory-pressure-engine-switch"],
                    notices=["测试切换：faster-whisper → whisper-cpp"],
                ),
            ),
            mock.patch("mediascribe.__main__._execute_transcribe", return_value=0),
        ):
            assert (
                main(
                    [
                        "transcribe",
                        "https://www.bilibili.com/video/BV1xx411c7mD",
                        "--model",
                        "large-v3",
                        "--allow-model-download",
                    ]
                )
                == 0
            )

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "auto→whisper-cpp" in out
        assert "large-v3→large-v3" in out
        assert "测试切换" in out

    def test_cli_missing_input_fails_before_pipeline(self, tmp_path, capsys):
        from mediascribe.__main__ import main

        missing = tmp_path / "missing.wav"
        with mock.patch("mediascribe.__main__.Pipeline") as pipeline:
            assert main(["transcribe", str(missing)]) == 1

        pipeline.assert_not_called()
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "INPUT_INVALID" in out
        assert "Traceback" not in out

    def test_cli_selection_plan_includes_input_and_output(self, tmp_path, capsys, monkeypatch):
        from mediascribe.__main__ import main

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "faster_whisper" else None,
        )
        source = "https://www.bilibili.com/video/BV1xx411c7mD"
        output = tmp_path / "out.md"

        with (
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
            mock.patch("mediascribe.__main__.Pipeline"),
            mock.patch("mediascribe.__main__._execute_transcribe", return_value=0),
        ):
            assert main(["transcribe", source, "--output", str(output)]) == 0

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert source in out
        assert str(output) in out

    def test_cli_model_download_flag_reaches_settings(self, monkeypatch):
        from mediascribe.__main__ import main

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "faster_whisper" else None,
        )

        with (
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
            mock.patch("mediascribe.__main__.Pipeline") as pipeline,
            mock.patch("mediascribe.__main__._execute_transcribe", return_value=0),
        ):
            assert (
                main(
                    [
                        "transcribe",
                        "https://www.bilibili.com/video/BV1xx411c7mD",
                        "--allow-model-download",
                    ]
                )
                == 0
            )

        settings = pipeline.call_args.args[0]
        assert settings.allow_model_download is True

    def test_cli_cache_dir_flag_reaches_settings(self, tmp_path, monkeypatch):
        from pathlib import Path

        from mediascribe.__main__ import main

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "faster_whisper" else None,
        )
        cache_dir = tmp_path / "cache"

        with (
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
            mock.patch("mediascribe.__main__.Pipeline") as pipeline,
            mock.patch("mediascribe.__main__._execute_transcribe", return_value=0),
        ):
            assert (
                main(
                    [
                        "transcribe",
                        "https://www.bilibili.com/video/BV1xx411c7mD",
                        "--cache-dir",
                        str(cache_dir),
                    ]
                )
                == 0
            )

        assert pipeline.call_args.args[0].cache_dir == Path(cache_dir)

    def test_cli_config_cache_dir_reaches_settings(self, tmp_path, monkeypatch):
        import json
        from pathlib import Path

        from mediascribe.__main__ import main

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "faster_whisper" else None,
        )
        cache_dir = tmp_path / "cache"
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"cache_dir": str(cache_dir)}), encoding="utf-8")

        with (
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
            mock.patch("mediascribe.__main__.Pipeline") as pipeline,
            mock.patch("mediascribe.__main__._execute_transcribe", return_value=0),
        ):
            assert (
                main(
                    [
                        "--config",
                        str(config),
                        "transcribe",
                        "https://www.bilibili.com/video/BV1xx411c7mD",
                    ]
                )
                == 0
            )

        assert pipeline.call_args.args[0].cache_dir == Path(cache_dir)

    @pytest.mark.parametrize("error_type", ["TranscriptionEmptyError", "NoSpeechError"])
    def test_cli_empty_result_returns_structured_error(self, capsys, monkeypatch, error_type):
        from mediascribe import pipeline_stages
        from mediascribe.__main__ import main

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "whisper" else None,
        )
        error = getattr(pipeline_stages, error_type)("empty result")
        with (
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
            mock.patch("mediascribe.__main__.Pipeline") as pipeline,
        ):
            pipeline.return_value.transcribe.side_effect = error
            with pytest.raises(SystemExit) as exc:
                main(["transcribe", "https://example.com/video.mp4"])
            assert exc.value.code == 1
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert error.code in out
        assert "Traceback" not in out

    def test_cli_recoverable_error_prints_action_without_raw_detail(self, capsys, monkeypatch):
        from mediascribe.__main__ import main
        from mediascribe.model_weights import ModelDownloadError

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "whisper" else None,
        )
        with (
            mock.patch("mediascribe.pipeline.resolve_device", return_value="cpu"),
            mock.patch("mediascribe.__main__.Pipeline") as pipeline,
        ):
            pipeline.return_value.transcribe.side_effect = ModelDownloadError(
                "private token /secret/path"
            )
            with pytest.raises(SystemExit) as exc:
                main(["transcribe", "https://example.com/video.mp4"])
        assert exc.value.code == 1
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "MODEL_DOWNLOAD_FAILED" in output
        assert "Retry" in output or "重试" in output
        assert "/secret/path" not in output
        assert "Traceback" not in output

    def test_cli_missing_model_returns_structured_error(self, capsys, monkeypatch):
        from mediascribe.__main__ import main
        from mediascribe.model_weights import WeightStatus

        monkeypatch.setattr(
            "mediascribe.config.find_spec",
            lambda name: object() if name == "whisper" else None,
        )
        monkeypatch.setattr(
            "mediascribe.model_weights.check_weights",
            lambda engine, model: WeightStatus(
                engine=engine,
                model=model,
                ok=False,
                location="fake-cache",
                hint="provision model",
            ),
        )

        assert (
            main(
                ["transcribe", "https://www.bilibili.com/video/BV1xx411c7mD", "--engine", "whisper"]
            )
            == 1
        )
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "MODEL_NOT_READY" in out
        assert "Traceback" not in out


class TestCacheCli:
    @staticmethod
    def _seed_download(tmp_path):
        from mediascribe.cache import PersistentDownloadCache

        root = tmp_path / "cache"
        source = tmp_path / "source.bin"
        source.write_bytes(b"abc")
        cache = PersistentDownloadCache(base=root)
        cached = cache.put("https://example.test/media", source, suffix=".bin")
        cache.close()
        return root, cached

    def test_info_json_reports_cache_state_without_creating_missing_root(
        self, tmp_path, capsys, monkeypatch
    ):
        import json

        from mediascribe.__main__ import main

        monkeypatch.delenv("MEDIASCRIBE_DOWNLOAD_CACHE", raising=False)
        root, _ = self._seed_download(tmp_path)

        assert main(["cache", "info", "--json", "--cache-dir", str(root)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["root"] == str(root)
        assert payload["download_cache_enabled"] is False
        assert payload["downloads"]["entries"] == 1
        assert payload["downloads"]["bytes"] == 3
        assert payload["downloads"]["wired"] is True
        assert payload["chunks"]["entries"] == 0
        assert payload["chunks"]["wired"] is False

        missing = tmp_path / "missing"
        assert main(["cache", "info", "--json", "--cache-dir", str(missing)]) == 0
        assert not missing.exists()
        capsys.readouterr()

    def test_clear_requires_explicit_yes_and_preserves_files(self, tmp_path, capsys):
        from mediascribe.__main__ import main

        root, cached = self._seed_download(tmp_path)

        assert main(["cache", "clear", "--cache-dir", str(root)]) == 2
        assert cached.exists()
        assert "--yes" in capsys.readouterr().err

    def test_clear_with_yes_removes_only_cache_entries(self, tmp_path):
        from mediascribe.__main__ import main
        from mediascribe.cache import PersistentChunkCache

        root, cached = self._seed_download(tmp_path)
        source = tmp_path / "source.bin"
        chunks_source = tmp_path / "chunks-source"
        chunks_source.mkdir()
        (chunks_source / "000.wav").write_bytes(b"chunk")
        chunks = PersistentChunkCache(base=root / "chunks")
        chunk_entry = chunks.put(source, {"chunk_seconds": 60}, chunks_source)
        chunks.close()
        learned_terms = root / "learned_terms.json"
        learned_terms.write_bytes(b"keep")

        assert main(["cache", "clear", "--yes", "--cache-dir", str(root)]) == 0
        assert not cached.exists()
        assert not chunk_entry.exists()
        assert learned_terms.read_bytes() == b"keep"
        assert root.is_dir()

    def test_clear_reports_corrupt_index_without_deleting_files(self, tmp_path, capsys):
        from mediascribe.__main__ import main

        root = tmp_path / "cache"
        root.mkdir()
        (root / "downloads.index.json").write_text("{broken", encoding="utf-8")
        cached = root / "cached.bin"
        cached.write_bytes(b"keep")

        assert main(["cache", "clear", "--yes", "--cache-dir", str(root)]) == 1
        assert cached.read_bytes() == b"keep"
        output = capsys.readouterr().out + capsys.readouterr().err
        assert "invalid" in output
        assert "Traceback" not in output
