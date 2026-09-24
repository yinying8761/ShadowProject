"""
Tests for compact: ConversationManager.summarize_and_trim + compact API endpoint.

Covers Seam 1 (keep_count=12, fake LLM) and Seam 2 (POST /{id}/compact).
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base, get_session


class FakeSummarizationLLM:
    """Returns a canned summary for testing."""

    def __init__(self, summary: str = "测试摘要：用户聊了 Rust。"):
        self._summary = summary
        self.call_count = 0

    async def chat_sync(self, messages, max_tokens=512, temperature=0.3):
        self.call_count += 1
        return self._summary


# ── 群对话的摘要输入（ticket #55） ───────────────────────────────────────


class TestGroupSummaryInput:
    """群对话的摘要输入走共享时间戳渲染，保留"谁说了什么"。"""

    @pytest.mark.asyncio
    async def test_summary_input_names_each_speaker(self):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from core.conversation_manager import ConversationManager
        from models.character import CharacterProfile
        from models.conversation import Conversation
        from models.message import Message

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        captured: dict[str, str] = {}

        class CaptureLLM:
            async def chat_sync(self, messages, max_tokens=512, temperature=0.3):
                captured["prompt"] = messages[-1]["content"]
                return "摘要内容"

        async with factory() as s:
            from models.group import Group

            s.add(CharacterProfile(id="c1", name="小柔"))
            s.add(CharacterProfile(id="c2", name="阿B"))
            s.add(Group(id="g1", name="深夜食堂"))
            s.add(Conversation(id="conv-g", character_id=None, group_id="g1"))
            for i in range(6):
                s.add(Message(
                    conversation_id="conv-g", role="user", content=f"用户第{i}句",
                ))
                s.add(Message(
                    conversation_id="conv-g", role="assistant", content=f"第{i}句回应",
                    speaker_id="c1" if i % 2 == 0 else "c2",
                ))
            await s.commit()

            result = await ConversationManager().summarize_and_trim(
                s, "conv-g", keep_count=2, llm_service=CaptureLLM()
            )

        assert result["deleted"] == 10
        prompt = captured["prompt"]
        # 谁说了什么：角色自己的名字，而不是笼统的「角色」
        assert "小柔" in prompt and "阿B" in prompt
        assert "[角色]:" not in prompt
        # 时间戳也在（共享渲染器）
        assert "[用户]:" in prompt
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_a_group_with_only_user_messages_still_uses_the_timestamped_format(self):
        """判断依据是会话种类，不是"解析出了几个名字"。

        窗口里只有用户消息时解析结果为空 —— 但这是群对话，摘要输入仍要走共享渲染器，
        否则会悄悄退回 1:1 的 `[用户]/[角色]` 格式、丢掉时间戳。
        """
        import re
        from datetime import datetime

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from core.conversation_manager import ConversationManager
        from models.conversation import Conversation
        from models.group import Group
        from models.message import Message

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        captured: dict[str, str] = {}

        class CaptureLLM:
            async def chat_sync(self, messages, max_tokens=512, temperature=0.3):
                captured["prompt"] = messages[-1]["content"]
                return "摘要内容"

        async with factory() as s:
            s.add(Group(id="g1", name="深夜食堂"))
            s.add(Conversation(id="conv-g", character_id=None, group_id="g1"))
            for i in range(6):
                s.add(Message(
                    conversation_id="conv-g", role="user", content=f"用户第{i}句",
                    created_at=datetime(2026, 9, 20, 6, 25, i),
                ))
            await s.commit()

            await ConversationManager().summarize_and_trim(
                s, "conv-g", keep_count=2, llm_service=CaptureLLM()
            )

        assert re.search(r"\d{4}/\d{1,2}/\d{1,2} \d{2}:\d{2} \[用户\]:", captured["prompt"])
        await engine.dispose()


# ── Seam 1: ConversationManager.summarize_and_trim ─────────────────────


class TestSummarizeAndTrim:
    @pytest.mark.asyncio
    async def test_no_trim_when_under_keep_count(self):
        """When total messages <= keep_count, nothing is deleted."""
        from core.conversation_manager import ConversationManager
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            # Seed conversation + 12 messages
            from models.conversation import Conversation
            from models.message import Message
            conv = Conversation(id="conv-1", character_id="char-1")
            s.add(conv)
            for i in range(12):
                s.add(Message(conversation_id="conv-1", role="user", content=f"msg {i}"))
            await s.commit()

            mgr = ConversationManager()
            llm = FakeSummarizationLLM()
            result = await mgr.summarize_and_trim(s, "conv-1", keep_count=12, llm_service=llm)

            assert result["deleted"] == 0
            assert result["summary"] == ""
            assert llm.call_count == 0  # LLM not called

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_trim_deletes_old_messages(self):
        """When total > keep_count, oldest messages are deleted."""
        from core.conversation_manager import ConversationManager
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            from models.conversation import Conversation
            from models.message import Message
            from sqlalchemy import select, func

            conv = Conversation(id="conv-2", character_id="char-1")
            s.add(conv)
            for i in range(30):
                s.add(Message(conversation_id="conv-2", role="user", content=f"msg {i}"))
            await s.commit()

            mgr = ConversationManager()
            llm = FakeSummarizationLLM("摘要内容")
            result = await mgr.summarize_and_trim(s, "conv-2", keep_count=12, llm_service=llm)

            assert result["deleted"] == 18  # 30 - 12
            assert result["summary"] == "摘要内容"
            assert llm.call_count == 1

            # Verify 12 messages remain
            count = await s.execute(
                select(func.count(Message.id)).where(Message.conversation_id == "conv-2")
            )
            assert count.scalar() == 12

        await engine.dispose()


# ── Seam 2: POST /{id}/compact API endpoint ────────────────────────────


@pytest.fixture
async def compact_engine():
    e = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
async def compact_client(compact_engine):
    factory = async_sessionmaker(compact_engine, class_=AsyncSession, expire_on_commit=False)
    from main import app

    async def override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _seed_conv_with_msgs(factory, conv_id: str, count: int):
    """Seed a conversation with count messages."""
    from models.conversation import Conversation
    from models.character import CharacterProfile
    from models.message import Message
    from models.user_config import UserConfig

    async with factory() as s:
        s.add(CharacterProfile(id="char-comp", name="Test", personality="", role="companion", archetype="friend"))
        s.add(Conversation(id=conv_id, character_id="char-comp"))
        s.add(UserConfig(id=1))
        for i in range(count):
            s.add(Message(conversation_id=conv_id, role="user", content=f"msg {i}"))
        await s.commit()


class TestCompactEndpoint:
    @pytest.mark.asyncio
    async def test_compact_returns_deleted_count(self, compact_client, compact_engine):
        """POST /{id}/compact returns deleted count when messages exceed keep_count."""
        factory = async_sessionmaker(compact_engine, class_=AsyncSession, expire_on_commit=False)
        await _seed_conv_with_msgs(factory, "conv-c1", 30)

        r = await compact_client.post("/api/conversations/conv-c1/compact?keep_count=12")
        assert r.status_code == 200
        data = r.json()
        assert data["conversation_id"] == "conv-c1"
        assert data["deleted"] == 18
        # Summary may be empty if LLM API is unavailable in test env

    @pytest.mark.asyncio
    async def test_compact_under_limit_returns_zero(self, compact_client, compact_engine):
        """POST /{id}/compact returns deleted=0 when messages <= keep_count."""
        factory = async_sessionmaker(compact_engine, class_=AsyncSession, expire_on_commit=False)
        await _seed_conv_with_msgs(factory, "conv-c2", 5)

        r = await compact_client.post("/api/conversations/conv-c2/compact?keep_count=12")
        assert r.status_code == 200
        data = r.json()
        assert data["deleted"] == 0
        assert data["summary"] == ""

    @pytest.mark.asyncio
    async def test_compact_not_found(self, compact_client):
        """POST /{id}/compact returns 404 for unknown conversation."""
        r = await compact_client.post("/api/conversations/nonexistent/compact")
        assert r.status_code == 404
