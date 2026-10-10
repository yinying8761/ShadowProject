"""审批闸门（ticket #10 / ADR-0009）：同一时刻只放行**一个**工具审批。

群里多个成员并发开口（ticket #09），每个成员都可能要审批（`write_file` /
`see_screen` / MCP 工具）—— 不排队的话用户会同时看到一排对话框。这里把"发送审批
请求 → 等用户回答"整段放进一把锁里，于是**下一个审批只在上一个被回答之后**才出现。

- 超时语义与 1:1 原来一致：**默认 120 秒没回答 = 拒绝**。
- 依赖注入 `send_json`（哪条连接、哪个界面由调用方决定），不碰网络、不用真实时钟，
  所以 `tests/test_approval_gate.py` 能确定地测出排队/放行/超时。
- **作用域 = 一条 WS 连接**：群聊的并发发生在同一条连接里（一个界面上绝不会同时出现
  两个审批框）；不同窗口各自有各自的闸门，互不阻塞。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Awaitable, Callable

SendJson = Callable[[dict], Awaitable[None]]

#: 用户没回答就按拒绝处理（与 1:1 既有语义一致）。
APPROVAL_TIMEOUT_SEC = 120.0


class ApprovalGate:
    """把并发的审批请求排成一队，一次只让一个出现在用户面前。"""

    def __init__(self, send_json: SendJson, *, timeout: float = APPROVAL_TIMEOUT_SEC):
        self._send_json = send_json
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[bool]] = {}

    @property
    def waiting(self) -> int:
        """当前在等用户回答的审批数（0 = 界面上没有审批框）。"""
        return len(self._pending)

    async def request(self, tool_name: str, arguments: dict) -> bool:
        """请求用户审批：排队 → 下发 → 等回答（超时/连接断了都算拒绝）。"""
        async with self._lock:
            request_id = f"approval-{uuid.uuid4().hex[:12]}"
            future: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
            self._pending[request_id] = future

            print(f"[APPROVAL] requesting tool={tool_name} id={request_id}", flush=True)
            try:
                await self._send_json({
                    "type": "approval_request",
                    "request_id": request_id,
                    "name": tool_name,
                    "arguments": arguments,
                })
                approved = await asyncio.wait_for(future, timeout=self._timeout)
                print(
                    f"[APPROVAL] resolved id={request_id} approved={approved}", flush=True
                )
            except asyncio.TimeoutError:
                print(f"[APPROVAL] TIMEOUT id={request_id}", flush=True)
                approved = False
            except asyncio.CancelledError:
                # 连接断了/这一轮被取消了：先把悬案撤掉再往上抛，别留一个没有回答的框
                self._pending.pop(request_id, None)
                raise
            finally:
                self._pending.pop(request_id, None)

            return bool(approved)

    def resolve(self, request_id: str, approved: bool) -> bool:
        """用户点了同意/拒绝。返回是否确实有这样一个待答审批（陈旧回答返回 False）。"""
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True
