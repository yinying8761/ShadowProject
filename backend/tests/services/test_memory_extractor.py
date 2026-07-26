"""
Tests for MemoryExtractor — LLM-driven extraction, JSON parsing,
deduplication, embedding, and storage.

extract_and_store() creates its own session via database.async_session,
so tests monkeypatch it to use an in-memory SQLite engine.
"""

import json
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from database import Base
from services.memory_store import MemoryStore
from services.memory_extractor import MemoryExtractor


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


async def _seed_messages(session, conversation_id: str, messages: list[tuple[str, str]]):
    """Insert messages. Each tuple is (role, content)."""
    from models.message import Message

    for role, content in messages:
        msg = Message(conversation_id=conversation_id, role=role, content=content)
        session.add(msg)
    await session.commit()


class FakeLLM:
    """Fake LLM service that returns a preset chat_sync result."""

    def __init__(self, response: str):
        self._response = response

    async def chat_sync(self, messages, **kwargs):
        return self._response


# ── Tests ─────────────────────────────────────────────────────────────────


class TestMemoryExtractor:
    @pytest.mark.asyncio
    async def test_insufficient_messages_returns_empty(self, monkeypatch):
        """Fewer than EXTRACTION_MIN_MESSAGES → returns [] without calling LLM."""
        engine, factory = await _setup_db()
        async with factory() as session:
            await _seed_messages(session, "conv-1", [
                ("user", "hello"),
                ("assistant", "hi!"),
            ])

        # Patch async_session so extract_and_store uses the test DB
        import services.memory_extractor as me
        monkeypatch.setattr(me, "async_session", factory)

        extractor = MemoryExtractor(MemoryStore())
        results = await extractor.extract_and_store(
            "conv-1", FakeLLM("SHOULD NOT BE CALLED")
        )

        assert results == []
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_valid_json_extraction_and_store(self, monkeypatch):
        """Valid JSON from LLM → parsed, deduped, stored."""
        engine, factory = await _setup_db()
        async with factory() as session:
            msgs = [("user", f"message {i}") for i in range(10)]
            msgs.append(("assistant", "reply"))
            await _seed_messages(session, "conv-2", msgs)

        import services.memory_extractor as me
        monkeypatch.setattr(me, "async_session", factory)

        fake_llm = FakeLLM(json.dumps([
            {"content": "user likes coffee especially latte",
             "memory_type": "user_preference", "importance": 7},
        ]))

        extractor = MemoryExtractor(MemoryStore())
        results = await extractor.extract_and_store("conv-2", fake_llm)

        assert len(results) == 1
        assert "coffee" in results[0].content
        assert results[0].memory_type == "user_preference"
        assert results[0].importance == 7
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_markdown_wrapped_json_parsed(self, monkeypatch):
        """JSON wrapped in markdown code fences → still extracted and parsed."""
        engine, factory = await _setup_db()
        async with factory() as session:
            msgs = [("user", f"msg {i}") for i in range(10)]
            msgs.append(("assistant", "ok"))
            await _seed_messages(session, "conv-3", msgs)

        import services.memory_extractor as me
        monkeypatch.setattr(me, "async_session", factory)

        fake_llm = FakeLLM(
            '```json\n[{"content": "user is learning Rust", '
            '"memory_type": "user_fact", "importance": 6}]\n```'
        )

        extractor = MemoryExtractor(MemoryStore())
        results = await extractor.extract_and_store("conv-3", fake_llm)

        assert len(results) == 1
        assert "Rust" in results[0].content
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self, monkeypatch):
        """Totally invalid JSON → returns [] without crashing."""
        engine, factory = await _setup_db()
        async with factory() as session:
            msgs = [("user", f"msg {i}") for i in range(10)]
            await _seed_messages(session, "conv-4", msgs)

        import services.memory_extractor as me
        monkeypatch.setattr(me, "async_session", factory)

        fake_llm = FakeLLM("not JSON, just random text")

        extractor = MemoryExtractor(MemoryStore())
        results = await extractor.extract_and_store("conv-4", fake_llm)

        assert results == []
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_dedup_merges_importance(self, monkeypatch):
        """Similar content → importance is merged (max), content updated."""
        engine, factory = await _setup_db()
        async with factory() as session:
            msgs = [("user", f"msg {i}") for i in range(10)]
            await _seed_messages(session, "conv-5", msgs)

        import services.memory_extractor as me
        monkeypatch.setattr(me, "async_session", factory)

        store = MemoryStore()
        extractor = MemoryExtractor(store)

        # First extraction
        fake_llm_1 = FakeLLM(json.dumps([
            {"content": "user likes coffee",
             "memory_type": "user_preference", "importance": 5},
        ]))
        results_1 = await extractor.extract_and_store("conv-5", fake_llm_1)
        assert len(results_1) == 1
        assert results_1[0].importance == 5

        # Seed more messages (need 10+ to trigger extraction again)
        async with factory() as session:
            msgs2 = [("user", f"msg2 {i}") for i in range(10)]
            await _seed_messages(session, "conv-5", msgs2)

        # Second extraction — same content, higher importance
        fake_llm_2 = FakeLLM(json.dumps([
            {"content": "user likes coffee",
             "memory_type": "user_preference", "importance": 9},
        ]))
        results_2 = await extractor.extract_and_store("conv-5", fake_llm_2)

        # Dedup merged: same memory, importance updated to max(5, 9) = 9
        assert len(results_2) == 1
        assert results_2[0].importance == 9
        assert results_2[0].id == results_1[0].id  # same memory, not a new one
        await engine.dispose()
