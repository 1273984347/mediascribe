#!/usr/bin/env python3
"""
抖音作者主页批量转录 v3 — 已收编为包模块 ``mediascribe.batch_cli``。

历史上它是仓库根级散文件 ``douyin_batch_v3.py``(entry point + py-modules
+ 动态加载三层耦合, 2026-10-03 审查收编); 根级现仅存一个弃用壳。

抖音作者主页批量转录 v3 - 完全强化版
（版本号与 mediascribe.__version__ 同步，当前 3.1.0）

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

结构（2026-10-23 可读性重构，行为不变）: ``main`` 只保留流程编排与
退出码; 参数注册集中在 :func:`build_parser`, 配置装配 / Agent 输出 /
作者页解析 / 视频清单 / 结果登记(:class:`_ResultRecorder`) / 收尾清理
拆为模块级辅助函数。``process_single_video_safe`` 与 ``_detect_platform``
的对外契约不变。
"""

import argparse
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# --lang 预解析器: i18n 字典生效前抢读语言参数, 才能本地化 argparse 输出。
_LANG_PRE_PARSER = argparse.ArgumentParser(add_help=False)
_LANG_PRE_PARSER.add_argument("--lang", default=None, choices=["en", "zh"])


def build_parser(i18n_t) -> argparse.ArgumentParser:
    from mediascribe.config import _VALID_ENGINES
    from mediascribe.url_utils import _PLATFORM_FINGERPRINTS

    """Build argument parser with i18n-aware help text.

    全部批量转录 CLI 选项都集中在此注册, ``main`` 只负责解析与编排。
    """
    t = i18n_t
    parser = argparse.ArgumentParser(
        prog="python -m mediascribe archive",
        description=t("CLI_DESCRIPTION"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=t("CLI_EPILOG"),
    )
    parser.add_argument("--from-video", metavar="URL", help=t("CLI_HELP_FROM_VIDEO"))
    parser.add_argument("--user", metavar="URL", help=t("CLI_HELP_USER"))
    parser.add_argument("-n", "--num", type=int, default=None, help=t("CLI_HELP_NUM"))
    parser.add_argument("--workers", type=int, default=None, help=t("CLI_HELP_WORKERS"))
    parser.add_argument("--no-headless", action="store_true", help=t("CLI_HELP_NO_HEADLESS"))
    parser.add_argument("--retries", type=int, default=None, help=t("CLI_HELP_RETRIES"))
    parser.add_argument("--output-dir", default=None, help=t("CLI_HELP_OUTPUT_DIR"))
    parser.add_argument("--keep-audio", action="store_true", help=t("CLI_HELP_KEEP_AUDIO"))
    parser.add_argument(
        "--engine",
        choices=list(_VALID_ENGINES),
        default=None,
        help=t("CLI_HELP_ENGINE"),
    )
    parser.add_argument(
        "--allow-model-download",
        action="store_true",
        default=False,
        help=t("CLI_HELP_ALLOW_MODEL_DOWNLOAD"),
    )
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
        "--bilingual-json",
        action="store_true",
        help=t("CLI_HELP_BILINGUAL_JSON"),
    )
    parser.add_argument(
        "--platform",
        metavar="NAME",
        action="append",
        default=None,
        # P2-9(2026-11-20 评审): choices 接真源 —— 不再手抄副本,
        # 与 url_utils 指纹表的一致性由 test_platform_consistency 行为断言钉死
        choices=[name for name, _ in _PLATFORM_FINGERPRINTS],
        help=t("CLI_HELP_PLATFORM"),
    )
    return parser


def _load_batch_config(args, t):
    """装配批量配置并初始化日志。

    优先级: CLI 显式参数(``merge_cli_args``) > 配置文件(``--config``)
    > 环境变量 > 内置默认。
    """
    from mediascribe.batch_config import BatchConfig
    from mediascribe.logger import log

    config = BatchConfig.from_env()

    if args.config:
        config_path = Path(args.config)
        file_config = BatchConfig.from_file(config_path)
        for k, v in file_config.to_dict().items():
            setattr(config, k, v)
        log.info(f"📂 {t('INFO_LOAD_CONFIG', path=config_path)}")

    # CLI 参数（最高优先级；未显式提供的参数不覆盖文件/环境变量值）
    config.merge_cli_args(args)

    log.set_level(config.log_level)
    if config.log_to_file:
        log_file = Path(config.output_dir) / "logs" / "batch.log"
        log.add_file_handler(log_file)
    return config


def _init_agent_output(args, config):
    """``--json`` / ``--bilingual-json`` 模式下初始化 Agent 结构化输出。

    返回 ``None`` 表示人类可读模式; 开启时人类日志被静音, stdout 只
    输出 JSON, 供上层 Agent 直接消费。
    """
    if not (args.json or args.bilingual_json):
        return None
    from mediascribe.agent_output import AgentOutput
    from mediascribe.logger import log

    # ``command`` 是 schema ``mediascribe.agent-output/v1`` 的**稳定字段值**,
    # 不是脚本路径 —— 旧入口 douyin_batch_v3.py 已于 2026-10-24 删除, 但
    # 该字面量已被下游 Agent 消费方依赖, 不随 CLI 改名而变动(要改须升 schema 版本)。
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
    return agent_out


def _platform_filter_from_args(args, t) -> Optional[List[str]]:
    """``--platform`` 平台白名单: 去重并保持首次出现顺序; 未传返回 None。"""
    if not args.platform:
        return None
    from mediascribe.logger import log

    platform_filter = list(dict.fromkeys(args.platform))
    log.info(f"🎯 {t('CLI_PLATFORM_SUMMARY', allowed=platform_filter)}")
    return platform_filter


def _log_banner(config, no_cache: bool, t) -> None:
    """打印启动横幅 — 当前生效的配置一览。"""
    from mediascribe.logger import log

    log.info("=" * 60)
    log.info(f"📚 {t('INFO_BANNER_TITLE')}")
    log.info("=" * 60)
    log.info(f"   {t('INFO_BANNER_CONFIG', workers=config.workers, max_videos=config.max_videos)}")
    log.info(
        f"   {t('INFO_BANNER_BROWSER_HEADLESS') if config.headless else t('INFO_BANNER_BROWSER_VISIBLE')}"
    )
    log.info(f"   {t('INFO_BANNER_RETRIES', count=config.max_retries)}")
    log.info(f"   {t('INFO_BANNER_CACHE_ON') if not no_cache else t('INFO_BANNER_CACHE_OFF')}")
    log.info("")


def _resolve_user_url(args, config, get_user_url_from_video, agent_out, t) -> Optional[str]:
    """确定要抓取的作者主页 URL: ``--from-video`` > ``--user`` > 配置文件。

    返回 ``None`` 表示无法确定(错误日志与 agent 错误已记录),
    调用方应以退出码 1 结束。
    """
    from mediascribe.logger import log

    if args.from_video:
        log.info(f"🔍 {t('INFO_STEP1_FROM_VIDEO')}")
        user_url: Optional[str] = get_user_url_from_video(args.from_video, headless=config.headless)
        if not user_url:
            log.error(f"❌ {t('ERROR_NO_USER')}")
            if agent_out is not None:
                agent_out.add_error("no_user", "Could not extract creator from video URL")
            return None
        log.success(f"{t('SUCCESS_USER_URL', url=user_url)}")
        return user_url

    if args.user:
        log.info(f"🔍 {t('INFO_STEP1_USE_USER', url=args.user)}")
        resolved: str = args.user
        return resolved

    # 从配置文件加载（P0-3: BatchConfig.user_url 字段正式生效）
    log.info(f"🔍 {t('INFO_STEP1_LOAD_CONFIG')}")
    user_url = config.user_url
    if not user_url:
        log.error(f"❌ {t('INFO_USER_URL_MISSING')}")
        if agent_out is not None:
            agent_out.add_error("no_user_url", "user_url missing from config")
        return None
    return user_url


def _collect_videos(args, cache, config, user_url, get_user_videos, t) -> List[Dict]:
    """取作者主页视频清单 — 命中缓存直接用, 否则抓取并回写缓存。"""
    from mediascribe.logger import log

    log.info(f"\n📋 {t('INFO_STEP2', count=config.max_videos)}")

    # P2-5: 视频列表缓存带 TTL(默认 24h,MEDIASCRIBE_BATCH_LIST_TTL_HOURS 可调,0=旧行为)
    list_ttl = float(getattr(config, "list_ttl_hours", 24) or 0)
    cached_videos: List[Dict] = (
        cache.get_user_videos(user_url, ttl_hours=list_ttl) if not args.no_cache else []
    )
    if cached_videos:
        log.info(f"   📦 {t('INFO_STEP2_CACHED', count=len(cached_videos))}")
        return cached_videos[: config.max_videos]

    # P2-13: 滚动节奏由 BatchConfig 注入（scroll_pause / max_scroll_rounds）
    videos: List[Dict] = get_user_videos(
        user_url,
        max_videos=config.max_videos,
        headless=config.headless,
        initial_wait=config.scroll_pause,
        scroll_pause=config.scroll_pause,
        max_scroll_rounds=config.max_scroll_rounds,
    )
    if videos:
        cache.save_user_videos(user_url, videos)
    return videos


def _sweep_orphan_parts(download_dir: Path, *, max_age_hours: float, log, i18n_t) -> int:
    """P2-5(2026-11-20 评审): 清扫批跑启动时残留的 ``*.part`` 孤儿文件。

    ``_http_download`` 的 finally 清理在 SIGKILL/断电时不运行,
    ``_cleanup_downloads`` 只 glob ``*.mp4`` —— 半截文件此前永久泄漏。
    只清超过 ``max_age_hours`` 的(新文件可能是并发 run 的,不动)。"""
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for p in sorted(download_dir.glob("*.part")):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _cleanup_downloads(config, results: List[Dict], download_dir: Path, t) -> None:
    """收尾清理下载的视频文件。

    P1-2: 失败视频的文件保留, 下一轮断点续传可重试;
    ``--keep-audio`` 时全部保留。
    """
    from mediascribe.logger import log

    if config.keep_audio:
        return
    log.info(f"\n🧹 {t('INFO_CLEANUP')}")
    from mediascribe.security import sanitize_filename

    protected = {
        sanitize_filename(f"{r['video_id']}.mp4") for r in results if r.get("status") != "success"
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


def _log_run_summary(
    results: List[Dict], videos: List[Dict], elapsed: float, summary_file, t
) -> int:
    """打印运行小计(总数/成功/失败/耗时/报告路径), 返回成功条数。"""
    from mediascribe.logger import log
    from mediascribe.progress import format_duration

    success_count = sum(1 for r in results if r.get("status") == "success")
    log.info("")
    log.info("=" * 60)
    log.success(f"{t('INFO_DONE')}")
    log.info(f"   {t('INFO_DONE_TOTAL', count=len(videos))}")
    log.info(f"   {t('INFO_DONE_SUCCESS', count=success_count)}")
    log.info(f"   {t('INFO_DONE_FAILED', count=len(videos) - success_count)}")
    log.info(f"   {t('INFO_DONE_ELAPSED', time=format_duration(elapsed))}")
    log.info(f"   {t('INFO_DONE_REPORT', path=summary_file)}")
    return success_count


def _close_browser_if_running() -> None:
    """关闭批量流程遗留的浏览器实例。

    P1-4: 仅在已有实例时关闭, 绝不凭空启动一次 Playwright。
    """
    try:
        from mediascribe.browser import BrowserManager

        BrowserManager.close_if_running()
    except Exception:
        pass


class _ResultRecorder:
    """批量结果登记器 — 串行与并发两条路径共用。

    持有跨线程共享的可变状态(结果列表 + 进度跟踪), ``record`` 整体
    加锁: workers>1 时结果追加、进度更新、缓存写回均线程安全
    (ProcessCache 内部亦有自己的锁)。JSON 模式下同步把每条结果
    喂给 :class:`~mediascribe.agent_output.AgentOutput`。
    """

    def __init__(
        self,
        tracker,
        cache,
        log,
        t,
        agent_out=None,
        workers: int = 1,
        events: Optional[Any] = None,
    ):
        self.results: List[Dict] = []
        self.tracker = tracker
        self.cache = cache
        self.log = log
        self.t = t
        self.agent_out = agent_out
        self.workers = workers
        self.events = events
        self._lock = threading.Lock()
        self._bot_wall_streak = 0  # P1-5: 连续风控命中次数 → 指数退避

    def record(self, result: Dict, task_time: float, worker_name: str) -> None:
        """登记单条结果: 进度/缓存/JSON 输出 + 运行小计日志。

        P1-5(2026-11-20 评审): ``failure_class == "bot_wall"`` 时整批
        指数退避暂停一拍(1s→2s→…→60s 封顶) —— 重试无益,放慢节奏才是
        对风控的正解。"""
        from mediascribe.progress import format_duration

        with self._lock:
            self.results.append(result)
            self.tracker.update(success=result["status"] == "success", task_time=task_time)

            # JSON 模式：记录每条结果
            if self.agent_out is not None:
                self.agent_out.add_video(
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

            # 保存到缓存(含失败类别 —— gone 类下轮断点续传直接跳过)
            self.cache.mark_processed(
                video_id=result["video_id"],
                video_url=result.get("url", ""),
                transcript_path=result.get("transcript"),
                audio_path=result.get("audio"),
                success=result["status"] == "success",
                failure_class=result.get("failure_class"),
            )
            if result.get("status") in {"success", "failed"} and self.events is not None:
                try:
                    error_code = result.get("error_code")
                    terminal = (
                        "success"
                        if result["status"] == "success"
                        else ("no_speech" if error_code == "NO_SPEECH" else "failed")
                    )
                    self.events.record_finished(
                        result=terminal,
                        stage=result.get("stage"),
                        error_code=error_code,
                    )
                except Exception:  # pragma: no cover - telemetry must not affect batch
                    import logging

                    logging.getLogger(__name__).debug(
                        "batch terminal event recording failed", exc_info=True
                    )
            if result.get("failure_class") == "bot_wall":
                self._bot_wall_streak += 1
                backoff = min(60.0, 2.0**self._bot_wall_streak)
            else:
                self._bot_wall_streak = 0
                backoff = 0.0
            # 锁外退避:风控命中 = 该平台整体被限流,整批暂停一拍。
            # 并发 worker 都要过这把锁拿记录,退避期间下一轮请求自然被推后。
            if backoff > 0:
                self.log.warning(self.t("WARN_BOT_WALL_BACKOFF", seconds=int(backoff)))
        if backoff > 0:
            time.sleep(backoff)

            print(self.tracker.render(f"{result.get('video_id', '')}"), end="", flush=True)
            print()  # 换行
            elapsed = format_duration(self.tracker.get_elapsed())
            worker_prefix = f"[worker:{worker_name}] " if self.workers > 1 else ""
            self.log.info(
                f"   {worker_prefix}"
                f"{self.t('INFO_RUNNING_TOTAL', done=self.tracker.completed, total=self.tracker.total, time=elapsed)}"
            )


def main(argv: Optional[List[str]] = None) -> int:
    """入口。

    v3.4.0: 接受可选 ``argv``（``python -m mediascribe archive`` 子命令
    复用本流程时传入）；``None`` 时照旧读 ``sys.argv[1:]``。
    """
    # Initialize i18n first (use default detection; --lang can override)
    from mediascribe.i18n import init_language, set_language, t

    init_language()

    # Early parse: we only need --lang to localize argparse output
    pre_args, _ = _LANG_PRE_PARSER.parse_known_args(argv)
    if pre_args.lang:
        set_language(pre_args.lang)

    parser = build_parser(t)
    args = parser.parse_args(argv)

    # Re-apply language in case --lang was passed after pre-parse
    if args.lang:
        set_language(args.lang)

    # 验证输入
    if not args.from_video and not args.user:
        if not args.config:
            parser.error(t("CLI_ERROR_NO_INPUT"))

    from mediascribe.batch_cache import ProcessCache
    from mediascribe.logger import log

    # 1. 加载配置并装配日志（优先级：CLI > 文件 > 环境变量 > 默认）
    config = _load_batch_config(args, t)

    # 1.4b 初始化 Agent 输出（--json 模式）
    agent_out = _init_agent_output(args, config)

    # 1.4c 引擎预检：无可用引擎时在发现/下载媒体前失败。
    from mediascribe.config import EngineUnavailableError, Settings
    from mediascribe.model_weights import ModelNotReadyError, ensure_model_ready

    try:
        engine_settings = Settings(model=config.whisper_model, engine=config.engine)
        if engine_settings.model != "auto":
            ensure_model_ready(
                engine_settings.engine,
                engine_settings.model,
                allow_download=config.allow_model_download,
            )
    except (EngineUnavailableError, ModelNotReadyError) as exc:
        from mediascribe.recovery import format_recovery, recovery_for_exception

        recovery = recovery_for_exception(exc)
        if agent_out is not None:
            agent_out.add_error(recovery["code"], recovery["reason"], recovery=recovery)
            agent_out.finish(False)
            agent_out.emit()
        else:
            log.error(format_recovery(exc))
        return 1
    if engine_settings.engine_notice:
        log.warning(engine_settings.engine_notice)

    # 1.4d 平台过滤器（--platform）
    platform_filter = _platform_filter_from_args(args, t)

    # 1.5 保存配置
    if args.save_config:
        save_path = Path(args.save_config)
        config.save(save_path)
        log.info(f"💾 {t('INFO_SAVE_CONFIG', path=save_path)}")

    # 显示配置
    _log_banner(config, args.no_cache, t)

    # 2. 初始化缓存
    cache = ProcessCache(cache_dir=Path(config.output_dir) / "cache")
    if args.clear_cache:
        cache.clear()
        log.warning(f"🧹 {t('INFO_CACHE_CLEARED')}")
    stats = cache.get_stats()
    log.info(
        f"📦 {t('INFO_CACHE_STATS', total=stats['total'], success=stats['success'], failed=stats['failed'])}"
    )

    # 3. 启动浏览器
    from mediascribe.browser import (
        get_media_url_fast,
        get_user_url_from_video,
        get_user_videos,
    )
    from mediascribe.progress import ProgressTracker
    from mediascribe.report import generate_summary_report
    from mediascribe.retry import download_media_with_retry
    from mediascribe.transcribe import transcribe_audio

    start_time = time.time()

    try:
        # 3.1 获取作者主页
        user_url = _resolve_user_url(args, config, get_user_url_from_video, agent_out, t)
        if user_url is None:
            return 1
        if agent_out is not None:
            agent_out.set_user_url(user_url)

        # 3.2 获取视频列表
        videos = _collect_videos(args, cache, config, user_url, get_user_videos, t)
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

        # P2-6(2026-11-20 评审): 磁盘空间守卫 —— 中间媒体可能很大,
        # 剩余不足提前失败,避免跑一半盘满、产物半截
        from mediascribe.platform_compat import free_disk_mb, min_free_disk_mb

        free_mb = free_disk_mb(download_dir)
        needed_mb = min_free_disk_mb()
        if free_mb is not None and free_mb < needed_mb:
            log.error(t("ERROR_DISK_SPACE", needed=needed_mb, free=free_mb))
            return 1

        # P2-5: 孤儿清扫 —— SIGKILL/断电漏下的 .part 半截文件,
        # 超 24h 即清(新文件可能是并发 run 的,不动)
        removed_parts = _sweep_orphan_parts(download_dir, max_age_hours=24, log=log, i18n_t=t)
        if removed_parts:
            log.info(t("INFO_ORPHAN_PARTS_SWEPT", count=removed_parts))

        # 进度跟踪（desc 经 i18n 注入，P2-15）
        tracker = ProgressTracker(total=len(videos), desc=t("INFO_PROGRESS_DESC"))
        workers = max(1, int(getattr(config, "workers", 1) or 1))
        from mediascribe.events import EventRecorder

        batch_events = EventRecorder(surface="batch")
        recorder = _ResultRecorder(
            tracker,
            cache,
            log,
            t,
            agent_out=agent_out,
            workers=workers,
            events=batch_events,
        )
        try:
            batch_events.record_first_run()
        except Exception:  # pragma: no cover - telemetry must not affect batch
            import logging

            logging.getLogger(__name__).debug("batch first-run event failed", exc_info=True)

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

        if workers > 1:
            # P2-6: workers>1 时逐视频并发（默认 1 保持原串行行为）
            from concurrent.futures import ThreadPoolExecutor, as_completed

            log.info(f"🚵 并发 workers={workers}")
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="worker") as pool:
                futures = {
                    pool.submit(_run_one, i, video): video for i, video in enumerate(videos, 1)
                }
                for fut in as_completed(futures):
                    result, task_time, worker_name = fut.result()
                    recorder.record(result, task_time, worker_name)
        else:
            for i, video in enumerate(videos, 1):
                print(tracker.render(f"{video['video_id']}"), end="", flush=True)
                result, task_time, worker_name = _run_one(i, video)
                recorder.record(result, task_time, worker_name)

        results = recorder.results

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

        success_count = _log_run_summary(results, videos, time.time() - start_time, summary_file, t)

        # 3.6 清理（P1-2: 失败视频的文件保留，下一轮可重试）
        _cleanup_downloads(config, results, download_dir, t)

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
        from mediascribe.recovery import format_recovery, recovery_for_exception

        recovery = recovery_for_exception(e)
        log.error(format_recovery(e))
        if agent_out is not None:
            agent_out.add_error(recovery["code"], recovery["reason"], recovery=recovery)
            agent_out.finish(ok=False)
            agent_out.emit()
        return 1
    finally:
        _close_browser_if_running()


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
        from mediascribe.i18n import t as _t

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
        media_url = get_media_url_fn(
            video_url, headless=config.headless, timeout=config.max_wait_for_media
        )
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
        from mediascribe.security import check_url_safety, sanitize_filename

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
        try:
            ok = download_media_fn(media_url, audio_path, max_retries=config.max_retries)
        except Exception as exc:
            # P1-5: 下载异常按类别路由 —— gone(已删)/bot_wall(风控)/
            # transient(网络) 由缓存与报告分别处理,不再是无类型异常
            from mediascribe.downloaders.download_errors import classify_exception

            cls_err = classify_exception(exc)
            from mediascribe.recovery import recovery_for_exception

            recovery = recovery_for_exception(cls_err)
            log.error(i18n_t("ERROR_VIDEO_DOWNLOAD", index=index, total=total, id=video_id))
            return {
                "video_id": video_id,
                "url": video_url,
                "platform": platform,
                "status": "failed",
                "stage": "download",
                "failure_class": cls_err.category,
                "error_code": recovery["code"],
                "recovery": recovery,
                "error": str(exc),
            }
        if not ok:
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
        from mediascribe.recovery import recovery_for_exception

        recovery = recovery_for_exception(e)
        log.error(i18n_t("ERROR_VIDEO_EXCEPTION", index=index, total=total, id=video_id, error=e))
        return {
            "video_id": video_id,
            "url": video_url,
            "platform": platform,
            "status": "failed",
            "stage": "exception",
            "error_code": recovery["code"],
            "recovery": recovery,
            "error": str(e),
        }


def _detect_platform(url: str) -> str:
    """
    Best-effort platform detection for `--json` output.
    Returns one of the stable PLATFORM_* constants.
    """
    from mediascribe.url_utils import match_platform

    platform = match_platform(url)
    if platform:
        return platform
    if not url:
        return "unknown"
    if url.lower().startswith(("http://", "https://")):
        return "unknown"
    return "local"


if __name__ == "__main__":
    sys.exit(main())
