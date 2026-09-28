"""群聊跑通（ticket #53）：WS 进一句话 → 成员按序回应 / 跳过不落库 / 预算封顶 / 串行。

Seam: `/ws/chat/{conversation_id}` 的**群对话**分支 + 历史接口（真实协议 + 内存库 + 假 LLM）。
编排语义本身在 `tests/test_group_turn.py`（纯逻辑）；这里验接线：`speaker_id` 落库、
跳过的不落库不推送、两条消息不并行、群对话不问候不主动。

落库断言走**历史接口**（公开面）而不是直接查库：看到的正是用户/LLM 会看到的东西。

WS 用最小 app + TestClient：不挂 main.app，避免 lifespan 里的 `init_db()` 动真实库。
WS 路由从 `database.async_session` 取连接（不是 `Depends(get_session)`），所以两者都替换。
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.websockets import WebSocketDisconnect

from core.tool_registry import ToolRegistry
from core.tool_runtime import ToolRuntime
from database import Base, get_session
from models.character import CharacterProfile
from models.conversation import Conversation
from models.group import Group, GroupMember
from models.message import Message


class FakeGroupLLM:
    """按角色名字挑脚本的假 LLM（角色名就在 system prompt 里）。

    每次流式吐完一句；脚本用完 → 空回复（= 什么都没说）。
    同时记录 system prompt 与并发峰值 —— "永不并行"必须能被测到。
    """

    def __init__(self, script: dict[str, list[str]]):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[list[dict]] = []  # 每次调用的完整 messages（system + 历史）
        self.concurrent = 0
        self.max_concurrent = 0

    @property
    def system_prompts(self) -> list[str]:
        return [call[0]["content"] for call in self.calls]

    async def estimate_prompt_tokens(self, messages):
        return 1

    async def chat_sync(self, messages, max_tokens=50, temperature=0.3):
        return "群聊标题"

    async def stream_chat(self, messages, tools=None, on_retry=None):
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            system = messages[0]["content"]
            self.calls.append(messages)
            name = next((n for n in self.script if n in system), None)
            replies = self.script.get(name, []) if name else []
            for ch in replies.pop(0) if replies else "":
                yield {"type": "token", "content": ch}
        finally:
            self.concurrent -= 1


async def _seed_group(factory):
    """小柔（顺序 0）、阿B（顺序 1）+ 一条群对话。"""
    async with factory() as s:
        s.add(CharacterProfile(id="c1", name="小柔", personality="温柔", role="companion", archetype="friend"))
        s.add(CharacterProfile(id="c2", name="阿B", personality="爱吐槽", role="companion", archetype="friend"))
        s.add(Group(id="g1", name="深夜食堂"))
        s.add(GroupMember(id="gm1", group_id="g1", character_id="c1", position=0))
        s.add(GroupMember(id="gm2", group_id="g1", character_id="c2", position=1))
        s.add(Conversation(id="conv-g", character_id=None, group_id="g1"))
        await s.commit()


async def _delete_conversation(factory) -> None:
    async with factory() as s:
        await s.delete(await s.get(Conversation, "conv-g"))
        await s.commit()


async def _all_messages(factory) -> list[tuple[str, str, str | None]]:
    async with factory() as s:
        rows = (await s.execute(select(Message))).scalars().all()
        return [(m.role, m.content, m.speaker_id) for m in rows]


def _client(monkeypatch, factory, llm, *, proactive=None) -> TestClient:
    monkeypatch.setattr("database.async_session", factory)

    async def _noop(*_args, **_kwargs):
        """避免在测试里启动后台 watcher / 配置轮询。"""

    monkeypatch.setattr("services.proactive_session.ProactiveSession.start", proactive or _noop)
    monkeypatch.setattr("services.proactive_session.ProactiveSession.stop", _noop)

    from core.agent import Agent

    monkeypatch.setattr(
        "api.chat.agent",
        Agent(
            llm_service=llm,
            tool_registry=ToolRuntime(ToolRegistry(), enable_tracing=False, enable_sandbox=False),
        ),
    )

    async def fake_search(session, query, top_k=3, character_id=None):
        return []

    # 后台补账单独测：这里禁掉，免得群对话测试去构造真的 LLMService
    monkeypatch.setattr("services.group_chat.schedule_catch_up", lambda *_a, **_k: None)

    monkeypatch.setattr("core.agent.memory_service.search", fake_search)

    from api.chat import router as chat_router
    from api.conversation import router as conversation_router

    app = FastAPI()
    app.include_router(chat_router)
    app.include_router(conversation_router)

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def _drain(ws, *, expected_dones=1, max_events=40) -> list[dict]:
    """读到本轮结束（done）为止的所有事件 —— 有上限，等不到就快速失败。"""
    events: list[dict] = []
    while sum(1 for e in events if e.get("type") == "done") < expected_dones:
        if len(events) >= max_events:
            raise AssertionError(f"等不到 done，只收到：{[e.get('type') for e in events]}")
        events.append(ws.receive_json())
    return events


class TestGroupTurnOverTheWebSocket:
    def test_members_reply_in_order_and_a_skip_leaves_no_trace(self, ws_env, monkeypatch):
        _engine, factory = ws_env
        asyncio.run(_seed_group(factory))
        llm = FakeGroupLLM({"小柔": ["在的呀"]})  # 阿B 没脚本 → 跳过
        client = _client(monkeypatch, factory, llm)

        with client.websocket_connect("/ws/chat/conv-g") as ws:
            ws.send_json({"type": "chat", "content": "你们好", "client_message_id": "local-1"})
            events = _drain(ws)

        assert "message_ack" in [e["type"] for e in events]
        group_messages = [e for e in events if e["type"] == "group_message"]
        # 说话人名字由后端给（与历史接口同一规则），前端不必再解析一遍
        assert [(e["character_id"], e["speaker"], e["content"]) for e in group_messages] == [
            ("c1", "小柔", "在的呀")
        ]
        assert events[-1] == {"type": "done", "group": True}

        # 历史接口（公开面）：用户的 + 小柔那一句（带 speaker_id/说话人名）；跳过的人没有痕迹
        rows = client.get("/api/conversations/conv-g/messages").json()
        assert [(r["role"], r["content"], r["speaker_id"]) for r in rows] == [
            ("user", "你们好", None),
            ("assistant", "在的呀", "c1"),
        ]
        assert rows[1]["speaker"] == "小柔"
        assert rows[1]["transcript"].endswith("[小柔]: 在的呀")

        # 场景说明 + 群成员进了 system prompt；历史里带时间戳与说话人
        first_system = llm.system_prompts[0]
        assert "群聊" in first_system and "深夜食堂" in first_system
        assert "小柔" in first_system and "阿B" in first_system
        # 历史（不是 system prompt）里带时间戳与说话人 —— 角色能看出"谁说了什么"
        history = [m for m in llm.calls[0] if m["role"] != "system"]
        assert any("[用户]: 你们好" in (m.get("content") or "") for m in history)

    def test_every_member_request_ends_with_a_user_turn(self, ws_env, monkeypatch):
        """带 `tools` 的请求若以 assistant 收尾，DeepSeek 思考模式直接 400
        （`reasoning_content` in the thinking mode must be passed back）。

        群轮里第二个请求的历史正好以"上一个角色刚说的话"结尾 —— 2026-09-27 的真实
        故障就是它（一个角色回完、1 秒后 400）。所以每一轮都要用合成 user 轮收尾。
        """
        _engine, factory = ws_env
        asyncio.run(_seed_group(factory))
        llm = FakeGroupLLM({"小柔": ["A1"], "阿B": ["B1"]})
        client = _client(monkeypatch, factory, llm)

        with client.websocket_connect("/ws/chat/conv-g") as ws:
            ws.send_json({"type": "chat", "content": "你们好"})
            _drain(ws)

        assert llm.calls, "假 LLM 一次都没被调用"
        for index, messages in enumerate(llm.calls):
            assert messages[-1]["role"] == "user", (
                f"第 {index} 次请求以 {messages[-1]['role']} 收尾 —— 会被思考模式 400"
            )

    def test_the_turn_end_event_reports_an_empty_queue(self, ws_env, monkeypatch):
        """按轮发信号（审查 P3）：一轮跑完先发 `turn_end`，队列空了 pending=False，`done` 兜底。"""
        _engine, factory = ws_env
        asyncio.run(_seed_group(factory))
        client = _client(monkeypatch, factory, FakeGroupLLM({"小柔": ["在的呀"]}))

        with client.websocket_connect("/ws/chat/conv-g") as ws:
            ws.send_json({"type": "chat", "content": "你们好"})
            events = _drain(ws)

        assert [e for e in events if e["type"] == "turn_end"] == [
            {"type": "turn_end", "group": True, "pending": False}
        ]
        assert events[-1] == {"type": "done", "group": True}  # 批次结束仍是 done

    def test_the_chain_budget_caps_a_chatty_group(self, ws_env, monkeypatch):
        _engine, factory = ws_env
        asyncio.run(_seed_group(factory))
        llm = FakeGroupLLM({"小柔": [f"第{i}句" for i in range(1, 10)], "阿B": ["我也说"]})
        client = _client(monkeypatch, factory, llm)

        with client.websocket_connect("/ws/chat/conv-g") as ws:
            ws.send_json({"type": "chat", "content": "聊聊呗"})
            events = _drain(ws)

        spoken = [e for e in events if e["type"] == "group_message"]
        assert len(spoken) == 6  # 链预算
        assert {e["character_id"] for e in spoken} == {"c1"}  # 到顶 → 后面的成员不被打扰

        rows = client.get("/api/conversations/conv-g/messages").json()
        assert [r["content"] for r in rows if r["role"] == "assistant"] == [
            f"第{i}句" for i in range(1, 7)
        ]

    def test_a_burst_of_messages_never_runs_two_turns_at_once(self, ws_env, monkeypatch):
        _engine, factory = ws_env
        asyncio.run(_seed_group(factory))
        llm = FakeGroupLLM({"小柔": ["收到一", "收到二", "收到三"]})
        client = _client(monkeypatch, factory, llm)

        with client.websocket_connect("/ws/chat/conv-g") as ws:
            ws.send_json({"type": "chat", "content": "第一句"})
            ws.send_json({"type": "chat", "content": "第二句"})
            _drain(ws)

        assert llm.max_concurrent == 1, "群轮必须串行：一次只有一个 LLM 流"
        rows = client.get("/api/conversations/conv-g/messages").json()
        assert [r["content"] for r in rows if r["role"] == "user"] == ["第一句", "第二句"]

    def test_opening_a_group_conversation_schedules_the_catch_up(self, ws_env, monkeypatch):
        """打开群对话 → 排上后台补账（补账本身在 tests/services/test_group_memory.py）。"""
        _engine, factory = ws_env
        asyncio.run(_seed_group(factory))
        scheduled: list[str] = []
        client = _client(monkeypatch, factory, FakeGroupLLM({}))
        monkeypatch.setattr("services.group_chat.schedule_catch_up", scheduled.append)

        with client.websocket_connect("/ws/chat/conv-g") as ws:
            ws.send_json({"type": "daily_greeting"})
            ws.receive_json()

        assert scheduled == ["conv-g"]

    def test_a_group_turn_needs_an_existing_conversation(self, ws_env, monkeypatch):
        """群聊也守"会话必须存在"：会话在连接期间被删掉 → 拒绝，且一条都不落库。"""
        _engine, factory = ws_env
        asyncio.run(_seed_group(factory))
        client = _client(monkeypatch, factory, FakeGroupLLM({}))

        with client.websocket_connect("/ws/chat/conv-g") as ws:
            asyncio.run(_delete_conversation(factory))
            ws.send_json({"type": "chat", "content": "还在吗"})
            assert ws.receive_json()["code"] == "conversation_not_found"
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()

        assert asyncio.run(_all_messages(factory)) == []

    def test_the_http_fallback_refuses_group_conversations(self, ws_env, monkeypatch):
        """HTTP 兜底是 1:1 的路：群对话必须拒绝（否则会写出 speaker_id=NULL 的回复）。"""
        _engine, factory = ws_env
        asyncio.run(_seed_group(factory))
        client = _client(monkeypatch, factory, FakeGroupLLM({}))

        res = client.post("/api/chat/send", json={
            "message": "你们好", "conversation_id": "conv-g", "character_id": "c1",
        })

        assert res.status_code == 400
        assert asyncio.run(_all_messages(factory)) == []

    def test_a_group_conversation_never_greets_or_goes_proactive(self, ws_env, monkeypatch):
        _engine, factory = ws_env
        asyncio.run(_seed_group(factory))
        started = {"count": 0}

        async def _record_start(*_args, **_kwargs):
            started["count"] += 1

        client = _client(monkeypatch, factory, FakeGroupLLM({}), proactive=_record_start)

        with client.websocket_connect("/ws/chat/conv-g") as ws:
            ws.send_json({"type": "daily_greeting"})
            event = ws.receive_json()

        assert started["count"] == 0, "群对话不启动主动陪伴"
        assert event == {"type": "daily_greeting_skip", "reason": "group_conversation"}
