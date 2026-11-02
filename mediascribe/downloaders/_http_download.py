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

from ..i18n import t

logger = logging.getLogger(__name__)

# 重试配置
_HTTP_MAX_RETRIES = 3
_HTTP_BACKOFF_FACTOR = 1.0  # 1s, 2s, 4s

# 各平台下载器共用的桌面 Chrome UA — 单一来源(2026-10-23 起, 原先在
# youtube/ytdlp/douyin/xiaohongshu/wechat_mp 共 10 处内联重复)。
# 站点封 UA 需要整体升级时只改这里。url_utils.py 的短链解析仍用历史
# Chrome/91 串, 未并入 — 保持其外发请求字节不变。
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


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


# 携带凭据的请求最大跟随跳数 — 超过视为重定向环
_CRED_MAX_HOPS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def cred_get(
    url: str,
    *,
    cookies: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 20,
    stream: bool = False,
    redirect_guard: Optional[Callable[[str], bool]] = None,
) -> requests.Response:
    """携带凭据的 GET — 手动接管重定向，逐跳做域名断言。

    安全背景 (2026-11-02 审计 SEC-001)：requests 默认跨域 302 会把
    ``Cookie`` 头原样重发到跳转目标 (cookiejar 域为空串匹配任意主机)。
    携带平台 cookies 的请求一旦被文章内嵌 URL 的开放重定向/热链跳转
    引出域，凭据即外泄 — 首跳的域名门控拦不住后续跳。

    语义：
    - 第 1 跳带 ``cookies`` (调用方须已对首跳 URL 做过域名断言)；
    - 后续跳**不带 cookies** (凭据只服务首跳平台)，且
      ``redirect_guard(next_url)`` 不通过即抛错中止；
    - ``redirect_guard`` 为 ``None`` 时仍不携带跨跳 cookies，但只限跳数
      防环 (对纯公用资源无影响)。
    """
    from urllib.parse import urljoin

    resp = requests.get(
        url,
        headers=headers,
        cookies=cookies,
        timeout=timeout,
        stream=stream,
        allow_redirects=False,
    )
    hops = 0
    while resp.status_code in _REDIRECT_STATUSES:
        hops += 1
        if hops > _CRED_MAX_HOPS:
            resp.close()
            raise RuntimeError(t("HTTP_REDIRECT_LOOP", url=url))
        loc = resp.headers.get("Location", "")
        resp.close()
        if not loc:
            raise RuntimeError(t("HTTP_REDIRECT_NO_LOCATION", url=url))
        next_url = str(urljoin(url, loc))
        if redirect_guard is not None and not redirect_guard(next_url):
            raise RuntimeError(t("HTTP_REDIRECT_OFF_DOMAIN", url=next_url))
        url = next_url
        resp = requests.get(
            url, headers=headers, timeout=timeout, stream=stream, allow_redirects=False
        )
    return resp


def stream_download(
    url: str,
    dest_path: Path,
    *,
    session: Optional[requests.Session] = None,
    expected_size: Optional[int] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    headers: Optional[dict] = None,
    timeout=60,
    max_bytes: Optional[int] = None,
    cookies: Optional[dict] = None,
    redirect_guard: Optional[Callable[[str], bool]] = None,
) -> Path:
    """流式下载 ``url`` 到 ``dest_path``。

    语义：
    - 先写 ``<dest>.<uuid8>.part`` 临时文件，全部成功后才落到 ``dest_path``
      （下载中途失败不会破坏已有文件）
    - 失败时 ``finally`` 清理 ``.part`` 文件并抛出异常，由调用方决定降级策略
    - ``total`` 取响应 ``content-length``，缺失时回退 ``expected_size``
    - 进度按 5% 分桶写日志，避免高带宽下逐 chunk 刷屏；若提供
      ``progress_cb``，每收到一个 chunk 都回调 ``progress_cb(downloaded, total)``
    - ``max_bytes``：响应大小上限，超限中止并清理（防恶意/损坏源灌满磁盘，
      2026-10-29 审查 P2）。``None`` 时读环境变量 ``MEDIASCRIBE_MAX_DOWNLOAD_MB``
      （默认 4096 MiB）。
    - ``cookies`` + ``redirect_guard`` (2026-11-02 SEC-001)：携带凭据的
      下载走 :func:`cred_get` 同款逐跳守卫 — 首跳带 cookies，后续跳
      脱域即中止且不带凭据。两者都缺省时保持旧的默认重定向行为。
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if max_bytes is None:
        import os

        try:
            max_bytes = int(os.environ.get("MEDIASCRIBE_MAX_DOWNLOAD_MB", "4096")) * 1024 * 1024
        except ValueError:
            max_bytes = 4096 * 1024 * 1024
    if max_bytes <= 0:
        max_bytes = None  # 显式关闭上限（不推荐）

    # uuid 短后缀防止同秒/多线程临时文件名碰撞
    tmp_path = dest_path.with_name(f"{dest_path.name}.{uuid.uuid4().hex[:8]}.part")

    own_session = session is None
    sess = session if session is not None else build_http_session()
    try:
        if cookies is not None or redirect_guard is not None:
            # 凭据/守卫模式: 禁用自动重定向, 逐跳处理 (SEC-001)
            response = sess.get(
                url,
                headers=headers,
                cookies=cookies,
                stream=True,
                timeout=timeout,
                allow_redirects=False,
            )
            hops = 0
            while response.status_code in _REDIRECT_STATUSES:
                hops += 1
                if hops > _CRED_MAX_HOPS:
                    response.close()
                    raise RuntimeError(t("HTTP_REDIRECT_LOOP", url=url))
                loc = response.headers.get("Location", "")
                response.close()
                if not loc:
                    raise RuntimeError(t("HTTP_REDIRECT_NO_LOCATION", url=url))
                from urllib.parse import urljoin

                next_url = str(urljoin(url, loc))
                if redirect_guard is not None and not redirect_guard(next_url):
                    raise RuntimeError(t("HTTP_REDIRECT_OFF_DOMAIN", url=next_url))
                url = next_url
                response = sess.get(
                    url,
                    headers=headers,
                    stream=True,
                    timeout=timeout,
                    allow_redirects=False,
                )
        else:
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
                if max_bytes is not None and downloaded > max_bytes:
                    # 慢速滴漏/无限流防线：超限即中止, finally 清 .part
                    limit_mb = (max_bytes + 1024 * 1024 - 1) // (1024 * 1024)
                    raise RuntimeError(
                        t("DL_SIZE_LIMIT_EXCEEDED", limit_mb=limit_mb, url=url[:120])
                    )
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
