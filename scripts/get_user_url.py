#!/usr/bin/env python3
"""从视频URL提取作者主页"""

import re
import time


def get_user_url_from_video(video_url, headless=True):
    from playwright.sync_api import sync_playwright

    user_url = None
    user_info = {}

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

            # 尝试提取sec_uid
            # 抖音真实的 sec_uid 是类似 MS4wLjABAAAA... 的 base64 编码
            patterns = [
                r'"sec_uid":"(MS4wLjABAAAA[A-Za-z0-9_\-]+)"',
                r"sec_uid=(MS4wLjABAAAA[A-Za-z0-9_\-]+)",
                r"/user/(MS4wLjABAAAA[A-Za-z0-9_\-]+)",
            ]
            for pattern in patterns:
                matches = re.findall(pattern, html)
                if matches:
                    user_info["sec_uid"] = matches[0]
                    user_url = f"https://www.douyin.com/user/{matches[0]}"
                    break

            # 提取作者昵称
            name_match = re.search(r'"nickname"\s*:\s*"([^"]+)"', html)
            if name_match:
                user_info["nickname"] = name_match.group(1)

            # 如果还没找到，保存HTML以便分析
            if not user_info.get("sec_uid") or user_info.get("sec_uid") == "self":
                with open("output/douyin_page.html", "w", encoding="utf-8") as f:
                    f.write(html)
                print("已保存HTML到 output/douyin_page.html")

        except Exception as e:
            print(f"错误: {e}")
        finally:
            try:
                page.close()
                context.close()
                browser.close()
            except Exception:
                pass

    return user_url, user_info


if __name__ == "__main__":
    url = "https://www.douyin.com/video/7647042350057661873"
    user_url, info = get_user_url_from_video(url)
    print(f"作者主页: {user_url}")
    print(f"作者信息: {info}")
