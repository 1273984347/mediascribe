"""Tests for report.py — generating summary markdown + JSON reports."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from douyin_batch.report import generate_summary_report


class TestGenerateSummaryReport(unittest.TestCase):
    """Tests for generate_summary_report()."""

    def setUp(self) -> None:
        self.tmp = Path(self._make_tmp())
        self.tmp.mkdir(parents=True, exist_ok=True)

    def _make_tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="v2t_report_")

    def _write_transcript(self, body: str) -> Path:
        p = self.tmp / f"transcript_{abs(hash(body))}.md"
        p.write_text(
            "# Video\n\n## 基本信息\n- 来源: Douyin\n\n## 转录内容\n\n" + body + "\n",
            encoding="utf-8",
        )
        return p

    def test_empty_results(self):
        out = generate_summary_report("https://www.douyin.com/user/EMPTY", [], self.tmp)
        self.assertTrue(out.exists())
        text = out.read_text(encoding="utf-8")
        self.assertIn("**总视频数**: 0", text)
        self.assertIn("**成功转录**: 0", text)
        self.assertIn("**失败**: 0", text)
        # JSON sidecar exists with empty results
        json_files = list(self.tmp.glob("results_*.json"))
        self.assertEqual(len(json_files), 1)
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        self.assertEqual(data["results"], [])
        self.assertEqual(data["total"], 0)

    def test_all_success_with_transcripts(self):
        t1 = self._write_transcript("你好世界，这是第一条视频。")
        t2 = self._write_transcript("第二条视频的转录。")
        results = [
            {
                "status": "success",
                "url": "https://www.douyin.com/video/001",
                "video_id": "001",
                "transcript": str(t1),
            },
            {
                "status": "success",
                "url": "https://www.douyin.com/video/002",
                "video_id": "002",
                "transcript": str(t2),
            },
        ]
        out = generate_summary_report("https://www.douyin.com/user/U1", results, self.tmp)
        text = out.read_text(encoding="utf-8")
        self.assertIn("**总视频数**: 2", text)
        self.assertIn("**成功转录**: 2", text)
        self.assertIn("**失败**: 0", text)
        self.assertIn("视频 001", text)
        self.assertIn("视频 002", text)
        self.assertIn("你好世界", text)
        self.assertIn("第二条视频", text)
        self.assertIn("https://www.douyin.com/user/U1", text)

    def test_all_failed(self):
        results = [
            {
                "status": "failed",
                "stage": "download",
                "video_id": "X1",
            },
            {
                "status": "failed",
                "stage": "transcribe",
                "video_id": "X2",
            },
        ]
        out = generate_summary_report("https://www.douyin.com/user/U2", results, self.tmp)
        text = out.read_text(encoding="utf-8")
        self.assertIn("**失败**: 2", text)
        self.assertIn("**成功转录**: 0", text)
        self.assertIn("失败阶段: download", text)
        self.assertIn("失败阶段: transcribe", text)

    def test_mixed_with_missing_url(self):
        # video without 'url' field falls back to constructed URL
        t = self._write_transcript("x")
        results = [
            {
                "status": "success",
                "video_id": "A1",
                "transcript": str(t),
                # no 'url' key
            },
            {
                "status": "failed",
                "video_id": "A2",
                "stage": "probe",
            },
        ]
        out = generate_summary_report("u", results, self.tmp)
        text = out.read_text(encoding="utf-8")
        self.assertIn("https://www.douyin.com/video/A1", text)
        self.assertIn("失败阶段: probe", text)

    def test_missing_transcript_file_is_skipped(self):
        results = [
            {
                "status": "success",
                "video_id": "M1",
                "transcript": str(self.tmp / "nonexistent.md"),
            },
        ]
        out = generate_summary_report("u", results, self.tmp)
        text = out.read_text(encoding="utf-8")
        # 视频列表 still references the video
        self.assertIn("M1", text)
        # but 转录内容 section is empty (file missing → skipped)
        self.assertNotIn("x\n\n---\n", text)

    def test_success_without_transcript_field(self):
        # success with no transcript key at all → row counted but not listed
        results = [
            {
                "status": "success",
                "video_id": "NT",
                "url": "https://www.douyin.com/video/NT",
            },
        ]
        out = generate_summary_report("u", results, self.tmp)
        text = out.read_text(encoding="utf-8")
        # still counts as success but no transcript file line
        self.assertIn("**成功转录**: 1", text)
        # 没有转录文件行（Path("") 触发缺文件 continue）
        self.assertNotIn("转录文件: ", text)

    def test_success_with_none_transcript(self):
        # success with explicit transcript=None → also safely skipped
        results = [
            {"status": "success", "video_id": "N1", "transcript": None},
        ]
        out = generate_summary_report("u", results, self.tmp)
        text = out.read_text(encoding="utf-8")
        self.assertIn("**成功转录**: 1", text)
        self.assertNotIn("转录文件: ", text)

    def test_unknown_status_counted_as_failure(self):
        results = [{"status": "weird", "video_id": "Q", "stage": "?"}]
        out = generate_summary_report("u", results, self.tmp)
        text = out.read_text(encoding="utf-8")
        self.assertIn("**失败**: 1", text)

    def test_no_transcript_content_section_in_output_is_empty(self):
        # transcript has '## 转录内容' header but empty body
        t = self.tmp / "empty_transcript.md"
        t.write_text("# Video\n\n## 基本信息\n\n## 转录内容\n", encoding="utf-8")
        results = [
            {"status": "success", "video_id": "E", "transcript": str(t)},
        ]
        out = generate_summary_report("u", results, self.tmp)
        text = out.read_text(encoding="utf-8")
        self.assertIn("视频 E", text)
        # empty transcript_text → no 转录内容 block written for that video
        self.assertNotIn("**转录内容**:\n", text)

    def test_json_sidecar_serialises_results(self):
        results = [
            {"status": "success", "video_id": "1", "transcript": None},
            {"status": "failed", "video_id": "2", "stage": "dl"},
        ]
        generate_summary_report("u", results, self.tmp)
        jsons = list(self.tmp.glob("results_*.json"))
        self.assertEqual(len(jsons), 1)
        data = json.loads(jsons[0].read_text(encoding="utf-8"))
        self.assertEqual(data["user_url"], "u")
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["success"], 1)
        self.assertEqual(data["failed"], 1)
        self.assertEqual(len(data["results"]), 2)
        # ensure_ascii=False, so Chinese remains Chinese
        self.assertIn("user_url", jsons[0].read_text(encoding="utf-8"))

    def test_creates_output_dir_if_missing(self):
        deep = self.tmp / "a" / "b" / "c"
        out = generate_summary_report("u", [], deep)
        self.assertTrue(deep.exists())
        self.assertTrue(out.exists())

    def test_status_icons(self):
        results = [
            {"status": "success", "video_id": "S1", "transcript": None},
            {"status": "failed", "video_id": "F1", "stage": "x"},
        ]
        out = generate_summary_report("u", results, self.tmp)
        text = out.read_text(encoding="utf-8")
        self.assertIn("✅", text)
        self.assertIn("❌", text)


if __name__ == "__main__":
    unittest.main()
