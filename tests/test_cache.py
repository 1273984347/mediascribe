"""douyin_batch.cache 的测试 —— 迁入自 tests/test_coverage_gaps.py(按被测模块归位)。"""

from __future__ import annotations

###########################################################################
# 迁入自 tests/test_coverage_gaps.py —— TestCacheExtras(douyin_batch.cache)
###########################################################################


class TestCacheExtras:
    """Cover the file-load error path and user-list helpers."""

    def test_load_returns_empty_on_corrupt_json(self, tmp_path):
        from douyin_batch.cache import ProcessCache

        (tmp_path / "processed_videos.json").write_text("{not json", encoding="utf-8")
        cache = ProcessCache(cache_dir=tmp_path)
        assert cache.is_processed("any") is False
        assert cache.get_stats() == {"total": 0, "success": 0, "failed": 0}

    def test_mark_processed_writes_to_disk(self, tmp_path):
        from douyin_batch.cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        cache.mark_processed("v1", "http://x/1", "t.md", "a.mp3", success=True)
        assert cache.is_processed("v1")
        rec = cache.get_processed("v1")
        assert rec["transcript"] == "t.md" and rec["success"] is True
        # Stats reflect it
        assert cache.get_stats() == {"total": 1, "success": 1, "failed": 0}

    def test_filter_unprocessed(self, tmp_path):
        from douyin_batch.cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        cache.mark_processed("a", "http://x/a", success=True)
        videos = [{"video_id": "a"}, {"video_id": "b"}, {"video_id": "c"}]
        out = cache.filter_unprocessed(videos)
        assert [v["video_id"] for v in out] == ["b", "c"]

    def test_user_videos_round_trip(self, tmp_path):
        from douyin_batch.cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        assert cache.get_user_videos("http://u/1") == []
        cache.save_user_videos("http://u/1", [{"id": 1}, {"id": 2}])
        videos = cache.get_user_videos("http://u/1")
        assert [v["id"] for v in videos] == [1, 2]
        # Hash is keyed on URL, not whitespace.
        assert cache.get_user_videos("http://u/1  ") == []

    def test_clear_resets_state(self, tmp_path):
        from douyin_batch.cache import ProcessCache

        cache = ProcessCache(cache_dir=tmp_path)
        cache.mark_processed("a", "http://x/a", success=True)
        cache.save_user_videos("http://u/1", [{"id": 1}])
        cache.clear()
        assert cache.is_processed("a") is False
        assert cache.get_user_videos("http://u/1") == []
