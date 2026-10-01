"""v3.4.3 中间媒体清理(CleanupStage / cleanup_intermediate_media)测试

覆盖:
* 目录白名单 — 只删 audio_dir / downloads_dir 内的文件, 本地自备文件不动
* 开关语义 — settings.cleanup_media 关闭或 result 未生成都不到清理
* 失败隔离 — 单个文件删不掉只警告, 不抛异常不影响转录结果
* CLI --cross-check 推迟清理在 CLI 层实现, 此处测底层语义
"""

from pathlib import Path

import pytest

from mediascribe.config import Settings
from mediascribe.pipeline_stages import (
    CleanupStage,
    PipelineContext,
    cleanup_intermediate_media,
    default_chain,
)


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(workspace_root=tmp_path / "ws")


@pytest.fixture()
def media(settings: Settings) -> dict:
    """在托管目录里造一对中间媒体文件。"""
    video = settings.downloads_dir / "BV123.mp4"
    audio = settings.audio_dir / "BV123.wav"
    video.write_bytes(b"v" * 128)
    audio.write_bytes(b"a" * 64)
    return {"video": video, "audio": audio}


class TestCleanupIntermediateMedia:
    def test_deletes_managed_media(self, settings, media):
        deleted = cleanup_intermediate_media(settings, media["audio"], media["video"])
        assert sorted(p.name for p in deleted) == ["BV123.mp4", "BV123.wav"]
        assert not media["audio"].exists()
        assert not media["video"].exists()

    def test_never_deletes_outside_managed_dirs(self, settings, tmp_path):
        # 用户自备的本地视频/音频(不在 audio_dir/downloads_dir 内)绝不删除
        local = tmp_path / "my-own-video.mp4"
        local.write_bytes(b"x" * 32)
        deleted = cleanup_intermediate_media(settings, local)
        assert deleted == []
        assert local.exists()

    def test_missing_and_none_paths_skipped(self, settings, media):
        deleted = cleanup_intermediate_media(
            settings, None, settings.audio_dir / "ghost.wav", media["audio"]
        )
        assert [p.name for p in deleted] == ["BV123.wav"]

    def test_unlink_failure_does_not_raise(self, settings, media, monkeypatch):
        def boom(self):
            raise PermissionError("file locked (Windows)")

        monkeypatch.setattr(Path, "unlink", boom)
        deleted = cleanup_intermediate_media(settings, media["audio"], media["video"])
        assert deleted == []
        assert media["audio"].exists()

    def test_directory_entries_skipped(self, settings):
        stray_dir = settings.downloads_dir / "not-a-file"
        stray_dir.mkdir()
        assert cleanup_intermediate_media(settings, stray_dir) == []
        assert stray_dir.exists()


class TestCleanupStage:
    def _ctx(self, settings: Settings, *, result: bool, media: bool) -> PipelineContext:
        ctx = PipelineContext(settings=settings, source_input="BV123")
        if media:
            ctx.audio_path = settings.audio_dir / "BV123.wav"
            ctx.audio_path.write_bytes(b"a")
            ctx.video_path = settings.downloads_dir / "BV123.mp4"
            ctx.video_path.write_bytes(b"v")
        if result:
            ctx.result = object()  # 只判存在性, 类型无关
        return ctx

    def test_disabled_by_default(self, settings, media):
        ctx = self._ctx(settings, result=True, media=True)
        assert CleanupStage().should_run(ctx) is False

    def test_enabled_when_flag_and_result(self, settings, media):
        settings.cleanup_media = True
        assert CleanupStage().should_run(self._ctx(settings, result=True, media=True)) is True
        # 转录失败(result=None)时中间文件保留供排查
        assert CleanupStage().should_run(self._ctx(settings, result=False, media=True)) is False

    def test_run_deletes_and_keeps_outputs(self, settings, media):
        settings.cleanup_media = True
        transcript = settings.transcripts_dir / "BV123.md"
        transcript.write_text("# ok", encoding="utf-8")
        ctx = self._ctx(settings, result=True, media=True)
        ctx.transcript_path = transcript

        CleanupStage().run(ctx)

        assert not ctx.audio_path.exists()
        assert not ctx.video_path.exists()
        assert transcript.exists()

    def test_runs_after_result_marker(self):
        # stage chain 循环在 result 写入后 break, cleanup 必须带
        # runs_after_result 标记才能轮到
        assert CleanupStage.runs_after_result is True

    def test_default_chain_ends_with_cleanup(self, settings):
        chain = default_chain(
            transcriber=None,
            downloader=None,
            downloader_getter=None,
            resolve_output_path=lambda base, output: Path("/tmp/x.md"),
            resolve_metadata_path=lambda p: p.with_suffix(".json"),
            build_markdown=lambda *a, **k: "",
        )
        assert isinstance(chain[-1], CleanupStage)


class TestSettingsFlag:
    def test_default_off(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MEDIASCRIBE_CLEANUP_MEDIA", raising=False)
        assert Settings(workspace_root=tmp_path / "ws").cleanup_media is False

    def test_env_enables(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MEDIASCRIBE_CLEANUP_MEDIA", "1")
        assert Settings(workspace_root=tmp_path / "ws").cleanup_media is True

    def test_explicit_param_enables(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MEDIASCRIBE_CLEANUP_MEDIA", raising=False)
        assert Settings(workspace_root=tmp_path / "ws", cleanup_media=True).cleanup_media is True
