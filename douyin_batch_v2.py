#!/usr/bin/env python3
"""
抖音作者主页批量转录 v2.0 - 优化版
核心优化：
1. 浏览器实例复用（节省3-5秒/视频）
2. 智能等待（替代固定sleep）
3. 自动重试机制
4. 转录器复用（避免重复加载Whisper）
5. 汇总报告生成
6. 模块化代码结构
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List


def main():
    parser = argparse.ArgumentParser(
        description="抖音作者主页批量转录工具 v2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 从视频URL自动获取作者主页
  python douyin_batch_v2.py --from-video "https://v.douyin.com/xxxxx/" -n 10

  # 直接提供作者主页
  python douyin_batch_v2.py --user "https://www.douyin.com/user/MS4wLjABAAAAxxxx" -n 20

  # 并发处理（实验性）
  python douyin_batch_v2.py --from-video "https://v.douyin.com/xxxxx/" -n 10 --workers 2
        """,
    )

    parser.add_argument("--from-video", metavar="URL", help="从视频URL自动获取作者主页")
    parser.add_argument("--user", metavar="URL", help="作者主页URL")
    parser.add_argument("-n", "--num", type=int, default=10, help="最大视频数量 (默认: 10)")
    parser.add_argument("--workers", type=int, default=1, help="并发数 (默认: 1，推荐 1-2)")
    parser.add_argument("--no-headless", action="store_true", help="禁用无头模式（显示浏览器）")
    parser.add_argument("--retries", type=int, default=3, help="下载重试次数 (默认: 3)")
    parser.add_argument("--output-dir", default="output", help="输出目录")
    parser.add_argument("--keep-audio", action="store_true", help="保留下载的音频文件")

    args = parser.parse_args()

    # 验证输入
    if not args.from_video and not args.user:
        parser.error("请提供 --from-video 或 --user 参数")

    headless = not args.no_headless

    print("=" * 60)
    print("📚 抖音作者往期内容批量转录 v2.0")
    print("=" * 60)
    print(f"   配置: workers={args.workers}, max_videos={args.num}")
    print(f"   浏览器: {'无头模式' if headless else '显示模式'}")
    print(f"   重试次数: {args.retries}")
    print()

    # 动态导入模块（确保项目根目录在 sys.path）
    project_root = Path(__file__).parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from douyin_batch.browser import (
        BrowserManager,
        get_user_url_from_video,
        get_user_videos,
    )
    from douyin_batch.report import generate_summary_report

    start_time = time.time()

    try:
        # 1. 获取作者主页
        if args.from_video:
            print("🔍 步骤1: 从视频URL提取作者主页...")
            user_url = get_user_url_from_video(args.from_video, headless=headless)
            if not user_url:
                print("❌ 无法获取作者主页")
                return 1
            print(f"   ✅ 作者主页: {user_url}")
        else:
            user_url = args.user
            print("🔍 步骤1: 使用提供的作者主页")
            print(f"   {user_url}")

        # 2. 获取所有视频
        print(f"\n📋 步骤2: 获取作者往期视频 (最多 {args.num} 个)...")
        videos = get_user_videos(user_url, max_videos=args.num, headless=headless)
        if not videos:
            print("❌ 未获取到任何视频")
            return 1
        print(f"   ✅ 共获取 {len(videos)} 个视频")

        # 3. 处理视频
        print(f"\n🎬 步骤3: 开始批量处理 ({len(videos)} 个视频)...")
        print("=" * 60)

        output_dir = Path(args.output_dir)
        download_dir = output_dir / "downloads" / "user_videos"
        download_dir.mkdir(parents=True, exist_ok=True)

        results = []

        if args.workers == 1:
            # 串行处理
            results = process_videos_sequential(
                videos, download_dir, headless, args.retries
            )
        else:
            # 并发处理
            results = process_videos_parallel(
                videos, download_dir, headless, args.retries, args.workers
            )

        # 4. 生成汇总报告
        print("\n" + "=" * 60)
        print("📊 生成汇总报告...")

        summary_file = generate_summary_report(
            user_url=user_url,
            results=results,
            output_dir=output_dir,
        )

        success_count = sum(1 for r in results if r.get("status") == "success")
        elapsed = time.time() - start_time

        print(f"\n{'=' * 60}")
        print("✅ 批量处理完成！")
        print(f"{'=' * 60}")
        print(f"   总数: {len(videos)}")
        print(f"   成功: {success_count}")
        print(f"   失败: {len(videos) - success_count}")
        print(f"   耗时: {elapsed:.1f} 秒")
        print(f"   汇总报告: {summary_file}")

        # 清理
        if not args.keep_audio:
            print("\n🧹 清理临时音频文件...")
            for f in download_dir.glob("*.mp4"):
                try:
                    f.unlink()
                except Exception:
                    pass

        return 0

    finally:
        # 确保浏览器被关闭
        try:
            BrowserManager().close()
        except Exception:
            pass


def process_videos_sequential(
    videos: List[Dict],
    download_dir: Path,
    headless: bool,
    max_retries: int,
) -> List[Dict]:
    """串行处理视频"""
    from douyin_batch.browser import get_media_url_fast
    from douyin_batch.retry import download_media_with_retry
    from douyin_batch.transcribe import transcribe_audio

    results = []

    for i, video in enumerate(videos, 1):
        print(f"\n[{i}/{len(videos)}] {video['video_id']}")
        result = process_single_video(
            video, i, len(videos), download_dir, headless, max_retries,
            get_media_url_fast, download_media_with_retry, transcribe_audio
        )
        results.append(result)

    return results


def process_videos_parallel(
    videos: List[Dict],
    download_dir: Path,
    headless: bool,
    max_retries: int,
    max_workers: int,
) -> List[Dict]:
    """并发处理视频（实验性，注意：浏览器共享可能导致问题）"""
    from douyin_batch.browser import get_media_url_fast
    from douyin_batch.retry import download_media_with_retry
    from douyin_batch.transcribe import transcribe_audio

    results = [None] * len(videos)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(
                process_single_video,
                video, i, len(videos), download_dir, headless, max_retries,
                get_media_url_fast, download_media_with_retry, transcribe_audio
            ): i
            for i, video in enumerate(videos)
        }

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = {
                    "video_id": videos[idx]["video_id"],
                    "url": videos[idx]["url"],
                    "status": "failed",
                    "stage": "executor",
                    "error": str(e),
                }

    return results


def process_single_video(
    video: Dict,
    index: int,
    total: int,
    download_dir: Path,
    headless: bool,
    max_retries: int,
    get_media_url_fn,
    download_media_fn,
    transcribe_fn,
) -> Dict:
    """处理单个视频"""
    video_id = video["video_id"]
    print(f"\n[{index}/{total}] {video_id}")
    print(f"   {video['url']}")

    # 1. 获取媒体URL
    media_url = get_media_url_fn(video["url"], headless=headless)
    if not media_url:
        print("   ❌ 无法获取媒体URL")
        return {"video_id": video_id, "url": video["url"], "status": "failed", "stage": "media_url"}

    # 2. 下载
    audio_path = download_dir / f"{video_id}.mp4"
    print("   📥 下载中...")
    if not download_media_fn(media_url, audio_path, max_retries=max_retries):
        return {"video_id": video_id, "url": video["url"], "status": "failed", "stage": "download"}

    size_mb = audio_path.stat().st_size / 1024 / 1024
    print(f"   📦 已下载 ({size_mb:.2f} MB)")

    # 3. 转录
    print("   🎤 转录中...")
    transcript_path = transcribe_fn(audio_path)
    if transcript_path:
        print(f"   ✅ 完成: {transcript_path.name}")
        return {
            "video_id": video_id,
            "url": video["url"],
            "status": "success",
            "transcript": str(transcript_path),
            "audio": str(audio_path),
        }
    else:
        return {"video_id": video_id, "url": video["url"], "status": "failed", "stage": "transcribe"}


if __name__ == "__main__":
    sys.exit(main())
