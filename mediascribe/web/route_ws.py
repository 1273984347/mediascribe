"""WebSocket 进度路由 —— ``/ws/progress/{job_id}``。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

try:
    from fastapi import WebSocket
except ImportError:  # pragma: no cover - optional
    # 只在类型注解与函数体里用(fastapi 缺失时不会执行到)。
    WebSocket = None  # type: ignore[assignment,misc]

from mediascribe.web.route_jobs import _job_registry
from mediascribe.web.security import _env_float, _verify_api_token
from mediascribe.web.ws_bridge import _JobEventBridge

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查用
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _register_ws_progress_route(app: "FastAPI") -> None:
    """Route registrar — create_app 装配时调用(2026-10-03 审查拆分, 路由逐字搬移)。"""

    @app.websocket("/ws/progress/{job_id}")
    async def ws_progress(websocket: WebSocket, job_id: str) -> None:
        """Stream per-stage progress events for a job.

        The job is created by :class:`mediascribe.progress.ProgressRegistry`
        and its events queue is drained here.  The connection stays
        open until the job finishes (event ``succeeded`` / ``failed``
        / ``cancelled``) and then closes with code ``1000``.

        P1-1 — 握手鉴权: 服务端设置了 ``MEDIASCRIBE_API_TOKEN`` 时,
        WS 必须携带 token(与 HTTP 侧同源、同为常数时间比较):
          * query param ``?token=<tok>``, 或
          * ``Sec-WebSocket-Protocol`` 首段(浏览器无法给 WS 加自定义头)。
        未配置 token 时放行(与 HTTP 行为一致)。校验失败直接
        ``close(1008)``, 不 accept。``cancel`` 指令同样只对已通过鉴权
        的连接生效。

        P2-9 — 事件桥接: 每个 job 一个常驻 drain 线程
        (:class:`_JobEventBridge`)把阻塞 events 队列桥接到 asyncio,
        多个 WS 连接订阅同一 bridge, 不再每连接每 0.5s 提交线程池任务。
        连接另有最大时长(``MEDIASCRIBE_WS_MAX_SESSION_SECONDS``, 默认
        3600s)与空闲超时(``MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS``,
        默认 300s), 超时以 ``1000`` 正常关闭。

        v3.2.0c Tier 1 — clients may send ``{"event": "cancel"}`` to
        request cancellation; the server then calls
        ``registry.cancel(job_id)`` (and, if registered, the matching
        ``AsyncPipeline.cancel()``) and pushes a ``cancelled`` event
        back to all listeners.
        """
        # P1-1: accept 之前完成鉴权 — 失败则握手拒绝, 不进入事件循环。
        # token 有两个通道: Sec-WebSocket-Protocol 子协议(首选, 不落
        # uvicorn/反代访问日志)与 ?token= query(兼容旧客户端 / 子协议
        # 字符集不安全的降级通道)。两通道候选**任一**匹配即放行 —
        # 混合客户端(无关子协议 + query token)不会因通道优先级被误拒
        # (2026-10-03 审查加固 + review 修复)。
        client_host = websocket.client.host if websocket.client else None
        protocol_header = websocket.headers.get("sec-websocket-protocol", "")
        header_tokens = [p.strip() for p in protocol_header.split(",") if p.strip()]
        query_token = websocket.query_params.get("token")
        candidates = header_tokens + ([query_token] if query_token else [])
        if candidates:
            ws_authenticated = any(_verify_api_token(t, client_host) for t in candidates)
        else:
            # 两通道都缺省: 无 token 模式看来源信任, 有 token 模式拒绝。
            ws_authenticated = _verify_api_token(None, client_host)
        if not ws_authenticated:
            await websocket.close(code=1008)
            return
        # 浏览器规范: 客户端请求了子协议时, 服务端必须从其列表中回显一个,
        # 否则握手直接失败; query token 不是子协议, 绝不回显。
        await websocket.accept(subprotocol=header_tokens[0] if header_tokens else None)
        try:
            registry = _job_registry(app)
        except ImportError:
            await websocket.send_json({"event": "error", "message": "progress module missing"})
            await websocket.close(code=1011)
            return
        job = registry.get(job_id)
        if job is None:
            await websocket.send_json({"event": "error", "message": "unknown job"})
            await websocket.close(code=4404)
            return
        # Replay current state, then drain new events
        await websocket.send_json({"event": "snapshot", **job.to_dict()})

        import json as _json

        loop = asyncio.get_running_loop()
        with app.state.ws_bridge_lock:
            bridge = app.state.ws_bridges.get(job.job_id)
            if bridge is None or bridge.subscriber_count == 0 or not bridge._thread.is_alive():
                bridge = _JobEventBridge(job, loop)
                app.state.ws_bridges[job.job_id] = bridge
        subscriber_queue = bridge.subscribe()
        max_session_seconds = _env_float("MEDIASCRIBE_WS_MAX_SESSION_SECONDS", 3600.0)
        if max_session_seconds <= 0:
            max_session_seconds = float("inf")  # 显式禁用会话上限
        idle_timeout = _env_float("MEDIASCRIBE_WS_IDLE_TIMEOUT_SECONDS", 300.0)
        # ping 周期取空闲超时与 30s 的较小者 — 让客户端能探测连接活性。
        ping_interval = min(30.0, idle_timeout) if idle_timeout > 0 else 30.0

        # v3.2.0c Tier 1 — clients may send ``{"event": "cancel"}`` to
        # request cancellation.  We use :func:`asyncio.wait` with
        # ``FIRST_COMPLETED`` so that both the event-drain task and the
        # client-listen task are polled within the *same* event-loop
        # iteration.  This makes cancel delivery reliable under
        # Starlette's TestClient (which drives the server forward one
        # client call at a time) — a separate ``create_task`` listener
        # would only be scheduled when the main loop yields, which can
        # race with the client's send/receive sequence.
        event_task = asyncio.create_task(subscriber_queue.get())
        client_task = asyncio.create_task(websocket.receive_text())
        session_deadline = loop.time() + max_session_seconds
        last_activity = loop.time()

        async def _handle_client_message(raw: str) -> bool:
            """处理一条客户端消息。返回 False 表示应终止连接。

            2026-10-31 重构: cancel 处理原本在两处重复(主循环 + ping 等待
            窗口), 抽到这里 —— ping 窗口里收到的 pong 与 cancel 走同一条
            路径, 不会漏。
            """
            try:
                parsed = _json.loads(raw)
            except Exception:
                parsed = None
            if not (isinstance(parsed, dict) and parsed.get("event") == "cancel"):
                return True
            if ws_authenticated:
                registry.cancel(job_id)
                # Also cancel any registered AsyncPipeline so
                # in-flight ``asyncio.Task``s are torn down.
                ap = app.state.async_pipelines.get(job_id)
                if ap is not None and hasattr(ap, "cancel"):
                    try:
                        ap.cancel()
                    except Exception:
                        pass
                return True
            # P1-1: 未鉴权连接不允许驱动 cancel。
            try:
                await websocket.send_json(
                    {
                        "event": "error",
                        "message": "cancel requires an authenticated connection",
                    }
                )
            except Exception:
                return False
            return True

        try:
            while not job.finished:
                now = loop.time()
                if now >= session_deadline:
                    break  # P2-9: 最大会话时长 — 防止连接永久挂起
                wait_timeout = min(
                    ping_interval,
                    max(0.05, session_deadline - now),
                )
                if idle_timeout > 0:
                    wait_timeout = min(
                        wait_timeout,
                        max(0.05, last_activity + idle_timeout - now),
                    )
                done, _ = await asyncio.wait(
                    {event_task, client_task},
                    timeout=wait_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if event_task in done:
                    event = event_task.result()
                    last_activity = loop.time()
                    try:
                        await websocket.send_json(event)
                    except Exception:
                        return
                    event_task = asyncio.create_task(subscriber_queue.get())
                if client_task in done:
                    try:
                        msg = client_task.result()
                    except Exception:
                        # WebSocket closed by client.
                        return
                    last_activity = loop.time()
                    if not await _handle_client_message(msg):
                        return
                    client_task = asyncio.create_task(websocket.receive_text())
                if not done:
                    # 超时 — 发 ping 让客户端探测连接活性; 继续等待。
                    #
                    # 2026-10-31 交互审查 P0-2 + 独立盲审 P2-5:
                    # 转写阶段是一次**阻塞调用**(progress.py:242-247 的
                    # pipeline.transcribe 包住全部耗时), 期间**不发任何事件**
                    # ⇒ 任何超过 idle_timeout(默认 300s)的真实转写都会被
                    # 服务端主动掐断, 前端只看到「连接断开」。
                    #
                    # 客户端现在会回 pong(见前端 jobs.js 的心跳), 而
                    # ``client_task`` 分支会刷新 last_activity —— 所以
                    # **这里不能**再刷新: 「发出 ping」不代表对端还活着
                    # (浏览器断网但 TCP 未断时, ping 会写进缓冲区而不报错)。
                    # 只有**收到 pong** 才证明连接真的可用。
                    try:
                        # ping 里带上 job 快照 —— 客户端重连后靠它补回进度
                        # (断线期间发生的阶段推进不会重放, 事件队列只保留增量)。
                        await websocket.send_json({"event": "ping", **job.to_dict()})
                    except Exception:
                        return
                    # 空闲判定**必须放在 ping 之后**, 且要给客户端一次往返
                    # 的机会 —— pong 要经过一次网络往返才到。
                    #
                    # 2026-10-31 回归实证: 判定放在 ping 之前 ⇒ pong 永远
                    # 来不及 ⇒ 长任务 100% 被掐断(正是本次要修的 P0-2);
                    # 放在 ping 之后但立即判定 ⇒ 同样是"pong 还没到" ⇒ 同样
                    # 断连。必须**再等一个 pong 周期**才判。
                    if idle_timeout > 0:
                        pong_deadline = loop.time() + min(ping_interval, idle_timeout)
                        while loop.time() < pong_deadline:
                            done, _ = await asyncio.wait(
                                {event_task, client_task},
                                timeout=max(0.05, pong_deadline - loop.time()),
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if event_task in done:
                                event = event_task.result()
                                last_activity = loop.time()
                                try:
                                    await websocket.send_json(event)
                                except Exception:
                                    return
                                event_task = asyncio.create_task(subscriber_queue.get())
                            if client_task in done:
                                try:
                                    raw = client_task.result()
                                except Exception:
                                    return
                                last_activity = loop.time()
                                if not await _handle_client_message(raw):
                                    return
                                client_task = asyncio.create_task(websocket.receive_text())
                            if done:
                                break
                        if (loop.time() - last_activity) >= idle_timeout:
                            # 发了 ping、又等满一个 pong 周期, 仍无任何回应
                            # ⇒ 连接真的死了。
                            break
            # Final snapshot then close
            try:
                await websocket.send_json({"event": "snapshot", **job.to_dict()})
            except Exception:
                pass
        finally:
            # Cancel background tasks without awaiting them — awaiting
            # a cancelled ``websocket.receive_text()`` task can interfere
            # with Starlette's WS teardown and surface as a
            # ``CancelledError`` in the TestClient's ``__exit__``.
            event_task.cancel()
            client_task.cancel()
            # P2-9: 退订; 最后一个订阅者离开时关闭并摘除 bridge。
            bridge.unsubscribe(subscriber_queue)
            if bridge.subscriber_count == 0:
                bridge.close()
                with app.state.ws_bridge_lock:
                    if app.state.ws_bridges.get(job.job_id) is bridge:
                        app.state.ws_bridges.pop(job.job_id, None)
        await websocket.close(code=1000)
