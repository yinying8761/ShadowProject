"""
Tests for MemoryStore.add() deduplication.

Verifies that add() checks for similar content via FTS5 before inserting
and merges into the existing record when a near-duplicate is found.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from database import Base
from services.memory_store import MemoryStore


# ── Helpers ──────────────────────────────────────────────────────────────


async def _setup_db():
    """Create an in-memory SQLite database with FTS5 + triggers."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
            "content, memory_type, content_rowid='rowid',"
            "tokenize='unicode61 remove_diacritics 1'"
            ")"
        ))
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
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    return engine, factory


# ── Tests ──────────────────────────────────────────────────────────────────


class TestMemoryStoreAddDedup:
    """MemoryStore.add() prevents duplicates via find_similar()."""

    @pytest.mark.asyncio
    async def test_duplicate_content_returns_same_record(self):
        """Adding the same content twice returns the same memory (merged)."""
        engine, factory = await _setup_db()
        store = MemoryStore()

        async with factory() as session:
            mem1 = await store.add(
                session, "用户喜欢喝咖啡",
                memory_type="user_preference", importance=7,
            )
            mem2 = await store.add(
                session, "用户喜欢喝咖啡",
                memory_type="user_preference", importance=7,
            )

            assert mem1.id == mem2.id
            assert mem1.content == mem2.content
            await session.rollback()

    @pytest.mark.asyncio
    async def test_different_content_not_deduped(self):
        """Completely different content creates separate records."""
        engine, factory = await _setup_db()
        store = MemoryStore()

        async with factory() as session:
            mem1 = await store.add(
                session, "用户喜欢喝咖啡",
                memory_type="user_preference", importance=7,
            )
            mem2 = await store.add(
                session, "用户每天早上跑步",
                memory_type="user_preference", importance=5,
            )

            assert mem1.id != mem2.id
            await session.rollback()

    @pytest.mark.asyncio
    async def test_importance_merged_to_max(self):
        """When deduping, importance takes the max of both values."""
        engine, factory = await _setup_db()
        store = MemoryStore()

        async with factory() as session:
            await store.add(
                session, "用户的生日是1月15日",
                memory_type="user_fact", importance=8,
            )
            result = await store.add(
                session, "用户的生日是1月15日",
                memory_type="user_fact", importance=3,  # lower → keep 8
            )

            assert result.importance == 8
            await session.rollback()

    @pytest.mark.asyncio
    async def test_content_updated_to_newer_wording(self):
        """When deduping, content is replaced with newer wording."""
        engine, factory = await _setup_db()
        store = MemoryStore()

        async with factory() as session:
            await store.add(
                session, "用户喜欢喝咖啡",
                memory_type="user_preference", importance=5,
            )
            result = await store.add(
                session, "用户特别喜欢喝手冲咖啡尤其是耶加雪菲",
                memory_type="user_preference", importance=5,
            )

            assert "耶加雪菲" in result.content
            await session.rollback()
