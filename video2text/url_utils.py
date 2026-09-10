"""
URL 工具模块 - 处理短链接、重定向等

v3.2.0g:
- ``print`` → ``logging.getLogger(__name__)``，--json 模式下不污染 stdout
- 短链域名判断改用 ``urlparse(...).hostname`` 精确匹配
  （原 ``"b23.tv" in url`` 子串匹配可被 ``http://b23.tv@127.0.0.1/`` 绕过）
"""
from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 需要重定向解析的短链域名（精确到主机名，含子域）
_RESOLVABLE_SHORT_DOMAINS = ("b23.tv", "v.douyin.com")

# 常见短链域名（is_short_url 判断用）
_SHORT_DOMAINS = ("b23.tv", "v.douyin.com", "t.cn", "youtu.be")


def _hostname(url: str) -> str:
    """取 URL 的主机名（小写）；解析失败返回空串。

    用 hostname 而不是子串匹配，避免 ``http://b23.tv@127.0.0.1/``
    （实际主机是 127.0.0.1）或 ``https://www.bilibili.com/b23.tv``
    （路径里的假域名）被误判。
    """
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:  # pragma: no cover - urlparse 极少抛异常
        return ""


def _host_matches(url: str, domains: tuple) -> bool:
    """主机名精确（或子域）匹配给定域名列表。"""
    host = _hostname(url)
    if not host:
        return False
    return any(host == d or host.endswith(f".{d}") for d in domains)


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

    # 检查是否是可解析的短链接（b23.tv / v.douyin.com，按主机名精确匹配）
    if not _host_matches(url, _RESOLVABLE_SHORT_DOMAINS):
        return url

    try:
        logger.info("解析短链接: %s", url)

        # 创建请求，设置 User-Agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        req = urllib.request.Request(url, headers=headers, method="HEAD")

        # 发送请求，允许自动重定向
        with urllib.request.urlopen(req, timeout=timeout) as response:
            real_url = response.url
            logger.info("解析成功: %s", real_url)
            return real_url

    except urllib.error.HTTPError as e:
        # HEAD 请求可能被拒绝，尝试 GET 请求
        if e.code in [403, 405, 412]:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    real_url = response.url
                    logger.info("解析成功: %s", real_url)
                    return real_url
            except Exception as e2:
                logger.warning("GET 请求也失败: %s (%s)", url, e2)
        else:
            logger.warning("HTTP 错误: %s (%s)", url, e)

    except Exception as e:
        logger.warning("解析短链接失败: %s (%s)", url, e)

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
    判断是否是短链接（按主机名精确匹配，含子域）

    Args:
        url: URL

    Returns:
        是否是短链接
    """
    return _host_matches(url, _SHORT_DOMAINS)


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

    # 检查是否是 Bilibili（按主机名精确匹配）并提取 BV 号
    if _host_matches(real_url, ("bilibili.com", "b23.tv")):
        bvid = extract_bvid(real_url)
        if bvid:
            return f"https://www.bilibili.com/video/{bvid}"

    return real_url
