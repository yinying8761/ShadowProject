"""Agent 的"群聊里某一轮"（ticket #53）。

Seam: `Agent.run` —— 群聊驱动注入 `persist_reply=False`：回复文本交回调用方，
由编排器先判定"跳过"（`<silent>`）再决定落不落库；同时注入本轮的场景说明与
记忆检索用的用户消息（`user_message=None` 时历史里的用户消息仍是本轮的锚点）。
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.agent import Agent
from core.tool_registry import ToolRegistry
from core.tool_runtime import ToolRuntime
from database import Base
from models.character import CharacterProfile
from models.conversation import Conversation
from models.message import Message


class FakeLLM:
    """脚本化 LLM：记下收到的 messages，按脚本逐字吐 token（并断言不并行）。"""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.seen: list[list[dict]] = []
        self._streaming = False

    async def estimate_prompt_tokens(self, messages):
        return 1

    async def stream_chat(self, messages, tools=None, on_retry=None):
        assert not self._streaming, "同一时刻只能有一个 LLM 流（群轮串行）"
        self._streaming = True
        try:
            self.seen.append(messages)
            reply = self.replies.pop(0) if self.replies else ""
            for ch in reply:
                yield {"type": "token", "content": ch}
        finally:
            self._streaming = False


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory):
    async with factory() as s:
        s.add(CharacterProfile(id="c1", name="小柔", personality="温柔", role="companion", archetype="friend"))
        s.add(Conversation(id="conv-g", character_id=None, group_id=None))
        s.add(Message(id="u1", conversation_id="conv-g", role="user", content="你们好"))
        await s.commit()


def _agent(replies: list[str]) -> Agent:
    return Agent(
        llm_service=FakeLLM(replies),
        tool_registry=ToolRuntime(ToolRegistry(), enable_tracing=False, enable_sandbox=False),
    )


async def _events(agent, session):
    return [
        event
        async for event in agent.run(
            session,
            user_message=None,
            conversation_id="conv-g",
            character_id="c1",
            persist_reply=False,
            system_suffix="## 当前场景：群聊\n无话可说就只输出 <silent>。",
            memory_query="你们好",
        )
    ]


class TestAgentGroupTurn:
    async def test_the_reply_is_handed_back_instead_of_persisted(self, factory):
        agent = _agent(["在的"])
        await _seed(factory)

        async with factory() as s:
            events = await _events(agent, s)

        assert events[-1]["type"] == "done"
        assert events[-1]["content"] == "在的"
        assert "message_id" not in events[-1]
        # 用户消息也没有被再落一遍（群聊由驱动负责落库）
        async with factory() as s:
            rows = (await s.execute(select(Message))).scalars().all()
            assert [m.id for m in rows] == ["u1"]

    async def test_the_group_scenario_reaches_the_system_prompt(self, factory):
        agent = _agent(["在的"])
        await _seed(factory)

        async with factory() as s:
            await _events(agent, s)

        system = agent.llm_service.seen[0][0]
        assert system["role"] == "system"
        assert "## 当前场景：群聊" in system["content"]
        assert "<silent>" in system["content"]

    async def test_memory_retrieval_uses_this_turns_user_message(self, factory, monkeypatch):
        agent = _agent(["在的"])
        await _seed(factory)
        searches: list[tuple[str, str | None]] = []

        async def fake_search(session, query, top_k=3, character_id=None):
            searches.append((query, character_id))
            return []

        monkeypatch.setattr("core.agent.memory_service.search", fake_search)

        async with factory() as s:
            await _events(agent, s)

        assert searches == [("你们好", "c1")]
