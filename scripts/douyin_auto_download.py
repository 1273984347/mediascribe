#!/usr/bin/env python3
"""
使用 Playwright 自动化获取抖音视频的真实媒体URL
无需 cookies！完全自动化！
"""
import sys
import time
from pathlib import Path

import requests


def get_douyin_media_url(url: str, headless: bool = False) -> dict:
    """
    使用 Playwright 打开抖音视频并提取真实媒体URL

    Args:
        url: 抖音视频URL
        headless: 是否使用无头模式

    Returns:
        dict: 包含音频和视频URL的字典
    """
    from playwright.sync_api import sync_playwright

    print(f"🎬 正在打开: {url}")

    media_urls = {
        "audio": None,
        "video": None,
        "page_url": None,
    }

    with sync_playwright() as p:
        # 使用 chromium，关闭 headless 以避免反爬
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

        # 拦截网络请求以捕获媒体URL
        captured_urls = []

        def handle_response(response):
            try:
                resp_url = response.url
                # 捕获 douyinvod.com 的请求
                if "douyinvod.com" in resp_url:
                    captured_urls.append({
                        "url": resp_url,
                        "type": response.request.resource_type,
                    })
                    print(f"   📡 捕获到: {resp_url[:100]}...")
            except Exception:
                pass

        page = context.new_page()
        page.on("response", handle_response)

        try:
            # 导航到视频
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            media_urls["page_url"] = page.url
            print(f"   ✅ 页面已加载: {page.url}")

            # 等待视频播放器加载
            print("   ⏳ 等待视频加载...")
            time.sleep(3)

            # 尝试点击播放按钮（如果有）
            try:
                play_button = page.query_selector("xg-icon[class*='play']") or \
                              page.query_selector(".play-button") or \
                              page.query_selector("video")
                if play_button:
                    play_button.click()
                    print("   ▶️ 已点击播放")
                    time.sleep(3)
            except Exception as e:
                print(f"   ⚠️ 点击播放失败: {e}")

            # 等待更长时间以捕获完整的网络请求
            print("   ⏳ 等待媒体URL捕获...")
            time.sleep(5)

        except Exception as e:
            print(f"   ❌ 页面加载错误: {e}")
        finally:
            try:
                page.close()
                context.close()
                browser.close()
            except Exception:
                pass

    # 分析捕获的URL
    print(f"\n   📊 共捕获 {len(captured_urls)} 个 douyinvod URL")

    for item in captured_urls:
        url_str = item["url"]
        if "media-audio" in url_str:
            media_urls["audio"] = url_str
            print(f"   🎵 音频URL: {url_str[:80]}...")
        elif "media-video" in url_str:
            media_urls["video"] = url_str
            print(f"   🎬 视频URL: {url_str[:80]}...")

    return media_urls


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
        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0

        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        print(f"\r   📥 下载进度: {percent:.1f}%", end="")

        print()
        size_mb = output_path.stat().st_size / 1024 / 1024
        print(f"   ✅ 下载完成: {output_path} ({size_mb:.2f} MB)")
        return True
    except Exception as e:
        print(f"   ❌ 下载失败: {e}")
        return False


if __name__ == "__main__":
    # 默认使用提供的抖音链接
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = "https://www.douyin.com/video/7647042350057661873"

    # 获取媒体URL
    media_urls = get_douyin_media_url(url, headless=False)

    # 优先下载音频（文件更小）
    if media_urls["audio"]:
        output = Path("output/downloads/douyin_auto_audio.mp4")
        if download_media(media_urls["audio"], output):
            print(f"\n✅ 成功！音频文件: {output}")
    elif media_urls["video"]:
        output = Path("output/downloads/douyin_auto_video.mp4")
        if download_media(media_urls["video"], output):
            print(f"\n✅ 成功！视频文件: {output}")
    else:
        print("\n❌ 未能捕获到任何媒体URL")
        print("\n💡 提示：请确保：")
        print("   1. Playwright 已安装: pip install playwright")
        print("   2. 浏览器已安装: playwright install chromium")
        print("   3. 视频可以正常访问")
