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


# ---------------------------------------------------------------------------
# 2026-11-02 可逆性门控: 来源不可回取时拒绝清理 (第三采样重下载前提)
# ---------------------------------------------------------------------------
class TestSourceReachabilityGate:
    def test_gone_source_blocks_deletion(self, settings, media, monkeypatch):
        monkeypatch.setattr(
            "mediascribe.pipeline_stages._probe_source_alive", lambda url, timeout=5.0: False
        )
        deleted = cleanup_intermediate_media(
            settings, media["audio"], media["video"], source_url="https://example.com/v/1"
        )
        assert deleted == []
        assert media["audio"].exists() and media["video"].exists()

    def test_indeterminate_source_blocks_deletion(self, settings, media, monkeypatch):
        # 离线/超时 → 无法判定 → 同样拒绝 (宁留勿删)
        monkeypatch.setattr(
            "mediascribe.pipeline_stages._probe_source_alive", lambda url, timeout=5.0: None
        )
        deleted = cleanup_intermediate_media(
            settings, media["audio"], media["video"], source_url="https://example.com/v/1"
        )
        assert deleted == []
        assert media["audio"].exists()

    def test_alive_source_deletes_normally(self, settings, media, monkeypatch):
        monkeypatch.setattr(
            "mediascribe.pipeline_stages._probe_source_alive", lambda url, timeout=5.0: True
        )
        deleted = cleanup_intermediate_media(
            settings, media["audio"], media["video"], source_url="https://example.com/v/1"
        )
        assert len(deleted) == 2

    def test_force_env_skips_probe(self, settings, media, monkeypatch):
        # 探测说挂了也不删不了 → force 跳过探测, 离线批量场景的阀门
        monkeypatch.setenv("MEDIASCRIBE_CLEANUP_FORCE", "1")
        monkeypatch.setattr(
            "mediascribe.pipeline_stages._probe_source_alive",
            lambda url, timeout=5.0: (_ for _ in ()).throw(AssertionError("probe must not run")),
        )
        deleted = cleanup_intermediate_media(
            settings, media["audio"], media["video"], source_url="https://example.com/v/1"
        )
        assert len(deleted) == 2

    def test_force_env_wide_vocabulary(self, settings, media, monkeypatch):
        monkeypatch.setattr(
            "mediascribe.pipeline_stages._probe_source_alive", lambda url, timeout=5.0: False
        )
        for value in ("1", "true", "YES", "On"):
            monkeypatch.setenv("MEDIASCRIBE_CLEANUP_FORCE", value)
            assert cleanup_intermediate_media(
                settings, media["audio"], media["video"], source_url="https://e.com/1"
            ), f"force={value} 应跳过探测直接删除"
            # 重建媒体文件供下一轮
            media["audio"].write_bytes(b"a" * 64)
            media["video"].write_bytes(b"v" * 128)

    def test_local_source_no_probe(self, settings, media, monkeypatch):
        # 本地文件来源没有 URL — 不探测直接删 (离线主路径零网络依赖)
        monkeypatch.setattr(
            "mediascribe.pipeline_stages._probe_source_alive",
            lambda url, timeout=5.0: (_ for _ in ()).throw(AssertionError("probe must not run")),
        )
        deleted = cleanup_intermediate_media(settings, media["audio"])
        assert [p.name for p in deleted] == ["BV123.wav"]

    def test_nothing_deletable_no_probe(self, settings, monkeypatch):
        monkeypatch.setattr(
            "mediascribe.pipeline_stages._probe_source_alive",
            lambda url, timeout=5.0: (_ for _ in ()).throw(AssertionError("probe must not run")),
        )
        assert (
            cleanup_intermediate_media(
                settings, None, settings.audio_dir / "ghost.wav", source_url="https://e.com/1"
            )
            == []
        )


class TestProbeSourceAlive:
    def test_404_is_gone(self, monkeypatch):
        import urllib.error

        from mediascribe.pipeline_stages import _probe_source_alive

        def raise_404(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)

        monkeypatch.setattr("urllib.request.urlopen", raise_404)
        assert _probe_source_alive("https://e.com/gone") is False

    def test_200_is_alive(self, monkeypatch):
        from mediascribe.pipeline_stages import _probe_source_alive

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            status = 200

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: FakeResp())
        assert _probe_source_alive("https://e.com/ok") is True

    def test_network_error_indeterminate(self, monkeypatch):
        from mediascribe.pipeline_stages import _probe_source_alive

        def boom(req, timeout):
            raise OSError("offline")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert _probe_source_alive("https://e.com/x") is None

    def test_403_head_blocked_treated_alive(self, monkeypatch):
        import urllib.error

        from mediascribe.pipeline_stages import _probe_source_alive

        def raise_403(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", None, None)

        monkeypatch.setattr("urllib.request.urlopen", raise_403)
        assert _probe_source_alive("https://e.com/anti-bot") is True
