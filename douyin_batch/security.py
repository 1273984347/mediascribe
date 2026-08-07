"""
Security utilities
安全工具
"""
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# 文件名清洗的规范实现统一收敛到 ``platform_compat.safe_filename``（被
# video2text 核心包直接引用，行为契约稳定）。``sanitize_filename`` 在其基础上
# 叠加安全专用规则（路径穿越 ``..``、空值回退 ``unnamed``、可调 max_length），
# 避免两份重复实现。
from .platform_compat import safe_filename

# Trusted domains
TRUSTED_DOMAINS = {
    "bilibili.com",
    "b23.tv",
    "douyin.com",
    "v.douyin.com",
    "douyinvod.com",
    "youtu.be",
    "youtube.com",
}

# Suspicious patterns
SUSPICIOUS_PATTERNS = [
    r"<script",
    r"javascript:",
    r"vbscript:",
    r"on\w+\s*=",
    r"data:text/html",
]


def is_safe_url(url: str) -> bool:
    """
    Check if a URL is safe to process.

    Args:
        url: URL to check

    Returns:
        True if safe
    """
    if not url or not isinstance(url, str):
        return False

    # Must be a valid URL
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    if not parsed.netloc:
        return False

    # Check for suspicious patterns
    url_lower = url.lower()
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, url_lower):
            return False

    return True


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """
    Sanitize a filename to prevent path traversal and other attacks.

    P1-14 收敛：基础清洗委托给 ``platform_compat.safe_filename``（跨平台规范实现），
    这里只叠加安全专用规则。保持历史行为以确保既有测试通过。

    Args:
        name: Filename to sanitize
        max_length: Maximum filename length

    Returns:
        Sanitized filename
    """
    if not name or not isinstance(name, str):
        return "unnamed"

    # 1) 长度上限 + 扩展名保留：在原始字符串上计算，避免 ``safe_filename`` 的
    #    200 截断提前吞掉扩展名（既有测试依赖此行为）。
    if max_length and len(name) > max_length:
        if "." in name:
            base, ext = name.rsplit(".", 1)
            base = base[: max(0, max_length - len(ext) - 1)]
            name = f"{base}.{ext}"
        else:
            name = name[:max_length]

    # 2) 跨平台基础清洗（去非法字符 / 控制字符 / Windows 保留名）。
    cleaned = safe_filename(name)

    # 3) 安全专用规则：中和父目录引用（路径穿越），并剥离首尾空白与小数点。
    cleaned = cleaned.replace("..", "_")
    cleaned = cleaned.strip().strip(".")

    if not cleaned:
        return "unnamed"

    return cleaned


def safe_join_path(base: Path, *parts: str) -> Optional[Path]:
    """
    Safely join paths to prevent path traversal.

    Args:
        base: Base directory
        *parts: Path components to join

    Returns:
        Joined path or None if traversal detected
    """
    base = base.resolve()
    target = base.joinpath(*parts).resolve()

    # Check that target is within base
    try:
        target.relative_to(base)
    except ValueError:
        return None

    return target


def validate_video_id(video_id: str) -> bool:
    """
    Validate that a video ID is well-formed.

    Args:
        video_id: Video ID (e.g., BV1xxx or numeric)

    Returns:
        True if valid
    """
    if not video_id or not isinstance(video_id, str):
        return False

    # BV 号
    if video_id.startswith("BV"):
        return bool(re.match(r"^BV[A-Za-z0-9]{10}$", video_id))

    # Numeric ID
    return bool(re.match(r"^\d{10,25}$", video_id))


def limit_string_length(s: str, max_length: int = 1000) -> str:
    """
    Limit string length to prevent memory exhaustion.

    Args:
        s: String to limit
        max_length: Maximum length

    Returns:
        Truncated string
    """
    if not isinstance(s, str):
        return ""
    if len(s) > max_length:
        return s[:max_length]
    return s


def check_url_safety(url: str, allowed_domains: Optional[set] = None) -> tuple:
    """
    Comprehensive URL safety check.

    Args:
        url: URL to check
        allowed_domains: Set of allowed domains (default: TRUSTED_DOMAINS)

    Returns:
        (is_safe: bool, reason: str)
    """
    allowed = allowed_domains or TRUSTED_DOMAINS

    if not is_safe_url(url):
        return False, "URL failed basic safety check"

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Remove port and www.
        domain = domain.split(":")[0]
        if domain.startswith("www."):
            domain = domain[4:]

        # Check if domain is trusted
        is_trusted = any(
            domain == allowed_d or domain.endswith(f".{allowed_d}")
            for allowed_d in allowed
        )

        if not is_trusted:
            return False, f"Untrusted domain: {domain}"

        return True, "OK"

    except Exception as e:
        return False, f"Parse error: {e}"
