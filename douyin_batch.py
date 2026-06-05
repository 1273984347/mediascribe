#!/usr/bin/env python3
"""
抖音作者主页批量转录 - 完整流程
1. 从视频URL提取作者主页
2. 获取作者所有视频
3. 批量下载并转录
4. 生成汇总报告
"""
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests


def get_user_url_from_video(video_url: str, headless: bool = True) -> Optional[str]:
    """从单个视频URL提取作者主页URL"""
    from playwright.sync_api import sync_playwright

    user_url = None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        try:
            page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)
            html = page.content()

            patterns = [
                r'"sec_uid":"(MS4wLjABAAAA[A-Za-z0-9_\-]+)"',
                r'sec_uid=(MS4wLjABAAAA[A-Za-z0-9_\-]+)',
            ]
            for pattern in patterns:
                matches = re.findall(pattern, html)
                if matches:
                    user_url = f"https://www.douyin.com/user/{matches[0]}"
                    break
        except Exception as e:
            print(f"   ❌ 错误: {e}")
        finally:
            try:
                page.close()
                context.close()
                browser.close()
            except Exception:
                pass

    return user_url


def get_user_videos(user_url: str, max_videos: int = 20, headless: bool = True) -> List[Dict]:
    """获取作者主页所有往期视频"""
    from playwright.sync_api import sync_playwright

    print("\n🎬 正在获取作者往期视频...")
    print(f"   URL: {user_url}")
    print(f"   目标: 最多 {max_videos} 个")

    videos = []
    seen_urls = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        page = context.new_page()

        try:
            page.goto(user_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            # 滚动加载
            scroll_rounds = (max_videos // 12) + 3
            for round_idx in range(scroll_rounds):
                html = page.content()
                pattern = re.compile(r'href="(/video/\d+)"')
                matches = pattern.findall(html)

                new_count = 0
                for match in matches:
                    full_url = f"https://www.douyin.com{match}"
                    if full_url not in seen_urls:
                        seen_urls.add(full_url)
                        videos.append({
                            "url": full_url,
                            "video_id": match.replace("/video/", ""),
                        })
                        new_count += 1

                print(f"   第 {round_idx+1} 轮: +{new_count} 个 (累计 {len(videos)})")

                if len(videos) >= max_videos:
                    break

                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2.5)

        except Exception as e:
            print(f"   ❌ 抓取错误: {e}")
        finally:
            try:
                page.close()
                context.close()
                browser.close()
            except Exception:
                pass

    return videos[:max_videos]


def get_media_url(video_url: str, headless: bool = True) -> Optional[str]:
    """获取单个抖音视频的真实媒体URL（音频）"""
    from playwright.sync_api import sync_playwright

    captured_urls = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        def handle_response(response):
            try:
                if "douyinvod.com" in response.url and "media-audio" in response.url:
                    captured_urls.append(response.url)
            except Exception:
                pass

        page.on("response", handle_response)

        try:
            page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(7)
        except Exception:
            pass
        finally:
            try:
                page.close()
                context.close()
                browser.close()
            except Exception:
                pass

    return captured_urls[0] if captured_urls else None


def download_media(media_url: str, output_path: Path) -> bool:
    """下载媒体文件"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.douyin.com/",
    }

    try:
        response = requests.get(media_url, headers=headers, stream=True, timeout=30)
        response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return True
    except Exception:
        return False


def transcribe_audio(audio_path: Path) -> Optional[Path]:
    """转录音频文件"""
    sys.path.insert(0, str(Path(__file__).parent))
    from video2text import Pipeline, Settings

    try:
        settings = Settings()
        pipeline = Pipeline(settings)
        result = pipeline.transcribe(str(audio_path), language="zh")
        return result.transcript_path
    except Exception:
        return None


def main():
    if len(sys.argv) < 2:
        print("=" * 60)
        print("📚 抖音作者往期内容批量转录工具")
        print("=" * 60)
        print()
        print("用法:")
        print("  python douyin_batch.py <作者主页URL或视频URL> [最大视频数]")
        print()
        print("示例:")
        print('  # 直接提供作者主页')
        print("  python douyin_batch.py https://www.douyin.com/user/MS4wLjABAAAAxxxx 10")
        print()
        print("  # 从视频URL自动获取作者主页")
        print("  python douyin_batch.py https://v.douyin.com/xxxxx/ 10")
        sys.exit(1)

    input_url = sys.argv[1]
    max_videos = 10  # 默认10个，避免太长
    if len(sys.argv) > 2 and sys.argv[2].isdigit():
        max_videos = int(sys.argv[2])

    print("=" * 60)
    print("📚 抖音作者往期内容批量转录")
    print("=" * 60)

    # 1. 判断输入类型
    if "/user/" in input_url:
        user_url = input_url
    else:
        # 视频URL，先获取作者主页
        print("\n🔍 步骤1: 从视频URL提取作者主页...")
        user_url = get_user_url_from_video(input_url, headless=True)
        if not user_url:
            print("❌ 无法获取作者主页")
            sys.exit(1)
        print(f"   ✅ 作者主页: {user_url}")

    # 2. 获取所有视频
    print("\n📋 步骤2: 获取作者往期视频...")
    videos = get_user_videos(user_url, max_videos=max_videos, headless=True)

    if not videos:
        print("❌ 未获取到任何视频")
        sys.exit(1)

    # 保存列表
    list_file = Path("output/user_videos_list.json")
    list_file.parent.mkdir(parents=True, exist_ok=True)
    with open(list_file, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)
    print(f"\n💾 视频列表已保存: {list_file}")

    # 3. 批量处理
    print(f"\n🎬 步骤3: 开始批量下载并转录 ({len(videos)} 个视频)...")
    print("=" * 60)

    download_dir = Path("output/downloads/user_videos")
    download_dir.mkdir(parents=True, exist_ok=True)

    results = []
    success_count = 0

    for i, video in enumerate(videos, 1):
        print(f"\n[{i}/{len(videos)}] {video['video_id']}")
        print(f"   {video['url']}")

        # 获取媒体URL
        media_url = get_media_url(video["url"], headless=True)
        if not media_url:
            print("   ❌ 无法获取媒体URL")
            results.append({"video_id": video["video_id"], "status": "failed", "stage": "media_url"})
            continue

        # 下载
        audio_path = download_dir / f"{video['video_id']}.mp4"
        if not download_media(media_url, audio_path):
            print("   ❌ 下载失败")
            results.append({"video_id": video["video_id"], "status": "failed", "stage": "download"})
            continue

        size_mb = audio_path.stat().st_size / 1024 / 1024
        print(f"   📦 已下载 ({size_mb:.2f} MB)")

        # 转录
        transcript_path = transcribe_audio(audio_path)
        if transcript_path:
            print("   ✅ 转录完成")
            results.append({
                "video_id": video["video_id"],
                "status": "success",
                "transcript": str(transcript_path),
                "audio": str(audio_path),
            })
            success_count += 1
        else:
            print("   ❌ 转录失败")
            results.append({"video_id": video["video_id"], "status": "failed", "stage": "transcribe"})

    # 4. 汇总
    print("\n" + "=" * 60)
    print("📊 批量处理完成")
    print("=" * 60)
    print(f"✅ 成功: {success_count}/{len(videos)}")
    print(f"❌ 失败: {len(videos) - success_count}/{len(videos)}")

    # 保存结果
    result_file = Path("output/user_videos_result.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump({
            "user_url": user_url,
            "total": len(videos),
            "success": success_count,
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n📋 结果已保存: {result_file}")


if __name__ == "__main__":
    main()
