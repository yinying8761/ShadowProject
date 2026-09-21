"""Consumer ② of the transcript renderer: history API (ticket #50, spec S2).

Seam: GET /api/conversations/{id}/messages — each message carries
`transcript` (shared-renderer line, null for tool plumbing) and
`speaker` (resolved name). Also covers the additive `messages.speaker_id`
migration (nullable, data preserved).
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base, get_session
from models.character import CharacterProfile
from models.conversation import Conversation
from models.message import Message
from models.user_profile import UserProfile


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


async def _seed(factory, *, profile_user_name="小明"):
    """Character + conversation + one exchange (+ tool plumbing)."""
    from datetime import datetime

    async with factory() as s:
        s.add(CharacterProfile(id="char-h", name="小柔", personality="", role="companion", archetype="friend"))
        s.add(Conversation(id="conv-h", character_id="char-h"))
        if profile_user_name is not None:
            s.add(UserProfile(character_id="char-h", user_name=profile_user_name))
        base = datetime(2026, 9, 20, 6, 25, 0)
        s.add(Message(id="hm1", conversation_id="conv-h", role="user", content="现在几点了", created_at=base))
        s.add(Message(
            id="hm2", conversation_id="conv-h", role="assistant", content="",
            created_at=datetime(2026, 9, 20, 6, 25, 1),
            tool_calls=[{"id": "c1", "name": "get_current_time", "arguments": {}}],
        ))
        s.add(Message(
            id="hm3", conversation_id="conv-h", role="tool",
            content='{"local_time": "2026-09-20 14:25:00"}',
            created_at=datetime(2026, 9, 20, 6, 25, 2), tool_call_id="c1",
        ))
        s.add(Message(id="hm4", conversation_id="conv-h", role="assistant", content="现在是下午两点半哦",
                      created_at=datetime(2026, 9, 20, 6, 25, 3)))
        await s.commit()


class TestHistoryTranscript:
    @pytest.mark.asyncio
    async def test_messages_carry_transcript_and_speaker(self, client, session_factory):
        """Text messages: transcript = renderer line; tool plumbing: null."""
        await _seed(session_factory)

        r = await client.get("/api/conversations/conv-h/messages")
        assert r.status_code == 200
        msgs = r.json()

        by_id = {m["id"]: m for m in msgs}
        assert by_id["hm1"]["transcript"] == "2026/9/20 14:25 [小明]: 现在几点了"
        assert by_id["hm1"]["speaker"] == "小明"
        assert by_id["hm4"]["transcript"] == "2026/9/20 14:25 [小柔]: 现在是下午两点半哦"
        assert by_id["hm4"]["speaker"] == "小柔"
        # Tool plumbing: no prefix, no speaker
        assert by_id["hm2"]["transcript"] is None and by_id["hm2"]["speaker"] is None
        assert by_id["hm3"]["transcript"] is None and by_id["hm3"]["speaker"] is None

    @pytest.mark.asyncio
    async def test_user_name_falls_back_to_global_profile(self, client, session_factory):
        """No character-specific profile → global (character_id NULL) profile wins."""
        await _seed(session_factory, profile_user_name=None)
        async with session_factory() as s:
            s.add(UserProfile(character_id=None, user_name="全球名"))
            await s.commit()

        r = await client.get("/api/conversations/conv-h/messages")
        msgs = r.json()
        by_id = {m["id"]: m for m in msgs}
        assert by_id["hm1"]["speaker"] == "全球名"
        assert by_id["hm1"]["transcript"] == "2026/9/20 14:25 [全球名]: 现在几点了"

    @pytest.mark.asyncio
    async def test_user_name_defaults_when_no_profile(self, client, session_factory):
        """No profiles at all → default generic speaker."""
        await _seed(session_factory, profile_user_name=None)

        r = await client.get("/api/conversations/conv-h/messages")
        msgs = r.json()
        by_id = {m["id"]: m for m in msgs}
        assert by_id["hm1"]["speaker"] == "用户"

    @pytest.mark.asyncio
    async def test_speaker_id_exposed(self, client, session_factory):
        """Group-ready: a message with speaker_id returns it verbatim (nullable)."""
        from datetime import datetime

        await _seed(session_factory)
        async with session_factory() as s:
            s.add(Message(
                id="hm5", conversation_id="conv-h", role="assistant",
                content="我不同意", speaker_id="char-b",
                created_at=datetime(2026, 9, 20, 6, 25, 4),
            ))
            await s.commit()

        r = await client.get("/api/conversations/conv-h/messages")
        msgs = r.json()
        by_id = {m["id"]: m for m in msgs}
        assert by_id["hm5"]["speaker_id"] == "char-b"


class TestSpeakerIdMigration:
    @pytest.mark.asyncio
    async def test_additive_migration_adds_nullable_speaker_id(self, session_factory):
        """Old DB without messages.speaker_id → ADDITIVE_MIGRATIONS adds it; data intact."""
        from database import _apply_additive_migrations

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Simulate a pre-ticket DB: rebuild messages without speaker_id
            # (SQLite cannot DROP a FOREIGN KEY column).
            await conn.execute(text("DROP TABLE messages"))
            await conn.execute(text(
                "CREATE TABLE messages ("
                " id VARCHAR(36) PRIMARY KEY,"
                " conversation_id VARCHAR(36) NOT NULL"
                "   REFERENCES conversations(id) ON DELETE CASCADE,"
                " role VARCHAR(20) NOT NULL,"
                " content TEXT,"
                " tool_calls JSON,"
                " tool_call_id VARCHAR(100),"
                " token_count INTEGER,"
                " created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
            ))
            # Seed a row the old way (no speaker_id).
            await conn.execute(text(
                "INSERT INTO messages (id, conversation_id, role, content) "
                "VALUES ('old-1', 'conv-x', 'user', '旧数据')"
            ))

        async with engine.begin() as conn:
            await _apply_additive_migrations(conn)

        async with engine.begin() as conn:
            info = await conn.execute(text("PRAGMA table_info(messages)"))
            cols = {row[1] for row in info.fetchall()}
            assert "speaker_id" in cols
            rows = (await conn.execute(text("SELECT id, speaker_id FROM messages"))).fetchall()
            assert rows == [("old-1", None)]  # nullable + data preserved
        await engine.dispose()
