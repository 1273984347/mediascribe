#!/usr/bin/env python3
"""
抖音作者主页批量视频抓取器
- 输入作者主页URL
- 自动获取所有往期视频链接
- 支持滚动加载
- 返回视频列表
"""
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List


def get_user_videos(user_url: str, max_videos: int = 30, headless: bool = False) -> List[Dict]:
    """
    获取抖音作者主页的所有往期视频

    Args:
        user_url: 作者主页URL，如 https://www.douyin.com/user/MS4wLjABAAAAxxxx
        max_videos: 最多获取视频数量
        headless: 是否使用无头模式

    Returns:
        视频列表 [{'title': '...', 'url': '...', 'duration': ...}, ...]
    """
    from playwright.sync_api import sync_playwright

    print(f"🎬 正在打开作者主页: {user_url}")

    videos = []
    seen_urls = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        page = context.new_page()

        try:
            page.goto(user_url, wait_until="domcontentloaded", timeout=30000)
            print(f"   ✅ 页面已加载: {page.url}")

            # 等待页面渲染
            time.sleep(3)

            # 多轮滚动，加载更多视频
            scroll_rounds = (max_videos // 12) + 2  # 大约每屏12个视频
            print(f"   ⏳ 将进行 {scroll_rounds} 轮滚动加载...")

            for round_idx in range(scroll_rounds):
                # 提取当前页面的视频链接
                # 抖音作者页面的视频链接格式: <a href="/video/xxxxxxxxx">
                html_content = page.content()

                # 找到所有视频链接
                video_pattern = re.compile(r'href="(/video/\d+)"')
                matches = video_pattern.findall(html_content)

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

                print(f"   第 {round_idx+1} 轮: 新增 {new_count} 个，累计 {len(videos)} 个")

                if len(videos) >= max_videos:
                    print(f"   ✅ 已达到目标数量 {max_videos}")
                    break

                # 滚动到底部加载更多
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

    print(f"\n   📊 共获取 {len(videos)} 个视频链接")
    return videos[:max_videos]


def get_video_info(video_url: str, headless: bool = False) -> Dict:
    """
    获取单个视频的详细信息（标题、时长等）
    """
    from playwright.sync_api import sync_playwright

    info = {
        "url": video_url,
        "title": None,
        "duration": None,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--no-sandbox"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        try:
            page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            # 提取标题
            try:
                title = page.evaluate("""
                    () => {
                        const el = document.querySelector('[data-e2e="video-desc"]') ||
                                  document.querySelector('h1') ||
                                  document.querySelector('.video-info-title');
                        return el ? el.innerText : null;
                    }
                """)
                if title:
                    info["title"] = title[:100]
            except Exception:
                pass

        except Exception as e:
            print(f"   ⚠️ 获取视频信息失败: {e}")
        finally:
            try:
                page.close()
                context.close()
                browser.close()
            except Exception:
                pass

    return info


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python douyin_user_scraper.py <作者主页URL> [最大视频数]")
        print()
        print("示例:")
        print("  python douyin_user_scraper.py https://www.douyin.com/user/MS4wLjABAAAAxxxx 20")
        sys.exit(1)

    user_url = sys.argv[1]
    max_videos = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    videos = get_user_videos(user_url, max_videos=max_videos, headless=False)

    if videos:
        # 保存到JSON文件
        output_file = Path("output/user_videos.json")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(videos, f, ensure_ascii=False, indent=2)

        print(f"\n✅ 视频列表已保存到: {output_file}")
        print("\n前5个视频:")
        for i, v in enumerate(videos[:5], 1):
            print(f"  {i}. {v['url']}")
