"""
浏览器管理模块 - 复用浏览器实例，避免重复启动

P1-14 收敛说明：
- ``get_user_url_from_video`` / ``get_user_videos``（作者主页解析）是
  douyin_batch 专属逻辑，核心包 ``video2text.downloaders.douyin`` 不覆盖，保留。
- ``get_media_url_fast`` 也**保留不委托核心**：它依赖本模块的 ``BrowserManager``
  单例复用浏览器，并带 ``--disable-blink-features=AutomationControlled`` 等反爬参数；
  核心 ``DouyinDownloader._extract_media_url_with_browser`` 每次新建浏览器且不含这些
  反爬参数，硬改会破坏反爬行为。因此这里保持独立实现，仅复核逻辑即可。

v3.2.0g:
- ``print`` → ``logging.getLogger(__name__)``，--json 模式下不污染 stdout
- 单例已存在但 ``headless`` 参数不一致时：logger.warning 并沿用现有实例
  （不再静默忽略参数差异，也绝不重复启动浏览器）
- ``get_user_videos`` 的初始等待 / 滚动停顿 / 滚动轮数改为参数注入
  （v3 从 ``BatchConfig.scroll_pause`` / ``max_scroll_rounds`` 传入）
- 新增 ``BrowserManager.close_if_running()``：只在已有实例时关闭，
  不会凭空启动一次浏览器（收尾清理用）
"""
import logging
import re
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class BrowserManager:
    """浏览器管理器 - 单例模式复用浏览器实例"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, headless: bool = True):
        if hasattr(self, "_initialized") and self._initialized:
            if getattr(self, "_headless", headless) != headless:
                logger.warning(
                    "BrowserManager 已存在（headless=%s），忽略新的 headless=%s 参数，"
                    "沿用现有浏览器实例",
                    self._headless, headless,
                )
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

    @classmethod
    def close_if_running(cls) -> None:
        """仅在已有实例时关闭浏览器（不会触发新的 Playwright 启动）。"""
        if cls._instance is not None:
            cls._instance.close()

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
        logger.warning("提取作者主页失败: %s (%s)", video_url, e)
    finally:
        try:
            page.close()
        except Exception:
            pass

    return user_url


def get_user_videos(
    user_url: str,
    max_videos: int = 20,
    headless: bool = True,
    initial_wait: float = 3.0,
    scroll_pause: float = 2.0,
    max_scroll_rounds: Optional[int] = None,
) -> List[Dict]:
    """获取作者主页所有往期视频

    v3.2.0g: ``initial_wait``（首页渲染等待秒数）、``scroll_pause``
    （每次滚动后的停顿秒数）、``max_scroll_rounds``（最大滚动轮数）
    由调用方注入（v3 传 ``BatchConfig.scroll_pause`` / ``max_scroll_rounds``），
    不再硬编码 sleep(3)/sleep(2) 与经验公式。
    """
    browser_mgr = BrowserManager(headless=headless)
    page = browser_mgr.new_page()

    videos = []
    seen_urls = set()

    try:
        page.goto(user_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(initial_wait)  # 给页面一些时间渲染

        if max_scroll_rounds is None:
            # 兼容旧行为：按 max_videos 估算滚动轮数
            scroll_rounds = (max_videos // 12) + 3
        else:
            scroll_rounds = max(1, int(max_scroll_rounds))
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

            logger.info("第 %d 轮: +%d 个 (累计 %d)", round_idx + 1, new_count, len(videos))

            if len(videos) >= max_videos:
                break

            # 检测是否还在加载（连续2轮没新增就停止）
            if new_count == 0:
                no_change_count += 1
                if no_change_count >= 2:
                    logger.info("无更多新视频，停止滚动")
                    break
            else:
                no_change_count = 0

            # 滚动到底部
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # 等待新内容加载
            time.sleep(scroll_pause)

    except Exception as e:
        logger.warning("抓取作者视频失败: %s (%s)", user_url, e)
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
    except Exception as e:
        logger.debug("媒体 URL 捕获中断: %s (%s)", video_url, e)
    finally:
        try:
            page.close()
        except Exception:
            pass

    return captured_urls[0] if captured_urls else None
