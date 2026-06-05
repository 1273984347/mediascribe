"""
重试机制和下载模块
"""
import time
from pathlib import Path
from typing import Callable, Optional

import requests


def retry(
    func: Callable,
    *args,
    max_retries: int = 3,
    delay: float = 2.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    on_retry: Optional[Callable] = None,
    **kwargs,
):
    """
    通用重试装饰器

    Args:
        func: 要执行的函数
        max_retries: 最大重试次数
        delay: 初始延迟（秒）
        backoff: 退避倍数
        exceptions: 捕获的异常类型
        on_retry: 重试时的回调函数
    """
    current_delay = delay
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except exceptions as e:
            last_exception = e
            if attempt >= max_retries:
                break

            if on_retry:
                on_retry(attempt + 1, e)

            time.sleep(current_delay)
            current_delay *= backoff

    raise last_exception


def download_media_with_retry(
    media_url: str,
    output_path: Path,
    max_retries: int = 3,
) -> bool:
    """带重试的下载"""

    def _do_download():
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.douyin.com/",
        }
        response = requests.get(media_url, headers=headers, stream=True, timeout=30)
        response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return True

    def _on_retry(attempt, exc):
        print(f"      ⚠️ 第 {attempt} 次重试... ({type(exc).__name__})")

    try:
        return retry(
            _do_download,
            max_retries=max_retries,
            delay=1.0,
            backoff=2.0,
            exceptions=(requests.RequestException, IOError),
            on_retry=_on_retry,
        )
    except Exception as e:
        print(f"      ❌ 下载最终失败: {e}")
        return False
