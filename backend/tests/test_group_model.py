"""Group data model foundation (ticket #51, spec: group-chat Phase 2).

Seams:
- models.Group / models.GroupMember (fixed member set + speaking order) via create_all
- Conversation.group_id / last_extract_at: fresh-DB shape + additive migration on an old DB
- A group conversation exists WITHOUT a single character (character_id NULL) —
  the modeling decision ADR-0004 records.
"""

from datetime import datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models.character import CharacterProfile
from models.conversation import Conversation
from models.group import Group, GroupMember
from models.message import Message


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _seed_characters(s):
    s.add(CharacterProfile(id="c1", name="小柔", personality="", role="companion", archetype="friend"))
    s.add(CharacterProfile(id="c2", name="阿B", personality="", role="companion", archetype="friend"))


class TestGroupModels:
    @pytest.mark.asyncio
    async def test_group_with_ordered_members_roundtrip(self, factory):
        """Group + members with speaking order (position) persist and order by position."""
        async with factory() as s:
            await _seed_characters(s)
            s.add(Group(id="g1", name="周末火锅群"))
            s.add(GroupMember(group_id="g1", character_id="c2", position=1))
            s.add(GroupMember(group_id="g1", character_id="c1", position=0))
            await s.commit()

        async with factory() as s:
            members = (await s.execute(
                select(GroupMember)
                .where(GroupMember.group_id == "g1")
                .order_by(GroupMember.position)
            )).scalars().all()
            assert [m.character_id for m in members] == ["c1", "c2"]

    @pytest.mark.asyncio
    async def test_same_character_cannot_join_group_twice(self, factory):
        """固定成员集合：(group_id, character_id) 唯一。"""
        async with factory() as s:
            await _seed_characters(s)
            s.add(Group(id="g1", name="群"))
            s.add(GroupMember(group_id="g1", character_id="c1", position=0))
            await s.commit()

        async with factory() as s:
            s.add(GroupMember(group_id="g1", character_id="c1", position=1))
            with pytest.raises(IntegrityError):
                await s.commit()

    @pytest.mark.asyncio
    async def test_group_conversation_without_single_character(self, factory):
        """群对话没有单一角色：character_id NULL + group_id 指向群；消息带 speaker_id。"""
        async with factory() as s:
            await _seed_characters(s)
            s.add(Group(id="g2", name="群"))
            s.add(Conversation(
                id="conv-g", character_id=None, group_id="g2",
                last_extract_at=datetime(2026, 9, 21, 8, 0, 0),
            ))
            s.add(Message(
                conversation_id="conv-g", role="assistant", content="我不同意",
                speaker_id="c2", created_at=datetime(2026, 9, 21, 8, 0, 5),
            ))
            await s.commit()

        async with factory() as s:
            conv = await s.get(Conversation, "conv-g")
            assert conv.character_id is None
            assert conv.group_id == "g2"
            assert conv.last_extract_at == datetime(2026, 9, 21, 8, 0, 0)
            msg = (await s.execute(
                select(Message).where(Message.conversation_id == "conv-g")
            )).scalar_one()
            assert msg.speaker_id == "c2"


class TestConversationGroupColumnsMigration:
    @pytest.mark.asyncio
    async def test_additive_migration_adds_group_columns(self, old_conversations_ddl):
        """Old DB (pre-group conversations table) → ADDITIVE_MIGRATIONS adds both
        columns; character_id stays NOT NULL (the rebuild migration owns that)."""
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Simulate a pre-group DB (FK columns cannot be DROPped in SQLite).
            await conn.execute(text("DROP TABLE conversations"))
            await conn.execute(text(old_conversations_ddl))

        from database import _apply_additive_migrations
        async with engine.begin() as conn:
            await _apply_additive_migrations(conn)
            info = await conn.execute(text("PRAGMA table_info(conversations)"))
            rows = info.fetchall()
            cols = {row[1]: row[3] for row in rows}  # name → notnull
            assert {"group_id", "last_extract_at"} <= set(cols)
            assert cols["character_id"] == 1  # additive 不改约束，交给重建迁移
        await engine.dispose()
