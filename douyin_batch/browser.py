"""
浏览器管理模块 - 复用浏览器实例，避免重复启动

P1-14 收敛说明：
- ``get_user_url_from_video`` / ``get_user_videos``（作者主页解析）是
  douyin_batch 专属逻辑，核心包 ``video2text.downloaders.douyin`` 不覆盖，保留。
- ``get_media_url_fast`` 也**保留不委托核心**：它依赖本模块的 ``BrowserManager``
  单例复用浏览器，并带 ``--disable-blink-features=AutomationControlled`` 等反爬参数；
  核心 ``DouyinDownloader._extract_media_url_with_browser`` 每次新建浏览器且不含这些
  反爬参数，硬改会破坏反爬行为。因此这里保持独立实现，仅复核逻辑即可。
"""
import re
import time
from typing import Dict, List, Optional


class BrowserManager:
    """浏览器管理器 - 单例模式复用浏览器实例"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, headless: bool = True):
        if hasattr(self, "_initialized") and self._initialized:
            return

        from playwright.sync_api import sync_playwright

        self._headless = headless
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        self._initialized = True

    def new_page(self):
        """创建新页面"""
        return self._context.new_page()

    def close(self):
        """关闭浏览器"""
        try:
            self._context.close()
            self._browser.close()
            self._playwright.stop()
        except Exception:
            pass
        BrowserManager._instance = None
        self._initialized = False

    def __del__(self):
        self.close()


def get_user_url_from_video(video_url: str, headless: bool = True) -> Optional[str]:
    """从单个视频URL提取作者主页URL"""
    browser_mgr = BrowserManager(headless=headless)
    page = browser_mgr.new_page()
    user_url = None

    try:
        page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
        # 智能等待：检查页面是否包含用户ID
        for _ in range(10):
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
            if user_url:
                break
            time.sleep(0.5)
    except Exception as e:
        print(f"   ⚠️ 提取错误: {e}")
    finally:
        try:
            page.close()
        except Exception:
            pass

    return user_url


def get_user_videos(user_url: str, max_videos: int = 20, headless: bool = True) -> List[Dict]:
    """获取作者主页所有往期视频"""
    browser_mgr = BrowserManager(headless=headless)
    page = browser_mgr.new_page()

    videos = []
    seen_urls = set()

    try:
        page.goto(user_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)  # 给页面一些时间渲染

        scroll_rounds = (max_videos // 12) + 3
        no_change_count = 0

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

            # 检测是否还在加载（连续2轮没新增就停止）
            if new_count == 0:
                no_change_count += 1
                if no_change_count >= 2:
                    print("   ⚠️ 无更多新视频，停止滚动")
                    break
            else:
                no_change_count = 0

            # 滚动到底部
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # 等待新内容加载
            time.sleep(2)

    except Exception as e:
        print(f"   ❌ 抓取错误: {e}")
    finally:
        try:
            page.close()
        except Exception:
            pass

    return videos[:max_videos]


def get_media_url_fast(video_url: str, headless: bool = True, timeout: int = 15) -> Optional[str]:
    """快速获取单个抖音视频的真实媒体URL（音频）"""
    browser_mgr = BrowserManager(headless=headless)
    page = browser_mgr.new_page()

    captured_urls = []

    def handle_response(response):
        try:
            if "douyinvod.com" in response.url and "media-audio" in response.url:
                captured_urls.append(response.url)
        except Exception:
            pass

    page.on("response", handle_response)

    try:
        page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
        # 智能等待：每0.5秒检查一次是否已捕获
        elapsed = 0
        while elapsed < timeout and not captured_urls:
            time.sleep(0.5)
            elapsed += 0.5
    except Exception:
        pass
    finally:
        try:
            page.close()
        except Exception:
            pass

    return captured_urls[0] if captured_urls else None
