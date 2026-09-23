"""conversations.character_id NOT NULL → nullable 的重建迁移（ticket #51）。

Seam: database.ensure_group_schema — 老库一次性重建（建新表→拷数据→换名）：
数据完整保留（含 created_at 等逐字段的值）、character_id 变可空、
幂等（重复执行安全）、破坏性步骤前回调备份。

调用顺序约定与 init_db 一致：create_all → _apply_additive_migrations →
ensure_group_schema（重建的 INSERT..SELECT 依赖 additive 先补齐 group_id/last_extract_at）。
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from conftest import OLD_CONVERSATIONS_DDL
from database import Base


async def _make_old_db():
    """Fresh engine whose conversations table is the PRE-GROUP shape (NOT NULL)."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP TABLE conversations"))
        await conn.execute(text(OLD_CONVERSATIONS_DDL))
        await conn.execute(text(
            "INSERT INTO conversations (id, character_id, title, summary, created_at, updated_at) VALUES "
            "('conv-1', 'c1', '旧的会话', '旧的摘要', '2026-09-20 06:25:00', '2026-09-20 07:00:00'),"
            "('conv-2', 'c1', '第二段对话', NULL, '2026-09-21 10:00:00', '2026-09-21 10:30:00')"
        ))
        await conn.execute(text(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES ('m-1', 'conv-1', 'user', '你好')"
        ))
    return engine


async def _run_migration(engine):
    """init_db 的迁移序列（单一权威实现：database.run_migration_sequence）。"""
    from database import run_migration_sequence
    async with engine.begin() as conn:
        return await run_migration_sequence(conn)


async def _conversations_schema(conn):
    info = await conn.execute(text("PRAGMA table_info(conversations)"))
    return {row[1]: row for row in info.fetchall()}  # name → row


class TestConversationsRebuildMigration:
    @pytest.mark.asyncio
    async def test_rebuild_makes_character_id_nullable_and_preserves_data(self):
        """老库重建：character_id 可空、数据逐字段保留、新列就位、消息外键仍指向 conversations。"""
        engine = await _make_old_db()
        ran = await _run_migration(engine)
        assert ran is True

        async with engine.begin() as conn:
            schema = await _conversations_schema(conn)
            assert schema["character_id"][3] == 0  # NOT NULL → 可空
            assert {"group_id", "last_extract_at"} <= set(schema)

            rows = (await conn.execute(text(
                "SELECT id, character_id, title, summary, created_at, updated_at "
                "FROM conversations ORDER BY id"
            ))).fetchall()
            assert rows == [
                ("conv-1", "c1", "旧的会话", "旧的摘要", "2026-09-20 06:25:00", "2026-09-20 07:00:00"),
                ("conv-2", "c1", "第二段对话", None, "2026-09-21 10:00:00", "2026-09-21 10:30:00"),
            ]

            # messages.conversation_id 的外键目标（表名）在重建后仍然成立
            joined = (await conn.execute(text(
                "SELECT m.id FROM messages m JOIN conversations c ON m.conversation_id = c.id"
            ))).fetchall()
            assert joined == [("m-1",)]
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_idempotent_second_run_is_noop(self):
        """重复执行安全：第二次不再重建、数据不动。"""
        engine = await _make_old_db()
        assert await _run_migration(engine) is True
        assert await _run_migration(engine) is False

        async with engine.begin() as conn:
            schema = await _conversations_schema(conn)
            assert schema["character_id"][3] == 0
            count = (await conn.execute(text("SELECT count(*) FROM conversations"))).scalar()
            assert count == 2
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_fresh_schema_skips_rebuild(self):
        """新库（create_all 直接建出新形状）→ 不触发重建。"""
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        assert await _run_migration(engine) is False
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_group_conversation_row_survives_after_rebuild(self):
        """重建后可以写入群对话（character_id NULL + group_id）——迁移的最终目的。"""
        engine = await _make_old_db()
        await _run_migration(engine)

        from models.character import CharacterProfile
        from models.conversation import Conversation
        from models.group import Group
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            s.add(CharacterProfile(id="c1", name="小柔", personality="", role="companion", archetype="friend"))
            s.add(Group(id="g1", name="群"))
            s.add(Conversation(id="conv-g", character_id=None, group_id="g1"))
            await s.commit()
        async with factory() as s:
            conv = await s.get(Conversation, "conv-g")
            assert conv.character_id is None and conv.group_id == "g1"
        await engine.dispose()


class TestRebuiltSchemaParity:
    @pytest.mark.asyncio
    async def test_rebuilt_table_matches_create_all_shape(self):
        """重建表与模型 create_all 的形状逐列一致（列序/类型/NOT NULL/默认值/PK/外键）。

        ensure_group_schema 手写 DDL 复刻 models.conversation——此测试把
        "逐列一致"从注释变成被锁定的行为，改模型若忘了同步 DDL 会在这里红。
        """
        from sqlalchemy.ext.asyncio import create_async_engine as _cae

        fresh = _cae("sqlite+aiosqlite://", echo=False)
        async with fresh.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        old = await _make_old_db()
        await _run_migration(old)

        async def shape(engine):
            async with engine.begin() as conn:
                info = await conn.execute(text("PRAGMA table_info(conversations)"))
                cols = [
                    (r[1], r[2], r[3], r[4], r[5])  # name, type, notnull, dflt, pk
                    for r in info.fetchall()
                ]
                fks = await conn.execute(text("PRAGMA foreign_key_list(conversations)"))
                fk_rows = sorted(fks.fetchall())
                return {"cols": cols, "fks": fk_rows}

        old_shape, fresh_shape = await shape(old), await shape(fresh)
        assert old_shape["cols"] == fresh_shape["cols"], (
            f"rebuild DDL drifted from the model:\nold ={old_shape['cols']}\nfresh={fresh_shape['cols']}"
        )
        assert old_shape["fks"] == fresh_shape["fks"]
        await old.dispose()
        await fresh.dispose()
