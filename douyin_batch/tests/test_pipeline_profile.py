"""
v3.2.0c Tier 2 — Pipeline profile flag tests.

Coverage:

1. ``Pipeline(profile=False)`` (default) does not record STEP_TIMES
2. ``Pipeline(profile=True)`` records per-stage timings
3. ``Pipeline(profile=True, profile_log=Path(...))`` writes JSONL
4. Public contract preserved: signature, return type unchanged
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mediascribe.config import Settings  # noqa: E402
from mediascribe.models import TranscriptResult  # noqa: E402
from mediascribe.performance import STEP_TIMES, clear_step_times, get_step_times  # noqa: E402
from mediascribe.pipeline import Pipeline  # noqa: E402


def _fake_settings(tmp: Path) -> Settings:
    return Settings(workspace_root=tmp)


def _audio_file(tmp: Path, name: str = "hello.wav") -> Path:
    p = tmp / name
    p.write_bytes(b"RIFFfake")
    return p


def _fake_transcriber() -> mock.MagicMock:
    t = mock.MagicMock(name="Transcriber")
    t.name = "fake"
    t.transcribe.return_value = {
        "text": "hello world",
        "model": "fake-tiny",
        "language": "en",
        "segments": [],
        "speaker_diarization": False,
    }
    return t


class TestPipelineProfileFlag(unittest.TestCase):
    def setUp(self) -> None:
        clear_step_times()

    def tearDown(self) -> None:
        clear_step_times()

    def test_profile_false_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = _audio_file(tmp)
            p = Pipeline(
                settings=_fake_settings(tmp),
                transcriber=_fake_transcriber(),
            )
            self.assertFalse(p.profile)
            self.assertIsNone(p.profile_log)
            r = p.transcribe(str(audio))
            self.assertIsInstance(r, TranscriptResult)
            # No STEP_TIMES recorded when profile is off.
            self.assertEqual(get_step_times(), {})

    def test_profile_true_records_step_times(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = _audio_file(tmp)
            p = Pipeline(
                settings=_fake_settings(tmp),
                transcriber=_fake_transcriber(),
                profile=True,
            )
            self.assertTrue(p.profile)
            r = p.transcribe(str(audio))
            self.assertIsInstance(r, TranscriptResult)
            times = get_step_times()
            # parse / download / extract_audio / transcribe / assemble
            # should all be recorded; should_run() may skip some.
            self.assertGreater(len(times), 0, "profile=True must record timings")
            # At least transcribe + assemble should always fire.
            self.assertIn("transcribe", times)
            self.assertIn("assemble", times)
            # Each label must have at least one duration.
            for label, durations in times.items():
                self.assertGreater(len(durations), 0)
                for d in durations:
                    self.assertGreaterEqual(d, 0.0)

    def test_profile_log_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = _audio_file(tmp)
            log_path = tmp / "perf.jsonl"
            p = Pipeline(
                settings=_fake_settings(tmp),
                transcriber=_fake_transcriber(),
                profile=True,
                profile_log=log_path,
            )
            p.transcribe(str(audio))
            self.assertTrue(log_path.exists(), "JSONL log file must be created")
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertGreater(len(lines), 0)
            for line in lines:
                rec = json.loads(line)
                self.assertIn("ts", rec)
                self.assertIn("label", rec)
                self.assertIn("duration_sec", rec)
                self.assertGreaterEqual(rec["duration_sec"], 0.0)
                self.assertIsInstance(rec["label"], str)

    def test_profile_clears_step_times_between_runs(self):
        """Each ``transcribe()`` call starts with a fresh STEP_TIMES."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = _audio_file(tmp)
            p = Pipeline(
                settings=_fake_settings(tmp),
                transcriber=_fake_transcriber(),
                profile=True,
            )
            p.transcribe(str(audio))
            first_run = get_step_times()
            self.assertGreater(len(first_run), 0)
            # Second run must clear the registry first so timings from
            # the first run do not leak into the second.
            p.transcribe(str(audio))
            second_run = get_step_times()
            # Counts should match (each label appears the same number
            # of times across the two runs) — clear_step_times is
            # called at the start of each run.
            for label in second_run:
                self.assertEqual(
                    len(second_run[label]), len(first_run.get(label, [])),
                    f"label {label!r} count drifted between runs",
                )

    def test_public_contract_preserved(self):
        """Pipeline(settings, downloader, transcriber) signature still works."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = _audio_file(tmp)
            # Positional args only — must remain valid.
            p = Pipeline(
                _fake_settings(tmp),
                None,
                _fake_transcriber(),
            )
            r = p.transcribe(str(audio))
            self.assertIsInstance(r, TranscriptResult)
            self.assertEqual(r.text, "hello world")


if __name__ == "__main__":
    unittest.main()
