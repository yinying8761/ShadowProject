"""
Tests for MemoryRetriever — hybrid search pipeline.
Uses in-memory SQLite with FTS5 for realistic integration tests.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from database import Base
from services.memory_store import MemoryStore
from services.memory_retriever import MemoryRetriever


# ── Helpers ──────────────────────────────────────────────────────────────


async def _setup_db():
    """Create an in-memory SQLite database with FTS5 and return a session."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # FTS5 virtual table
        await conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
            "content, memory_type, content_rowid='rowid',"
            "tokenize='unicode61 remove_diacritics 1'"
            ")"
        ))
        # Triggers to keep FTS5 in sync with memories table
        await conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS memory_fts_insert "
            "AFTER INSERT ON memories BEGIN "
            "INSERT INTO memory_fts(rowid, content, memory_type) "
            "VALUES (new.rowid, new.content, new.memory_type); END"
        ))
        await conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS memory_fts_delete "
            "AFTER DELETE ON memories BEGIN "
            "INSERT INTO memory_fts(memory_fts, rowid, content, memory_type) "
            "VALUES ('delete', old.rowid, old.content, old.memory_type); END"
        ))
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    return engine, session_factory


async def _seed_memory(session, content: str, **kwargs):
    """Insert one memory — the FTS5 trigger syncs the virtual table."""
    from models.memory import Memory

    mem = Memory(
        content=content,
        memory_type=kwargs.get("memory_type", "user_fact"),
        importance=kwargs.get("importance", 5),
        source_conversation_id=kwargs.get("source_conversation_id"),
    )
    session.add(mem)
    await session.commit()
    await session.refresh(mem)
    return mem


# ── Tests ─────────────────────────────────────────────────────────────────


class TestMemoryRetriever:
    @pytest.mark.asyncio
    async def test_empty_query_returns_recent_important(self):
        """Empty query → memories ranked by importance × recency."""
        engine, factory = await _setup_db()
        async with factory() as session:
            await _seed_memory(session, "低重要性旧记忆", importance=2)
            await _seed_memory(session, "高重要性新记忆", importance=9)
            await _seed_memory(session, "中等记忆", importance=5)

            retriever = MemoryRetriever(MemoryStore())
            results = await retriever.search(session, "", top_k=2)

            assert len(results) == 2
            assert results[0].importance >= results[1].importance
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_fts5_search_returns_matches(self):
        """FTS5 query returns matching memories."""
        engine, factory = await _setup_db()
        async with factory() as session:
            await _seed_memory(session, "user likes coffee")
            await _seed_memory(session, "user lives in Beijing")
            await _seed_memory(session, "user enjoys programming")

            retriever = MemoryRetriever(MemoryStore())
            results = await retriever.search(session, "coffee", top_k=3)

            assert len(results) >= 1
            assert any("coffee" in r.content for r in results)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_character_filter(self):
        """Memories are filtered by character_id via source conversation."""
        engine, factory = await _setup_db()
        async with factory() as session:
            from models.character import CharacterProfile
            from models.conversation import Conversation

            char_a = CharacterProfile(
                id="char-a", name="角色A", personality="A",
                role="companion", archetype="friend",
            )
            char_b = CharacterProfile(
                id="char-b", name="角色B", personality="B",
                role="companion", archetype="friend",
            )
            conv_a = Conversation(id="conv-a", character_id="char-a")
            conv_b = Conversation(id="conv-b", character_id="char-b")
            session.add_all([char_a, char_b, conv_a, conv_b])
            await session.commit()

            await _seed_memory(session, "memory from char A", source_conversation_id="conv-a")
            await _seed_memory(session, "memory from char B", source_conversation_id="conv-b")

            retriever = MemoryRetriever(MemoryStore())
            results = await retriever.search(
                session, "memory", top_k=5, character_id="char-a"
            )

            assert len(results) == 1
            assert "char A" in results[0].content
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_fts5_exception_fallback_does_not_crash(self):
        """When FTS5 table is missing, search falls back gracefully."""
        engine, factory = await _setup_db()
        async with factory() as session:
            from models.memory import Memory

            # Drop FTS5 table and triggers to simulate unavailability
            await session.execute(text("DROP TRIGGER IF EXISTS memory_fts_insert"))
            await session.execute(text("DROP TRIGGER IF EXISTS memory_fts_delete"))
            await session.execute(text("DROP TABLE IF EXISTS memory_fts"))
            await session.commit()

            # Seed memory directly (no FTS5 sync — table is gone)
            mem = Memory(content="用户喜欢喝茶", importance=8)
            session.add(mem)
            await session.commit()

            retriever = MemoryRetriever(MemoryStore())
            results = await retriever.search(session, "茶", top_k=3)

            # Fallback returns recent important memories without crashing
            assert isinstance(results, list)
        await engine.dispose()
