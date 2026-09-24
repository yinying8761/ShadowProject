"""聊天接口必须拒绝不存在的 conversation_id（审查 §B2.3：孤儿消息的直接成因）。

agent.run 对"会话不存在"是宽容的（`conv` 为 None 也继续落库），所以拦截必须在 API 层：
- HTTP：404，且一条消息都不写。
- WS：连接时查一次、每一轮再查一次（会话可能在连接期间被删），
  不存在 → `error(conversation_not_found)` + 关闭码 4404。

WS 用最小 app + TestClient 驱动真实协议：不挂 main.app，避免触发 lifespan 里的
`init_db()`（那会迁移真实的 data/companion.db）。WS 路由从 `database.async_session`
取连接（不是 `Depends(get_session)`），所以直接替换该模块属性。
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.websockets import WebSocketDisconnect

from database import Base
from models.character import CharacterProfile
from models.conversation import Conversation
from models.message import Message


class TestHttpSendRefusesUnknownConversation:
    async def test_unknown_conversation_is_404_and_writes_nothing(self, client, session_factory):
        res = await client.post(
            "/api/chat/send",
            json={"message": "hello ghost", "conversation_id": "ghost-conv", "character_id": "ch1"},
        )
        assert res.status_code == 404
        assert res.json()["detail"] == "Conversation not found"

        async with session_factory() as s:
            assert (await s.execute(select(Message))).scalars().all() == []
            assert (await s.execute(select(Conversation))).scalars().all() == []

    async def test_a_valid_conversation_is_accepted(self, client, session_factory, monkeypatch):
        """回归：守卫只拒绝"不存在"，不能把正常会话一起挡掉（agent 用假流替身）。"""
        async with session_factory() as s:
            s.add(CharacterProfile(id="c1", name="小柔"))
            s.add(Conversation(id="conv-1", character_id="c1"))
            await s.commit()

        async def _fake_run(**_kwargs):
            yield {"type": "message_ack", "message_id": "m-user"}
            yield {"type": "token", "content": "在的"}
            yield {"type": "done", "message_id": "m-assistant"}

        monkeypatch.setattr("api.chat.agent.run", _fake_run)

        res = await client.post(
            "/api/chat/send",
            json={"message": "在吗", "conversation_id": "conv-1", "character_id": "c1"},
        )
        assert res.status_code == 200
        assert res.json() == {
            "conversation_id": "conv-1",
            "message_id": "m-assistant",
            "content": "在的",
        }


def _ws_client(monkeypatch, factory) -> TestClient:
    monkeypatch.setattr("database.async_session", factory)

    async def _noop(*_args, **_kwargs) -> None:
        """避免在测试里启动后台 watcher / 配置轮询。"""

    monkeypatch.setattr("services.proactive_session.ProactiveSession.start", _noop)
    monkeypatch.setattr("services.proactive_session.ProactiveSession.stop", _noop)

    from api.chat import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _run_sync(coro):
    """在测试线程的 loop 里跑一小段 DB 操作（app 跑在 TestClient 的 portal loop）。"""
    return asyncio.run(coro)


async def _message_ids(factory) -> list[str]:
    async with factory() as s:
        return sorted(r.id for r in (await s.execute(select(Message))).scalars().all())


class TestWebSocketRefusesUnknownConversation:
    def test_connect_is_refused_when_the_conversation_does_not_exist(self, ws_env, monkeypatch):
        _engine, factory = ws_env
        client = _ws_client(monkeypatch, factory)

        with client.websocket_connect("/ws/chat/ghost-conv") as ws:
            assert ws.receive_json() == {
                "type": "error",
                "code": "conversation_not_found",
                "message": "Conversation not found",
            }
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()

        assert _run_sync(_message_ids(factory)) == []

    def test_chat_turn_is_refused_after_the_conversation_is_deleted(self, ws_env, monkeypatch):
        """多窗口的现场：socket 还挂着，会话已被删 —— 这一轮不许落库。"""
        _engine, factory = ws_env

        async def _seed():
            async with factory() as s:
                s.add(CharacterProfile(id="c1", name="小柔"))
                s.add(Conversation(id="conv-1", character_id="c1"))
                await s.commit()

        async def _delete():
            async with factory() as s:
                await s.delete(await s.get(Conversation, "conv-1"))
                await s.commit()

        _run_sync(_seed())
        client = _ws_client(monkeypatch, factory)

        with client.websocket_connect("/ws/chat/conv-1") as ws:
            _run_sync(_delete())
            ws.send_json({"type": "chat", "content": "还在吗", "character_id": "c1"})
            assert ws.receive_json()["code"] == "conversation_not_found"
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()

        assert _run_sync(_message_ids(factory)) == []
