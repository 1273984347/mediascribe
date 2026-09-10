#!/usr/bin/env python3
"""
抖音作者主页批量转录 v3 - 完全强化版
（版本号与 douyin_batch.__version__ 同步，当前 3.1.0）

v3 相比 v2 的改进：
✅ 完整日志系统（彩色 + 文件）
✅ 配置管理（文件/环境变量/CLI）
✅ 进度条 + ETA 估计
✅ 断点续传（缓存已处理；失败项下一轮自动重试）
✅ 单元测试覆盖
✅ 优雅的异常处理
✅ 中英文双语 UI (i18n)

v3.2.0g:
- --config 纯配置文件启动可用（BatchConfig.user_url）
- workers>1 时用 ThreadPoolExecutor 逐视频并发（默认 1 保持原行为）
- 下载前对媒体 URL 做 check_url_safety 安全检查（security 工具接线）
- 收尾清理跳过失败视频的文件（可重试）
- 收尾只在浏览器已在运行时关闭（不再凭空启动一次 Playwright）
"""
import argparse
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

# 动态路径
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def build_parser(i18n_t) -> argparse.ArgumentParser:
    """Build argument parser with i18n-aware help text."""
    return argparse.ArgumentParser(
        description=i18n_t("CLI_DESCRIPTION"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=i18n_t("CLI_EPILOG"),
    )


def main():
    # Initialize i18n first (use default detection; --lang can override)
    from douyin_batch.i18n import init_language, set_language, t

    init_language()

    # Early parse: we only need --lang to localize argparse output
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--lang", default=None, choices=["en", "zh"])
    pre_args, _ = pre_parser.parse_known_args()
    if pre_args.lang:
        set_language(pre_args.lang)

    parser = build_parser(t)

    parser.add_argument("--from-video", metavar="URL", help=t("CLI_HELP_FROM_VIDEO"))
    parser.add_argument("--user", metavar="URL", help=t("CLI_HELP_USER"))
    parser.add_argument("-n", "--num", type=int, default=None, help=t("CLI_HELP_NUM"))
    parser.add_argument("--workers", type=int, default=None, help=t("CLI_HELP_WORKERS"))
    parser.add_argument("--no-headless", action="store_true", help=t("CLI_HELP_NO_HEADLESS"))
    parser.add_argument("--retries", type=int, default=None, help=t("CLI_HELP_RETRIES"))
    parser.add_argument("--output-dir", default=None, help=t("CLI_HELP_OUTPUT_DIR"))
    parser.add_argument("--keep-audio", action="store_true", help=t("CLI_HELP_KEEP_AUDIO"))
    parser.add_argument("--config", metavar="FILE", help=t("CLI_HELP_CONFIG"))
    parser.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--no-cache", action="store_true", help=t("CLI_HELP_NO_CACHE"))
    parser.add_argument("--clear-cache", action="store_true", help=t("CLI_HELP_CLEAR_CACHE"))
    parser.add_argument("--save-config", metavar="FILE", help=t("CLI_HELP_SAVE_CONFIG"))
    parser.add_argument("--lang", default=None, choices=["en", "zh"], help=t("CLI_HELP_LANG"))
    parser.add_argument(
        "--json",
        action="store_true",
        help=t("CLI_HELP_JSON"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=t("CLI_HELP_DRY_RUN"),
    )
    parser.add_argument(
        "--bilingual-json",
        action="store_true",
        help=t("CLI_HELP_BILINGUAL_JSON"),
    )
    parser.add_argument(
        "--platform",
        metavar="NAME",
        action="append",
        default=None,
        choices=["bilibili", "douyin", "youtube", "xiaohongshu", "wechat_mp", "tiktok"],
        help=t("CLI_HELP_PLATFORM"),
    )

    args = parser.parse_args()

    # Re-apply language in case --lang was passed after pre-parse
    if args.lang:
        set_language(args.lang)

    # 验证输入
    if not args.from_video and not args.user:
        if not args.config:
            parser.error(t("CLI_ERROR_NO_INPUT"))

    # 1. 加载配置（优先级：CLI > 文件 > 环境变量 > 默认）
    from douyin_batch.config import BatchConfig
    from douyin_batch.logger import log

    # 1.1 默认配置
    config = BatchConfig.from_env()

    # 1.2 配置文件
    if args.config:
        config_path = Path(args.config)
        file_config = BatchConfig.from_file(config_path)
        for k, v in file_config.to_dict().items():
            setattr(config, k, v)
        log.info(f"📂 {t('INFO_LOAD_CONFIG', path=config_path)}")

    # 1.3 CLI 参数（最高优先级；未显式提供的参数不覆盖文件/环境变量值）
    config.merge_cli_args(args)

    # 1.4 日志配置
    log.set_level(config.log_level)
    if config.log_to_file:
        log_file = Path(config.output_dir) / "logs" / "batch.log"
        log.add_file_handler(log_file)

    # 1.4b 初始化 Agent 输出（--json 模式）
    agent_out = None
    if args.json or args.bilingual_json:
        from douyin_batch.agent_output import AgentOutput
        agent_out = AgentOutput(command="douyin_batch_v3")
        if args.bilingual_json:
            agent_out.set_bilingual(True)
        log.set_quiet(True)  # 把人类可读的输出静音，JSON 才不会被污染
        # 基本配置
        agent_out.set_config(
            {
                "max_videos": config.max_videos,
                "workers": config.workers,
                "headless": config.headless,
                "language": config.language,
                "whisper_model": getattr(config, "whisper_model", "small"),
                "output_dir": str(config.output_dir),
            }
        )

    # 1.4c 平台过滤器（--platform）
    platform_filter = None
    if args.platform:
        # 去重但保持顺序
        seen = set()
        platform_filter = []
        for p in args.platform:
            if p not in seen:
                seen.add(p)
                platform_filter.append(p)
        log.info(f"🎯 {t('CLI_PLATFORM_SUMMARY', allowed=platform_filter)}")

    # 1.5 保存配置
    if args.save_config:
        save_path = Path(args.save_config)
        config.save(save_path)
        log.info(f"💾 {t('INFO_SAVE_CONFIG', path=save_path)}")

    # 显示配置
    log.info("=" * 60)
    log.info(f"📚 {t('INFO_BANNER_TITLE')}")
    log.info("=" * 60)
    log.info(f"   {t('INFO_BANNER_CONFIG', workers=config.workers, max_videos=config.max_videos)}")
    log.info(f"   {t('INFO_BANNER_BROWSER_HEADLESS') if config.headless else t('INFO_BANNER_BROWSER_VISIBLE')}")
    log.info(f"   {t('INFO_BANNER_RETRIES', count=config.max_retries)}")
    log.info(f"   {t('INFO_BANNER_CACHE_ON') if not args.no_cache else t('INFO_BANNER_CACHE_OFF')}")
    log.info("")

    # 2. 初始化缓存
    from douyin_batch.cache import ProcessCache
    cache = ProcessCache(cache_dir=Path(config.output_dir) / "cache")
    if args.clear_cache:
        cache.clear()
        log.warning(f"🧹 {t('INFO_CACHE_CLEARED')}")
    stats = cache.get_stats()
    log.info(f"📦 {t('INFO_CACHE_STATS', total=stats['total'], success=stats['success'], failed=stats['failed'])}")

    # 3. 启动浏览器
    from douyin_batch.browser import (
        get_media_url_fast,
        get_user_url_from_video,
        get_user_videos,
    )
    from douyin_batch.progress import ProgressTracker, format_duration
    from douyin_batch.report import generate_summary_report
    from douyin_batch.retry import download_media_with_retry
    from douyin_batch.transcribe import transcribe_audio

    start_time = time.time()

    try:
        # 3.1 获取作者主页
        if args.from_video:
            log.info(f"🔍 {t('INFO_STEP1_FROM_VIDEO')}")
            user_url = get_user_url_from_video(args.from_video, headless=config.headless)
            if not user_url:
                log.error(f"❌ {t('ERROR_NO_USER')}")
                if agent_out is not None:
                    agent_out.add_error("no_user", "Could not extract creator from video URL")
                return 1
            log.success(f"{t('SUCCESS_USER_URL', url=user_url)}")
        elif args.user:
            user_url = args.user
            log.info(f"🔍 {t('INFO_STEP1_USE_USER', url=user_url)}")
        else:
            # 从配置文件加载（P0-3: BatchConfig.user_url 字段正式生效）
            log.info(f"🔍 {t('INFO_STEP1_LOAD_CONFIG')}")
            user_url = config.user_url
            if not user_url:
                log.error(f"❌ {t('INFO_USER_URL_MISSING')}")
                if agent_out is not None:
                    agent_out.add_error("no_user_url", "user_url missing from config")
                return 1

        if agent_out is not None:
            agent_out.set_user_url(user_url)

        # 3.2 获取视频列表
        log.info(f"\n📋 {t('INFO_STEP2', count=config.max_videos)}")

        # 尝试从缓存读取
        cached_videos = cache.get_user_videos(user_url) if not args.no_cache else []
        if cached_videos:
            log.info(f"   📦 {t('INFO_STEP2_CACHED', count=len(cached_videos))}")
            videos = cached_videos[:config.max_videos]
        else:
            # P2-13: 滚动节奏由 BatchConfig 注入（scroll_pause / max_scroll_rounds）
            videos = get_user_videos(
                user_url,
                max_videos=config.max_videos,
                headless=config.headless,
                initial_wait=config.scroll_pause,
                scroll_pause=config.scroll_pause,
                max_scroll_rounds=config.max_scroll_rounds,
            )
            if videos:
                cache.save_user_videos(user_url, videos)

        if not videos:
            log.error(f"❌ {t('ERROR_NO_VIDEOS')}")
            return 1
        log.success(f"{t('SUCCESS_VIDEOS_FOUND', count=len(videos))}")

        # 3.3 断点续传：过滤已处理
        if not args.no_cache:
            unprocessed = cache.filter_unprocessed(videos)
            skipped = len(videos) - len(unprocessed)
            if skipped > 0:
                log.info(f"   ⏭️  {t('INFO_SKIP_PROCESSED', count=skipped)}")
            videos = unprocessed
            if not videos:
                log.info(f"✅ {t('INFO_ALL_PROCESSED')}")
                return 0

        # 3.4 批量处理
        log.info(f"\n🎬 {t('INFO_STEP3', count=len(videos))}...")
        log.info("=" * 60)

        output_dir = Path(config.output_dir)
        download_dir = output_dir / "downloads" / "user_videos"
        download_dir.mkdir(parents=True, exist_ok=True)

        # 进度跟踪（desc 经 i18n 注入，P2-15）
        tracker = ProgressTracker(total=len(videos), desc=t("INFO_PROGRESS_DESC"))

        results = []
        results_lock = threading.Lock()

        # P1-7: 转录池透传 BatchConfig（whisper_model / language 生效）
        def _transcribe_with_config(audio_path):
            return transcribe_audio(audio_path, config=config)

        def _run_one(index: int, video: Dict):
            started = time.time()
            result = process_single_video_safe(
                video=video,
                index=index,
                total=len(videos),
                download_dir=download_dir,
                config=config,
                get_media_url_fn=get_media_url_fast,
                download_media_fn=download_media_with_retry,
                transcribe_fn=_transcribe_with_config,
                log=log,
                i18n_t=t,
                platform_filter=platform_filter,
            )
            return result, time.time() - started, threading.current_thread().name

        def _record_result(result: Dict, task_time: float, worker_name: str) -> None:
            """登记单条结果（加锁：workers>1 时缓存写/进度更新线程安全）。"""
            with results_lock:
                results.append(result)
                tracker.update(success=result["status"] == "success", task_time=task_time)

                # JSON 模式：记录每条结果
                if agent_out is not None:
                    agent_out.add_video(
                        {
                            "video_id": result.get("video_id"),
                            "url": result.get("url"),
                            "platform": result.get("platform", "unknown"),
                            "status": result.get("status"),
                            "stage": result.get("stage"),
                            "transcript": result.get("transcript"),
                            "audio": result.get("audio"),
                            "error": result.get("error"),
                        }
                    )

                # 保存到缓存（ProcessCache 内部亦有线程锁）
                cache.mark_processed(
                    video_id=result["video_id"],
                    video_url=result.get("url", ""),
                    transcript_path=result.get("transcript"),
                    audio_path=result.get("audio"),
                    success=result["status"] == "success",
                )

                print(tracker.render(f"{result.get('video_id', '')}"), end="", flush=True)
                print()  # 换行
                elapsed = format_duration(tracker.get_elapsed())
                worker_prefix = f"[worker:{worker_name}] " if workers > 1 else ""
                log.info(
                    f"   {worker_prefix}"
                    f"{t('INFO_RUNNING_TOTAL', done=tracker.completed, total=tracker.total, time=elapsed)}"
                )

        workers = max(1, int(getattr(config, "workers", 1) or 1))
        if workers > 1:
            # P2-6: workers>1 时逐视频并发（默认 1 保持原串行行为）
            from concurrent.futures import ThreadPoolExecutor, as_completed

            log.info(f"🚵 并发 workers={workers}")
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="worker") as pool:
                futures = {
                    pool.submit(_run_one, i, video): video
                    for i, video in enumerate(videos, 1)
                }
                for fut in as_completed(futures):
                    result, task_time, worker_name = fut.result()
                    _record_result(result, task_time, worker_name)
        else:
            for i, video in enumerate(videos, 1):
                print(tracker.render(f"{video['video_id']}"), end="", flush=True)
                result, task_time, worker_name = _run_one(i, video)
                _record_result(result, task_time, worker_name)

        # 3.5 生成汇总报告
        log.info("\n" + "=" * 60)
        log.info(f"📊 {t('INFO_REPORT_GENERATING')}")

        summary_file = generate_summary_report(
            user_url=user_url,
            results=results,
            output_dir=output_dir,
        )

        if agent_out is not None:
            agent_out.set_summary_report(summary_file)

        success_count = sum(1 for r in results if r.get("status") == "success")
        elapsed = time.time() - start_time

        log.info("")
        log.info("=" * 60)
        log.success(f"{t('INFO_DONE')}")
        log.info(f"   {t('INFO_DONE_TOTAL', count=len(videos))}")
        log.info(f"   {t('INFO_DONE_SUCCESS', count=success_count)}")
        log.info(f"   {t('INFO_DONE_FAILED', count=len(videos) - success_count)}")
        log.info(f"   {t('INFO_DONE_ELAPSED', time=format_duration(elapsed))}")
        log.info(f"   {t('INFO_DONE_REPORT', path=summary_file)}")

        # 3.6 清理（P1-2: 失败视频的文件保留，下一轮可重试）
        if not config.keep_audio:
            log.info(f"\n🧹 {t('INFO_CLEANUP')}")
            from douyin_batch.security import sanitize_filename

            protected = {
                sanitize_filename(f"{r['video_id']}.mp4")
                for r in results
                if r.get("status") != "success"
            }
            kept = 0
            for f in download_dir.glob("*.mp4"):
                if f.name in protected:
                    kept += 1
                    continue
                try:
                    f.unlink()
                except Exception:
                    pass
            if kept:
                log.info(t("INFO_CLEANUP_KEEP_FAILED", count=kept))

        if agent_out is not None:
            agent_out.finish(ok=(success_count == len(videos)))
            agent_out.emit()
        return 0 if success_count == len(videos) else 1

    except KeyboardInterrupt:
        log.warning(f"\n⚠️ {t('WARN_INTERRUPTED')}")
        if agent_out is not None:
            agent_out.add_error("interrupted", "user interrupted")
            agent_out.finish(ok=False)
            agent_out.emit()
        return 130
    except Exception as e:
        log.error(f"💥 {t('ERROR_UNHANDLED', error=e)}")
        import traceback
        log.error(traceback.format_exc())
        if agent_out is not None:
            agent_out.add_error("unhandled", str(e))
            agent_out.finish(ok=False)
            agent_out.emit()
        return 1
    finally:
        # 清理浏览器（P1-4: 仅在已有实例时关闭，绝不凭空启动一次浏览器）
        try:
            from douyin_batch.browser import BrowserManager

            BrowserManager.close_if_running()
        except Exception:
            pass


def process_single_video_safe(
    video: Dict,
    index: int,
    total: int,
    download_dir: Path,
    config,
    get_media_url_fn,
    download_media_fn,
    transcribe_fn,
    log,
    i18n_t=None,
    platform_filter: Optional[List[str]] = None,
) -> Dict:
    """处理单个视频（带异常处理）。

    ``platform_filter`` 为 None 或空列表时处理所有平台。
    否则当 ``_detect_platform(video_url)`` 不在列表中时，
    返回 ``status="skipped"`` 的字典，不下载也不转录。
    """
    if i18n_t is None:
        # Late import to avoid circular issues
        from douyin_batch.i18n import t as _t
        i18n_t = _t

    video_id = video["video_id"]
    video_url = video["url"]
    platform = _detect_platform(video_url)

    # 平台过滤：跳过不属于白名单的视频
    if platform_filter and platform not in platform_filter:
        log.warning(
            i18n_t(
                "CLI_WARN_PLATFORM_SKIPPED",
                video_id=video_id,
                platform=platform,
                filter_set=platform_filter,
            )
        )
        return {
            "video_id": video_id,
            "url": video_url,
            "platform": platform,
            "status": "skipped",
            "stage": "platform_filter",
        }

    try:
        # 1. 获取媒体URL
        media_url = get_media_url_fn(video_url, headless=config.headless, timeout=config.max_wait_for_media)
        if not media_url:
            log.error(i18n_t("ERROR_VIDEO_MEDIA", index=index, total=total, id=video_id))
            return {
                "video_id": video_id,
                "url": video_url,
                "platform": platform,
                "status": "failed",
                "stage": "media_url",
            }

        # 1.5 下载前安全检查（P2-7: security 工具接线）
        from douyin_batch.security import check_url_safety, sanitize_filename

        url_ok, url_reason = check_url_safety(media_url)
        if not url_ok:
            log.warning(
                i18n_t("WARN_URL_UNSAFE", video_id=video_id, url=media_url, reason=url_reason)
            )
            return {
                "video_id": video_id,
                "url": video_url,
                "platform": platform,
                "status": "failed",
                "stage": "media_url",
                "error": f"unsafe media url: {url_reason}",
            }

        # 2. 下载（落地文件名过安全清洗，防路径穿越/非法字符）
        audio_path = download_dir / sanitize_filename(f"{video_id}.mp4")
        if not download_media_fn(media_url, audio_path, max_retries=config.max_retries):
            log.error(i18n_t("ERROR_VIDEO_DOWNLOAD", index=index, total=total, id=video_id))
            return {
                "video_id": video_id,
                "url": video_url,
                "platform": platform,
                "status": "failed",
                "stage": "download",
            }

        # 3. 转录
        transcript_path = transcribe_fn(audio_path)
        if transcript_path:
            log.success(i18n_t("SUCCESS_VIDEO_TRANSCRIBED", index=index, total=total, id=video_id))
            return {
                "video_id": video_id,
                "url": video_url,
                "platform": platform,
                "status": "success",
                "transcript": str(transcript_path),
                "audio": str(audio_path),
            }
        else:
            log.error(i18n_t("ERROR_VIDEO_TRANSCRIBE", index=index, total=total, id=video_id))
            return {
                "video_id": video_id,
                "url": video_url,
                "platform": platform,
                "status": "failed",
                "stage": "transcribe",
            }

    except Exception as e:
        log.error(i18n_t("ERROR_VIDEO_EXCEPTION", index=index, total=total, id=video_id, error=e))
        return {
            "video_id": video_id,
            "url": video_url,
            "platform": platform,
            "status": "failed",
            "stage": "exception",
            "error": str(e),
        }


def _detect_platform(url: str) -> str:
    """
    Best-effort platform detection for `--json` output.
    Returns one of the stable PLATFORM_* constants.
    """
    if not url:
        return "unknown"
    u = url.lower()
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    if "douyin.com" in u or "iesdouyin.com" in u:
        return "douyin"
    if "youtube.com" in u or "youtu.be" in u or "youtube-nocookie.com" in u:
        return "youtube"
    if "xiaohongshu.com" in u or "xhslink.com" in u:
        return "xiaohongshu"
    if "mp.weixin.qq.com" in u:
        return "wechat_mp"
    if "tiktok.com" in u:
        return "tiktok"
    if u.startswith("http://") or u.startswith("https://"):
        return "unknown"
    return "local"


if __name__ == "__main__":
    sys.exit(main())
