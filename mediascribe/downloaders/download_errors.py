"""下载侧错误分类 — P1-5(2026-11-20 架构评审)。

此前所有下载失败都收敛成无类型 ``RuntimeError``/``HTTPError``:
404(视频已删)、403/429(风控墙)、网络抖动对调用方不可区分 ——
批量跑对"已删视频"每轮重试一次永久占位,对风控没有退避,报告也无法
给出每类失败的计数。

本模块提供统一分类:

* ``transient`` — 网络抖动 / 网关 5xx / 超时:值得重试;
* ``bot_wall`` — 403/412/429 与平台风控码(如微信 1023/1024):
  重试无益,应退避并放慢节奏;
* ``gone`` — 404/410:资源已删,重试永久无效,批处理跳过;
* ``fatal`` — 其余(参数错 / 域名拒绝 / 未知):失败即终态。

``classify_exception`` 是幂等入口:已是 ``DownloadError`` 原样返回,
``requests.HTTPError`` 按 HTTP 状态归类,超时/连接错误归 transient,
其余归 fatal。
"""

from __future__ import annotations

from typing import Any, Optional

import requests

CATEGORY_TRANSIENT = "transient"
CATEGORY_BOT_WALL = "bot_wall"
CATEGORY_GONE = "gone"
CATEGORY_FATAL = "fatal"

_CATEGORIES = (CATEGORY_TRANSIENT, CATEGORY_BOT_WALL, CATEGORY_GONE, CATEGORY_FATAL)

__all__ = [
    "DownloadError",
    "CATEGORY_TRANSIENT",
    "CATEGORY_BOT_WALL",
    "CATEGORY_GONE",
    "CATEGORY_FATAL",
    "classify_status",
    "classify_exception",
    "raise_for_status_classified",
]

#: 视为"风控墙"的 HTTP 状态(重试无益,应退避)
_BOT_WALL_STATUSES = (403, 412, 429)
#: 视为"已删"的 HTTP 状态(重试永久无效)
_GONE_STATUSES = (404, 410)
#: 视为"瞬时"的 HTTP 状态(传输层 Retry 已兜一轮,分类供上游决策)
_TRANSIENT_STATUSES = (408, 500, 502, 503, 504)


class DownloadError(Exception):
    """带分类的下载失败。

    Attributes
    ----------
    category
        ``transient`` / ``bot_wall`` / ``gone`` / ``fatal`` 之一。
    status
        触发分类的 HTTP 状态码(平台风控码场景为 ``None``,码值在
        message 里携带)。
    """

    code = "DOWNLOAD_FAILED"

    def __init__(self, category: str, message: str, *, status: Optional[int] = None):
        super().__init__(message)
        self.category = category if category in _CATEGORIES else CATEGORY_FATAL
        self.status = status


def classify_status(status: Optional[int]) -> str:
    """HTTP 状态码 → 失败类别。"""
    if status is None:
        return CATEGORY_FATAL
    if status in _GONE_STATUSES:
        return CATEGORY_GONE
    if status in _BOT_WALL_STATUSES:
        return CATEGORY_BOT_WALL
    if status in _TRANSIENT_STATUSES:
        return CATEGORY_TRANSIENT
    return CATEGORY_FATAL


def classify_exception(exc: BaseException) -> DownloadError:
    """把任意下载异常归类为 :class:`DownloadError`(幂等)。"""
    if isinstance(exc, DownloadError):
        return exc
    status: Optional[int] = None
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            status = int(response.status_code)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            status = None
    if status is not None:
        return DownloadError(classify_status(status), str(exc), status=status)
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return DownloadError(CATEGORY_TRANSIENT, str(exc))
    return DownloadError(CATEGORY_FATAL, str(exc))


def raise_for_status_classified(response: Any) -> None:
    """``response.raise_for_status()`` 的分类版 — HTTPError 升格为
    :class:`DownloadError`(带类别与状态码),其余异常原样上抛。"""
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        status: Optional[int] = None
        if getattr(exc, "response", None) is not None:
            try:
                status = int(exc.response.status_code)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                status = None
        raise DownloadError(classify_status(status), str(exc), status=status) from exc
