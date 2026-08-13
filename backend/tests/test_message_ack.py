"""
Ticket #11 — 后端回传用户消息真实 UUID（message_ack 事件）

Seam: agent.run 事件流（WS 与 HTTP 两条发送路径共同消费）。
断言外部可观察行为：事件顺序与载荷，不断言内部实现。
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base, get_session

# Model imports at module level so Base.metadata is complete before the
# engine fixture runs create_all.
from models.character import CharacterProfile
from models.conversation import Conversation
from models.message import Message
from models.user_config import UserConfig


# ── Harness ──────────────────────────────────────────────────────────────────


class FakeStreamLLM:
    """Yields a single token so the chat loop reaches 'done' without a real API."""

    def __init__(self, response_text: str = "你好！"):
        self._response = response_text

    async def stream_chat(self, messages, tools=None):
        yield {"type": "token", "content": self._response}


async def _fake_memory_search(session, query="", top_k=3, character_id=None):
    return []


async def _seed(session, char_id="char-1", conv_id="conv-1"):
    session.add_all([
        CharacterProfile(id=char_id, name="测试角色", personality="开朗",
                         role="companion", archetype="friend"),
        Conversation(id=conv_id, character_id=char_id),
        UserConfig(id=1),
    ])
    await session.commit()


def _make_agent():
    from core.agent import Agent
    from core.tool_registry import ToolRegistry
    from core.tool_runtime import ToolRuntime
    return Agent(
        llm_service=FakeStreamLLM(),
        tool_registry=ToolRuntime(registry=ToolRegistry(), enable_tracing=False, enable_sandbox=False),
    )


async def _collect_events(agent, session, **run_kwargs):
    events = []
    async for event in agent.run(session=session, **run_kwargs):
        events.append(event)
    return events


async def _get_user_message(session_factory, conversation_id) -> Message:
    """Fetch the single persisted user message of a conversation."""
    async with session_factory() as session:
        return (await session.execute(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.role == "user",
            )
        )).scalar_one()


@pytest.fixture(autouse=True)
def _patched_memory_search(monkeypatch):
    """All agent.run calls in these tests hit the fake memory search."""
    from services.memory_service import memory_service as memory_svc
    monkeypatch.setattr(memory_svc, "search", _fake_memory_search)


@pytest.fixture
async def engine():
    e = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ── Agent event stream tests ─────────────────────────────────────────────────


class TestMessageAckEvent:
    @pytest.mark.asyncio
    async def test_ack_before_first_token_with_real_id(self, session_factory):
        """ack 在首个 token 前出现，message_id 等于落库用户消息主键，client id 原样回传。"""
        async with session_factory() as session:
            await _seed(session)
            events = await _collect_events(
                _make_agent(), session,
                user_message="你好",
                conversation_id="conv-1",
                character_id="char-1",
                client_message_id="local-123",
            )

        acks = [e for e in events if e["type"] == "message_ack"]
        assert len(acks) == 1, f"Expected exactly one message_ack, got events: {events}"
        ack = acks[0]
        assert ack["client_message_id"] == "local-123"
        assert ack["message_id"] and ack["message_id"] != "local-123"

        ack_idx = next(i for i, e in enumerate(events) if e["type"] == "message_ack")
        token_idx = next(i for i, e in enumerate(events) if e["type"] == "token")
        assert ack_idx < token_idx, (
            f"message_ack (idx={ack_idx}) must precede first token (idx={token_idx})"
        )

        msg = await _get_user_message(session_factory, "conv-1")
        assert msg.id == ack["message_id"]

    @pytest.mark.asyncio
    async def test_no_ack_when_client_message_id_absent(self, session_factory):
        """未携带 client_message_id（未选入）时不产生 ack——行为与旧客户端不变。"""
        async with session_factory() as session:
            await _seed(session)
            events = await _collect_events(
                _make_agent(), session,
                user_message="你好",
                conversation_id="conv-1",
                character_id="char-1",
            )

        assert not any(e["type"] == "message_ack" for e in events), (
            f"must not emit message_ack without client_message_id, got: {events}"
        )

    @pytest.mark.asyncio
    async def test_no_ack_without_user_message(self, session_factory):
        """proactive 回合（无用户消息）不产生 ack。"""
        async with session_factory() as session:
            await _seed(session)
            events = await _collect_events(
                _make_agent(), session,
                user_message=None,
                proactive_hint="主动打个招呼",
                conversation_id="conv-1",
                character_id="char-1",
            )

        assert not any(e["type"] == "message_ack" for e in events), (
            f"proactive round must not emit message_ack, got: {events}"
        )

    @pytest.mark.asyncio
    async def test_no_ack_in_greeting_mode(self, session_factory):
        """daily greeting 回合不产生 ack。"""
        async with session_factory() as session:
            await _seed(session)
            events = await _collect_events(
                _make_agent(), session,
                user_message=None,
                conversation_id="conv-1",
                character_id="char-1",
                mode="greeting",
                extra_context={},
            )

        assert not any(e["type"] == "message_ack" for e in events), (
            f"greeting must not emit message_ack, got: {events}"
        )


# ── HTTP endpoint test ───────────────────────────────────────────────────────


class TestHttpSendUserMessageId:
    @pytest.fixture
    async def client(self, session_factory, monkeypatch):
        """AsyncClient wired to the real app with in-memory DB + fake LLM agent."""
        from main import app
        import api.chat as chat_mod

        async def override_get_session():
            async with session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        monkeypatch.setattr(chat_mod, "agent", _make_agent())

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_send_returns_user_message_id(self, client, session_factory):
        async with session_factory() as session:
            await _seed(session)

        resp = await client.post("/api/chat/send", json={
            "message": "你好",
            "character_id": "char-1",
            "client_message_id": "local-456",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["user_message_id"], "response must include user_message_id"
        assert data["user_message_id"] != "local-456"

        msg = await _get_user_message(session_factory, data["conversation_id"])
        assert msg.id == data["user_message_id"]

    @pytest.mark.asyncio
    async def test_send_without_client_message_id_still_works(self, client, session_factory):
        """不携带 client_message_id 时接口行为不变——响应不含 user_message_id。"""
        async with session_factory() as session:
            await _seed(session)

        resp = await client.post("/api/chat/send", json={
            "message": "你好",
            "character_id": "char-1",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["message_id"], "assistant message_id must still be returned"
        assert "user_message_id" not in data, (
            "response shape must be unchanged when client_message_id is absent"
        )
