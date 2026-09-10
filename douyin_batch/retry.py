"""
重试机制和下载模块
"""

import shutil
import time
from pathlib import Path
from typing import Callable, Optional


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
    """带重试的下载。

    P1-14 收敛：内部委托核心 ``mediascribe.downloaders.douyin.DouyinDownloader``
    的媒体下载路径（流式下载 + 带 ``HTTPAdapter`` 重试的 session + 失败清理），
    消除与核心包重复的实现。

    保持原函数签名不变，以便继续作为 ``process_single_video_safe`` 注入的
    ``download_media_fn``。核心下载器会自生成临时 uuid 文件名，这里下载完成后
    移动到调用方指定的 ``output_path``（核心不支持自定义输出文件名）。
    原有的外部重试（``max_retries`` / 退避 / 重试打印）语义保留。
    """
    output_path = Path(output_path)

    def _do_download():
        # 延迟导入核心下载器，避免模块导入期强依赖 mediascribe。
        from mediascribe.downloaders.douyin import DouyinDownloader

        downloader = DouyinDownloader()
        suffix = output_path.suffix or ".mp4"
        tmp_path = downloader._download_media(media_url, output_path.parent, suffix=suffix)
        if not tmp_path or not tmp_path.exists():
            raise IOError("核心下载器未产出文件")

        # 移动到调用方指定的目标路径（核心生成的是临时 uuid 文件名）。
        if tmp_path.resolve() != output_path.resolve():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            shutil.move(str(tmp_path), str(output_path))
        return True

    def _on_retry(attempt, exc):
        print(f"      ⚠️ 第 {attempt} 次重试... ({type(exc).__name__})")

    try:
        return retry(
            _do_download,
            max_retries=max_retries,
            delay=1.0,
            backoff=2.0,
            exceptions=(Exception,),
            on_retry=_on_retry,
        )
    except Exception as e:
        print(f"      ❌ 下载最终失败: {e}")
        return False
