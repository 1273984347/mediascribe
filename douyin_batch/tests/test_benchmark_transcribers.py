"""
Unit tests for the transcriber benchmark script.

The benchmark is mostly an I/O / timing wrapper around the
``video2text.transcribers.factory`` module.  These tests pin
the parts that are easy to break: cost profile integrity, table
renderer, and the JSON report shape.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import benchmark_transcribers  # noqa: E402


class TestCostProfile(unittest.TestCase):
    """All three engines must have a valid cost profile entry."""

    def test_all_engines_have_profile(self):
        for engine in ("whisper", "faster-whisper", "whisperx"):
            self.assertIn(engine, benchmark_transcribers.COST_PROFILE)

    def test_profile_shape(self):
        for engine, profile in benchmark_transcribers.COST_PROFILE.items():
            self.assertIn("cold_start_ms", profile, engine)
            self.assertIn("ms_per_audio_sec", profile, engine)
            self.assertGreater(profile["cold_start_ms"], 0, engine)
            self.assertGreater(profile["ms_per_audio_sec"], 0, engine)

    def test_faster_whisper_cheaper_than_whisper(self):
        # Sanity: the documented ordering is whisperx ~ faster-whisper << whisper
        w = benchmark_transcribers.COST_PROFILE["whisper"]["ms_per_audio_sec"]
        fw = benchmark_transcribers.COST_PROFILE["faster-whisper"]["ms_per_audio_sec"]
        wx = benchmark_transcribers.COST_PROFILE["whisperx"]["ms_per_audio_sec"]
        self.assertLess(fw, w, "faster-whisper should be cheaper than whisper")
        self.assertLess(wx, w, "whisperx should be cheaper than whisper")


class TestRenderTable(unittest.TestCase):
    """The ASCII table must include every result row."""

    def test_renders_all_engines(self):
        results = [
            benchmark_transcribers.BenchResult(
                engine="whisper", available=True,
                cold_start_ms=10, per_call_ms=[100, 110], peak_mem_mb=1.0,
            ),
            benchmark_transcribers.BenchResult(
                engine="faster-whisper", available=True,
                cold_start_ms=5, per_call_ms=[20, 25], peak_mem_mb=0.5,
            ),
        ]
        text = benchmark_transcribers.render_table(results)
        self.assertIn("whisper", text)
        self.assertIn("faster-whisper", text)
        self.assertIn("Engine", text)
        self.assertIn("---", text)


class TestWriteReports(unittest.TestCase):
    """JSON + markdown reports must be written and parseable."""

    def test_writes_three_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            results = [
                benchmark_transcribers.BenchResult(
                    engine="whisper", available=True,
                    per_call_ms=[100.0], peak_mem_mb=1.0,
                )
            ]
            benchmark_transcribers.write_reports(results, out_dir)
            self.assertTrue((out_dir / "benchmark.json").exists())
            self.assertTrue((out_dir / "benchmark.txt").exists())
            self.assertTrue((out_dir / "benchmark.md").exists())
            data = json.loads((out_dir / "benchmark.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["results"]), 1)
            self.assertEqual(data["results"][0]["engine"], "whisper")


if __name__ == "__main__":
    unittest.main()
