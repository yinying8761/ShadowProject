"""
Tests for conversation title generation (Workflow H, ticket #35).

Covers:
- Seam 1: ConversationManager.ensure_title with fake LLM + in-memory SQLite
- Seam 2: PUT /api/conversations/{id} with in-memory SQLite + TestClient
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select

from database import Base, get_session
from models.conversation import Conversation
from models.message import Message
from models.character import CharacterProfile


# ── Helpers ────────────────────────────────────────────────────────────────

class FakeTitleLLM:
    """Returns a canned title for testing."""

    def __init__(self, title: str = "测试标题"):
        self._title = title
        self.call_count = 0

    async def chat_sync(self, messages, max_tokens=50, temperature=0.3):
        self.call_count += 1
        return self._title


class FakeFailingLLM:
    """Always raises a retryable exception."""

    def __init__(self, exc: Exception = TimeoutError("llm timeout")):
        self._exc = exc
        self.call_count = 0

    async def chat_sync(self, messages, max_tokens=50, temperature=0.3):
        self.call_count += 1
        raise self._exc


class FakeEmptyLLM:
    """Returns empty string."""

    async def chat_sync(self, messages, max_tokens=50, temperature=0.3):
        return ""


async def _seed_conv_with_msgs(factory, conv_id: str, title: str = "New Conversation"):
    """Seed a conversation with user + assistant messages."""
    async with factory() as s:
        s.add(CharacterProfile(id="char-t1", name="Test", personality="", role="companion", archetype="friend"))
        s.add(Conversation(id=conv_id, character_id="char-t1", title=title))
        s.add(Message(conversation_id=conv_id, role="user", content="你好，今天天气怎么样？"))
        s.add(Message(conversation_id=conv_id, role="assistant", content="今天天气很好，适合出去走走。"))
        await s.commit()


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


@pytest.fixture
async def client(engine, session_factory):
    from main import app

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ── Seam 1: ConversationManager.ensure_title ───────────────────────────────

class TestEnsureTitle:
    @pytest.mark.asyncio
    async def test_generates_title_when_default(self, session_factory):
        """When title == default, LLM generates a new title."""
        from core.conversation_manager import ConversationManager

        await _seed_conv_with_msgs(session_factory, "conv-default")
        mgr = ConversationManager()
        llm = FakeTitleLLM("天气对话")

        async with session_factory() as s:
            result = await mgr.ensure_title(s, "conv-default", llm)
            assert result == "天气对话"
            assert llm.call_count == 1

            # Verify DB updated
            conv = await s.get(Conversation, "conv-default")
            assert conv.title == "天气对话"

    @pytest.mark.asyncio
    async def test_skips_when_already_named(self, session_factory):
        """When title != default, skip generation."""
        from core.conversation_manager import ConversationManager

        await _seed_conv_with_msgs(session_factory, "conv-named", title="已有标题")
        mgr = ConversationManager()
        llm = FakeTitleLLM("新标题")

        async with session_factory() as s:
            result = await mgr.ensure_title(s, "conv-named", llm)
            assert result == "已有标题"
            assert llm.call_count == 0

            conv = await s.get(Conversation, "conv-named")
            assert conv.title == "已有标题"

    @pytest.mark.asyncio
    async def test_skips_after_user_rename(self, session_factory):
        """User renamed title != default → skip forever."""
        from core.conversation_manager import ConversationManager

        await _seed_conv_with_msgs(session_factory, "conv-renamed", title="用户改名")
        mgr = ConversationManager()
        llm = FakeTitleLLM("AI标题")

        async with session_factory() as s:
            result = await mgr.ensure_title(s, "conv-renamed", llm)
            assert result == "用户改名"
            assert llm.call_count == 0

    @pytest.mark.asyncio
    async def test_llm_failure_keeps_default(self, session_factory):
        """LLM fails after retries → keep default silently."""
        from core.conversation_manager import ConversationManager

        await _seed_conv_with_msgs(session_factory, "conv-fail")
        mgr = ConversationManager()
        llm = FakeFailingLLM()

        async with session_factory() as s:
            result = await mgr.ensure_title(s, "conv-fail", llm)
            assert result is None
            assert llm.call_count == 3  # 1 initial + 2 retries

            from models.conversation import Conversation
            conv = await s.get(Conversation, "conv-fail")
            assert conv.title == mgr.DEFAULT_TITLE

    @pytest.mark.asyncio
    async def test_empty_result_keeps_default(self, session_factory):
        """LLM returns empty/whitespace → keep default."""
        from core.conversation_manager import ConversationManager

        await _seed_conv_with_msgs(session_factory, "conv-empty")
        mgr = ConversationManager()
        llm = FakeEmptyLLM()

        async with session_factory() as s:
            result = await mgr.ensure_title(s, "conv-empty", llm)
            assert result is None

            from models.conversation import Conversation
            conv = await s.get(Conversation, "conv-empty")
            assert conv.title == mgr.DEFAULT_TITLE

    @pytest.mark.asyncio
    async def test_no_messages_keeps_default(self, session_factory):
        """No messages → no title generated."""
        from core.conversation_manager import ConversationManager
        from models.conversation import Conversation
        from models.character import CharacterProfile

        async with session_factory() as s:
            s.add(CharacterProfile(id="char-empty", name="Test", personality="", role="companion", archetype="friend"))
            s.add(Conversation(id="conv-empty-msg", character_id="char-empty"))
            await s.commit()

        mgr = ConversationManager()
        llm = FakeTitleLLM("标题")

        async with session_factory() as s:
            result = await mgr.ensure_title(s, "conv-empty-msg", llm)
            assert result is None
            assert llm.call_count == 0


# ── Seam 2: PUT /api/conversations/{id} ───────────────────────────────────

class TestPutEndpoint:
    @pytest.mark.asyncio
    async def test_put_round_trip(self, client, session_factory):
        """PUT updates title and returns updated conversation."""
        from models.conversation import Conversation
        from models.character import CharacterProfile

        async with session_factory() as s:
            s.add(CharacterProfile(id="char-put", name="Test", personality="", role="companion", archetype="friend"))
            s.add(Conversation(id="conv-put", character_id="char-put", title="Old Title"))
            await s.commit()

        r = await client.put("/api/conversations/conv-put", json={"title": "新标题"})
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "新标题"
        assert data["id"] == "conv-put"

    @pytest.mark.asyncio
    async def test_put_rejects_empty(self, client, session_factory):
        """Empty or whitespace title returns 400."""
        from models.conversation import Conversation
        from models.character import CharacterProfile

        async with session_factory() as s:
            s.add(CharacterProfile(id="char-put2", name="Test", personality="", role="companion", archetype="friend"))
            s.add(Conversation(id="conv-put2", character_id="char-put2", title="Original"))
            await s.commit()

        r = await client.put("/api/conversations/conv-put2", json={"title": ""})
        assert r.status_code == 400

        r2 = await client.put("/api/conversations/conv-put2", json={"title": "   "})
        assert r2.status_code == 400

    @pytest.mark.asyncio
    async def test_put_not_found(self, client):
        """Unknown conversation returns 404."""
        r = await client.put("/api/conversations/nonexistent", json={"title": "标题"})
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_put_blocks_ai_overwrite(self, client, session_factory):
        """After PUT rename, ensure_title skips (integration)."""
        from core.conversation_manager import ConversationManager

        from models.conversation import Conversation
        from models.character import CharacterProfile
        from models.message import Message

        async with session_factory() as s:
            s.add(CharacterProfile(id="char-block", name="Test", personality="", role="companion", archetype="friend"))
            s.add(Conversation(id="conv-block", character_id="char-block", title="New Conversation"))
            s.add(Message(conversation_id="conv-block", role="user", content="你好"))
            s.add(Message(conversation_id="conv-block", role="assistant", content="你好呀"))
            await s.commit()

        # User renames via PUT
        r = await client.put("/api/conversations/conv-block", json={"title": "用户标题"})
        assert r.status_code == 200

        # ensure_title should skip because title != default
        mgr = ConversationManager()
        llm = FakeTitleLLM("AI标题")
        async with session_factory() as s:
            result = await mgr.ensure_title(s, "conv-block", llm)
            assert result == "用户标题"
            assert llm.call_count == 0
