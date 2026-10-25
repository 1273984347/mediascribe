"""WebSocket 进度事件桥 —— 阻塞 events 队列 → asyncio 事件循环。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any

_WS_TERMINAL_EVENTS = frozenset({"succeeded", "failed", "cancelled"})


def _aio_queue_put_drop_oldest(q: "asyncio.Queue", item: Any) -> None:
    """投入 asyncio.Queue; 满时丢最旧(终态事件后发, 天然不丢)。"""
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:  # pragma: no cover - defensive
            pass
    q.put_nowait(item)


class _JobEventBridge:
    """把 ``JobProgress.events``(阻塞 queue)桥接到 asyncio 事件循环。

    P2-9: 旧实现每个 WS 连接每 0.5s 向线程池提交一次
    ``asyncio.to_thread(job.events.get, ...)`` — N 个连接就是 N 个
    0.5s 周期的线程池任务。这里改为每个 job 一个常驻 daemon drain
    线程, 经 ``loop.call_soon_threadsafe`` 把事件扇出到每个订阅者的
    ``asyncio.Queue``; 终态事件分发完毕后线程自动退出。
    """

    def __init__(self, job: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._job = job
        self._loop = loop
        self._closed = threading.Event()
        self._subscribers: "set[asyncio.Queue]" = set()
        self._subscribers_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._drain,
            daemon=True,
            name="v2t-ws-bridge",
        )
        self._thread.start()

    @property
    def subscriber_count(self) -> int:
        with self._subscribers_lock:
            return len(self._subscribers)

    def subscribe(self) -> "asyncio.Queue":
        q: "asyncio.Queue" = asyncio.Queue(maxsize=1000)
        with self._subscribers_lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: "asyncio.Queue") -> None:
        with self._subscribers_lock:
            self._subscribers.discard(q)

    def close(self) -> None:
        self._closed.set()

    def _drain(self) -> None:
        job = self._job
        while not self._closed.is_set():
            try:
                event = job.events.get(timeout=1.0)
            except queue.Empty:
                if job.finished:
                    break
                continue
            with self._subscribers_lock:
                subs = list(self._subscribers)
            for q in subs:
                try:
                    self._loop.call_soon_threadsafe(
                        _aio_queue_put_drop_oldest,
                        q,
                        event,
                    )
                except RuntimeError:
                    # 事件循环已关闭(应用停机)— 退出线程。
                    self._closed.set()
                    break
            if event.get("event") in _WS_TERMINAL_EVENTS:
                break
