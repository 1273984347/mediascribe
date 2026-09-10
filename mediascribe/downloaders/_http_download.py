"""
公共 HTTP 流式下载实现（downloaders 内部共享，下划线前缀表示非公开模块）。

P1-6 收敛说明：douyin / wechat_mp / xiaohongshu 三个下载器原本各自维护一份
下载逻辑，行为不一致（重试策略 / 临时文件 / 失败清理）。这里统一实现：

- ``build_http_session()``：带 ``HTTPAdapter`` 自动重试的 ``requests.Session``
- ``stream_download()``：流式下载，统一以下语义
  - ``.part`` 临时文件名（带 uuid 后缀，多线程/同秒不碰撞）
  - ``try/finally`` 失败清理（不留半截文件，也不覆盖旧的好文件）
  - 进度日志按 5% 分桶降频 + 可选 ``progress_cb(downloaded, total)`` 回调
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Callable, Optional

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

# 重试配置
_HTTP_MAX_RETRIES = 3
_HTTP_BACKOFF_FACTOR = 1.0  # 1s, 2s, 4s


def build_http_session() -> requests.Session:
    """构造带重试的 ``requests.Session``。

    重试条件：
    - 连接错误（ConnectTimeout / ConnectionError）
    - 读取超时（ReadTimeout）
    - 5xx 响应
    不重试：4xx（除 429 由 urllib3 自动处理）
    """
    session = requests.Session()
    try:
        from urllib3.util.retry import Retry

        retry = Retry(
            total=_HTTP_MAX_RETRIES,
            connect=_HTTP_MAX_RETRIES,
            read=_HTTP_MAX_RETRIES,
            backoff_factor=_HTTP_BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retry)
    except ImportError:  # pragma: no cover - urllib3 总是随 requests 安装
        adapter = HTTPAdapter()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def stream_download(
    url: str,
    dest_path: Path,
    *,
    session: Optional[requests.Session] = None,
    expected_size: Optional[int] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    headers: Optional[dict] = None,
    timeout=60,
) -> Path:
    """流式下载 ``url`` 到 ``dest_path``。

    语义：
    - 先写 ``<dest>.<uuid8>.part`` 临时文件，全部成功后才落到 ``dest_path``
      （下载中途失败不会破坏已有文件）
    - 失败时 ``finally`` 清理 ``.part`` 文件并抛出异常，由调用方决定降级策略
    - ``total`` 取响应 ``content-length``，缺失时回退 ``expected_size``
    - 进度按 5% 分桶写日志，避免高带宽下逐 chunk 刷屏；若提供
      ``progress_cb``，每收到一个 chunk 都回调 ``progress_cb(downloaded, total)``
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # uuid 短后缀防止同秒/多线程临时文件名碰撞
    tmp_path = dest_path.with_name(f"{dest_path.name}.{uuid.uuid4().hex[:8]}.part")

    own_session = session is None
    sess = session if session is not None else build_http_session()
    try:
        response = sess.get(url, headers=headers, stream=True, timeout=timeout)
        response.raise_for_status()

        total_size = expected_size or int(response.headers.get("content-length", 0))
        downloaded = 0

        # 进度日志按 5% 分桶降频，避免高带宽下逐 chunk 刷屏。
        last_logged_bucket = -1
        with open(tmp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb is not None:
                    try:
                        progress_cb(downloaded, total_size)
                    except Exception:  # 回调异常不应中断下载
                        logger.debug("progress_cb 异常（忽略）", exc_info=True)
                if total_size > 0:
                    percent = (downloaded / total_size) * 100
                    bucket = int(percent // 5)
                    if bucket != last_logged_bucket:
                        last_logged_bucket = bucket
                        logger.info(
                            "下载进度: %.1f%% (%d/%d 字节)",
                            percent,
                            downloaded,
                            total_size,
                        )
        logger.info("下载完成: %s (%d 字节)", dest_path.name, downloaded)

        # 覆盖写防护：下载全程只碰 .part，最后一步才替换目标文件
        tmp_path.replace(dest_path)
        return dest_path

    finally:
        # 失败/异常路径清理 .part；成功路径 replace 后文件已不存在
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        if own_session:
            sess.close()
