"""审批闸门（ticket #10 / ADR-0009）：同一时刻只放行**一个**工具审批。

Seam: `services.approval_gate.ApprovalGate`。注入 `send_json` 与超时参数，不碰网络、
不用真实时钟（只靠 asyncio 的 future/事件），所以"排队 / 放行 / 超时 / 未知 id"都能
确定地测出来。

为什么需要它：群里多个成员并发开口（ticket #09），每个成员都可能要审批
（`write_file` / `see_screen` / MCP 工具）—— 不排队的话用户会同时看到一排对话框。
超时语义与 1:1 原来一致：**120 秒没回答 = 拒绝**。
"""

import asyncio

import pytest

from services.approval_gate import ApprovalGate


class FakeSocket:
    """记下所有下发的消息（= 用户的界面）。"""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    def of_type(self, kind: str) -> list[dict]:
        return [p for p in self.sent if p["type"] == kind]


async def _until(predicate, *, ticks: int = 200) -> bool:
    """等到条件成立（让出事件循环，不用真实时间）。"""
    for _ in range(ticks):
        if predicate():
            return True
        await asyncio.sleep(0)
    return predicate()


class TestApprovalGate:
    @pytest.mark.asyncio
    async def test_only_one_approval_is_in_front_of_the_user_at_a_time(self):
        """两个成员同时要审批：第二个**必须等**第一个被回答（否则用户看到一排对话框）。"""
        socket = FakeSocket()
        gate = ApprovalGate(socket.send_json)

        first = asyncio.create_task(gate.request("write_file", {"path": "a"}))
        second = asyncio.create_task(gate.request("write_file", {"path": "b"}))

        assert await _until(lambda: len(socket.of_type("approval_request")) == 1)
        # 再给几拍：第一个还没回答，第二个绝不该冒出来
        for _ in range(3):
            await asyncio.sleep(0)
        assert len(socket.of_type("approval_request")) == 1

        gate.resolve(socket.of_type("approval_request")[0]["request_id"], True)
        assert await _until(lambda: len(socket.of_type("approval_request")) == 2)
        assert socket.of_type("approval_request")[0]["name"] == "write_file"

        assert await first is True
        # 第二个还在等回答（第一个放行不代表第二个自动放行）
        assert not second.done()

        gate.resolve(socket.of_type("approval_request")[1]["request_id"], False)
        assert await second is False

    @pytest.mark.asyncio
    async def test_a_timeout_is_a_denial_and_frees_the_queue(self):
        """超时语义不变：没回答 = 拒绝；而且**不留悬案、不卡死闸门**（后面的人还能进来）。"""
        socket = FakeSocket()
        gate = ApprovalGate(socket.send_json, timeout=0)

        first = asyncio.create_task(gate.request("write_file", {}))
        second = asyncio.create_task(gate.request("see_screen", {}))

        assert await first is False  # 超时 = 拒绝
        assert await second is False  # 第一个人超时后第二个才被放行（也超时）
        assert len(socket.of_type("approval_request")) == 2
        assert socket.of_type("approval_request")[1]["name"] == "see_screen"
        assert gate.waiting == 0  # 没有留悬案

    @pytest.mark.asyncio
    async def test_answering_an_unknown_request_reports_false(self):
        """陈旧/重复的回答不该炸，也不该误放行别人（WS 层据此打日志）。"""
        socket = FakeSocket()
        gate = ApprovalGate(socket.send_json)

        assert gate.resolve("approval-nope", True) is False

        waiting = asyncio.create_task(gate.request("write_file", {}))
        assert await _until(lambda: len(socket.of_type("approval_request")) == 1)

        assert gate.resolve("approval-nope", True) is False  # 还是错的 id
        assert not waiting.done()
        gate.resolve(socket.of_type("approval_request")[0]["request_id"], True)
        assert await waiting is True

    @pytest.mark.asyncio
    async def test_the_request_id_is_echoed_so_the_client_can_answer_it(self):
        socket = FakeSocket()
        gate = ApprovalGate(socket.send_json)

        waiting = asyncio.create_task(gate.request("write_file", {"path": "x"}))
        assert await _until(lambda: len(socket.of_type("approval_request")) == 1)

        request = socket.of_type("approval_request")[0]
        assert request["request_id"].startswith("approval-")
        assert request["arguments"] == {"path": "x"}
        assert gate.resolve(request["request_id"], True) is True
        assert await waiting is True
