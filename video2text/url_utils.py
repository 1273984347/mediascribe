"""
URL 工具模块 - 处理短链接、重定向等
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Optional


def resolve_short_url(url: str, timeout: int = 10) -> Optional[str]:
    """
    解析短链接，获取重定向后的真实 URL

    Args:
        url: 短链接 URL
        timeout: 请求超时时间（秒）

    Returns:
        解析后的真实 URL，或 None（如果解析失败）
    """
    if not url.startswith("http"):
        url = "https://" + url

    # 检查是否是短链接（b23.tv 或 v.douyin.com）
    if "b23.tv" not in url and "v.douyin.com" not in url:
        return url

    try:
        print(f"🔍 解析短链接: {url}")

        # 创建请求，设置 User-Agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        req = urllib.request.Request(url, headers=headers, method="HEAD")

        # 发送请求，允许自动重定向
        with urllib.request.urlopen(req, timeout=timeout) as response:
            real_url = response.url
            print(f"✅ 解析成功: {real_url}")
            return real_url

    except urllib.error.HTTPError as e:
        # HEAD 请求可能被拒绝，尝试 GET 请求
        if e.code in [403, 405, 412]:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    real_url = response.url
                    print(f"✅ 解析成功: {real_url}")
                    return real_url
            except Exception as e2:
                print(f"⚠️ GET 请求也失败: {e2}")
        else:
            print(f"⚠️ HTTP 错误: {e}")

    except Exception as e:
        print(f"⚠️ 解析短链接失败: {e}")

    return None


def extract_bvid(url: str) -> Optional[str]:
    """
    从 URL 中提取 BV 号

    Args:
        url: Bilibili URL

    Returns:
        BV 号，或 None
    """
    # 匹配 BV 号格式：BV 开头，后跟 10 个字符
    bv_match = re.search(r"BV[a-zA-Z0-9]{10}", url)
    if bv_match:
        return bv_match.group(0)
    return None


def is_short_url(url: str) -> bool:
    """
    判断是否是短链接

    Args:
        url: URL

    Returns:
        是否是短链接
    """
    short_domains = [
        "b23.tv",           # B站短链
        "v.douyin.com",     # 抖音短链
        "t.cn",             # 微博短链
        "youtu.be",         # YouTube 短链
    ]
    return any(domain in url for domain in short_domains)


def normalize_bilibili_url(url: str) -> tuple[Optional[str], Optional[str]]:
    """
    规范化 Bilibili URL

    Args:
        url: 原始 URL

    Returns:
        (规范化后的 URL, BV 号)
    """
    # 先解析短链接
    real_url = resolve_short_url(url)
    if not real_url:
        real_url = url

    # 提取 BV 号
    bvid = extract_bvid(real_url)

    # 如果有 BV 号，构建标准 URL
    if bvid:
        standard_url = f"https://www.bilibili.com/video/{bvid}"
        return standard_url, bvid

    return real_url, None


def normalize_url(url: str) -> Optional[str]:
    """
    通用 URL 规范化（支持 Bilibili、抖音等

    Args:
        url: 原始 URL

    Returns:
        规范化后的 URL
    """
    # 先解析短链接
    real_url = resolve_short_url(url)
    if not real_url:
        real_url = url

    # 检查是否是 Bilibili 并提取 BV 号
    if "bilibili.com" in real_url or "b23.tv" in real_url:
        bvid = extract_bvid(real_url)
        if bvid:
            return f"https://www.bilibili.com/video/{bvid}"

    return real_url
