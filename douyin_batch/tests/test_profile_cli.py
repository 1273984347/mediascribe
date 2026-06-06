"""Tests for v3.2.0a profile CLI."""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _write_jsonl(path: Path, records) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _sample_records():
    return [
        {"ts": "2026-06-06T10:00:00+00:00", "label": "download", "duration_sec": 1.0},
        {"ts": "2026-06-06T10:00:01+00:00", "label": "download", "duration_sec": 2.0},
        {"ts": "2026-06-06T10:00:02+00:00", "label": "download", "duration_sec": 3.0},
        {"ts": "2026-06-06T10:00:03+00:00", "label": "transcribe", "duration_sec": 5.0},
        {"ts": "2026-06-06T10:00:04+00:00", "label": "transcribe", "duration_sec": 7.0},
        {"ts": "2026-06-06T10:00:05+00:00", "label": "merge", "duration_sec": 0.5},
    ]


# ---------------------------------------------------------------------------
# read_jsonl + filter_records + aggregate
# ---------------------------------------------------------------------------


class TestReadJsonl(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self._make_tmp())
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.f = self.tmp / "x.jsonl"
        _write_jsonl(self.f, _sample_records())

    def _make_tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="v2t_prof_")

    def test_reads_all(self):
        from video2text.profile_cli import read_jsonl

        recs = read_jsonl(self.f)
        self.assertEqual(len(recs), 6)

    def test_skips_blank_lines(self):
        from video2text.profile_cli import read_jsonl

        with self.f.open("a", encoding="utf-8") as fh:
            fh.write("\n\n")
        recs = read_jsonl(self.f)
        self.assertEqual(len(recs), 6)

    def test_skips_corrupt_lines(self):
        from video2text.profile_cli import read_jsonl

        with self.f.open("a", encoding="utf-8") as fh:
            fh.write("{not json}\n")
            fh.write(json.dumps({"label": "ok", "duration_sec": 1.0}) + "\n")
        recs = read_jsonl(self.f)
        # 6 valid + 1 new valid = 7; corrupt line skipped
        self.assertEqual(len(recs), 7)

    def test_missing_file_raises(self):
        from video2text.profile_cli import read_jsonl

        with self.assertRaises(FileNotFoundError):
            read_jsonl(self.tmp / "absent.jsonl")


class TestFilterRecords(unittest.TestCase):
    def setUp(self) -> None:
        from video2text.profile_cli import read_jsonl

        self.tmp = Path(self._make_tmp())
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.f = self.tmp / "x.jsonl"
        _write_jsonl(self.f, _sample_records())
        self.records = read_jsonl(self.f)

    def _make_tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="v2t_prof_")

    def test_no_filters(self):
        from video2text.profile_cli import filter_records

        out = filter_records(self.records)
        self.assertEqual(len(out), 6)

    def test_by_stage_substring(self):
        from video2text.profile_cli import filter_records

        out = filter_records(self.records, by_stage="down")
        self.assertEqual(len(out), 3)

    def test_by_stage_no_match(self):
        from video2text.profile_cli import filter_records

        out = filter_records(self.records, by_stage="zzzz")
        self.assertEqual(out, [])

    def test_since_filters_old(self):
        from video2text.profile_cli import filter_records

        # Drop everything before 2026-06-06T10:00:03 → 3 records remain
        out = filter_records(self.records, since="2026-06-06T10:00:03")
        self.assertEqual(len(out), 3)

    def test_since_bad_format_raises(self):
        from video2text.profile_cli import filter_records

        with self.assertRaises(ValueError):
            filter_records(self.records, since="not-a-date")


class TestAggregate(unittest.TestCase):
    def test_aggregates_by_label(self):
        import tempfile

        from video2text.profile_cli import aggregate, read_jsonl
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.jsonl"
            _write_jsonl(p, _sample_records())
            recs = read_jsonl(p)
        stages = aggregate(recs)
        labels = [s.label for s in stages]
        # Sorted by total_sec desc: transcribe (12) > download (6) > merge (0.5)
        self.assertEqual(labels, ["transcribe", "download", "merge"])
        dl = next(s for s in stages if s.label == "download")
        self.assertEqual(dl.count, 3)
        self.assertAlmostEqual(dl.total_sec, 6.0, places=3)
        self.assertAlmostEqual(dl.mean_sec, 2.0, places=3)
        self.assertEqual(dl.min_sec, 1.0)
        self.assertEqual(dl.max_sec, 3.0)

    def test_skips_non_numeric_durations(self):
        from video2text.profile_cli import aggregate

        stages = aggregate([
            {"label": "a", "duration_sec": "not-a-number"},
            {"label": "a", "duration_sec": 1.0},
        ])
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0].count, 1)


# ---------------------------------------------------------------------------
# render_markdown / render_json
# ---------------------------------------------------------------------------


class TestRender(unittest.TestCase):
    def test_markdown_empty(self):
        from video2text.profile_cli import render_markdown

        s = render_markdown([])
        self.assertIn("no profile data", s)

    def test_markdown_contains_table(self):
        from video2text.profile_cli import AggregatedStage, render_markdown

        stages = [AggregatedStage("a", 2, 3.0, 1.5, 1.0, 2.0, 2.0, 1.0)]
        s = render_markdown(stages)
        self.assertIn("| Stage | Count |", s)
        self.assertIn("| `a` | 2 |", s)
        self.assertIn("Total wall time", s)

    def test_markdown_top_n(self):
        from video2text.profile_cli import AggregatedStage, render_markdown

        # b has the highest total_sec, so it should be #1 under top=1
        stages = [
            AggregatedStage("b", 1, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0),
            AggregatedStage("a", 1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
        ]
        s = render_markdown(stages, top=1)
        self.assertIn("`b`", s)
        self.assertNotIn("`a`", s)

    def test_json_output(self):
        from video2text.profile_cli import AggregatedStage, render_json

        stages = [AggregatedStage("a", 1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)]
        s = render_json(stages)
        data = json.loads(s)
        self.assertEqual(data["stages"][0]["label"], "a")
        self.assertEqual(data["total_sec"], 1.0)


# ---------------------------------------------------------------------------
# main() — CLI entry point
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self._make_tmp())
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.f = self.tmp / "x.jsonl"
        _write_jsonl(self.f, _sample_records())

    def _make_tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="v2t_prof_main_")

    def test_renders_markdown(self):
        from video2text.profile_cli import main

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = main([str(self.f)])
        self.assertEqual(rc, 0)
        s = buf.getvalue()
        self.assertIn("| Stage |", s)
        self.assertIn("transcribe", s)

    def test_renders_json(self):
        from video2text.profile_cli import main

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = main([str(self.f), "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("stages", data)

    def test_top_filter(self):
        from video2text.profile_cli import main

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            main([str(self.f), "--top", "1"])
        s = buf.getvalue()
        self.assertIn("transcribe", s)
        self.assertNotIn("merge", s)

    def test_by_stage_filter(self):
        from video2text.profile_cli import main

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            main([str(self.f), "--by-stage", "trans"])
        s = buf.getvalue()
        self.assertIn("transcribe", s)
        self.assertNotIn("download", s)

    def test_since_filter(self):
        from video2text.profile_cli import main

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            main([str(self.f), "--since", "2026-06-06T10:00:05"])
        s = buf.getvalue()
        # only merge (ts 10:00:05) is >= cutoff
        self.assertIn("merge", s)
        self.assertNotIn("download", s)


# ---------------------------------------------------------------------------
# @profile_step JSONL dump — end-to-end
# ---------------------------------------------------------------------------


class TestProfileStepJsonlDump(unittest.TestCase):
    def test_writes_jsonl(self):
        import tempfile

        from video2text.performance import profile_step

        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "timings.jsonl"

            @profile_step("demo", log_to=log)
            def work(n: int) -> int:
                return n * 2

            work(2)
            work(3)
            work(4)

            lines = log.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 3)
            records = [json.loads(line) for line in lines]
            for r in records:
                self.assertEqual(r["label"], "demo")
                self.assertIn("ts", r)
                self.assertIn("duration_sec", r)
                # Duration is small but > 0
                self.assertGreaterEqual(r["duration_sec"], 0.0)

    def test_no_log_no_file(self):
        import tempfile

        from video2text.performance import profile_step

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)

            @profile_step("no-log")
            def work() -> int:
                return 1

            work()
            # No log file should have been created
            self.assertEqual(list(d.iterdir()), [])


# ---------------------------------------------------------------------------
# video2text.__main__ dispatches to profile
# ---------------------------------------------------------------------------


class TestPackageMain(unittest.TestCase):
    def test_no_args(self):
        from video2text.__main__ import main

        with mock.patch("sys.stderr", io.StringIO()):
            rc = main()
        self.assertEqual(rc, 1)

    def test_help(self):
        from video2text.__main__ import main

        with mock.patch("sys.stdout", io.StringIO()):
            rc = main(["help"])
        self.assertEqual(rc, 0)

    def test_unknown_command(self):
        from video2text.__main__ import main

        with mock.patch("sys.stderr", io.StringIO()):
            rc = main(["nope"])
        self.assertEqual(rc, 1)

    def test_profile_routes_through(self):
        import tempfile

        from video2text.__main__ import main

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.jsonl"
            _write_jsonl(p, _sample_records())
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                rc = main(["profile", str(p), "--top", "1"])
        self.assertEqual(rc, 0)
        s = buf.getvalue()
        self.assertIn("transcribe", s)


if __name__ == "__main__":
    unittest.main()
