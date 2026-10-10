"""群消息单调序号（ticket #56 / spec: group-chat · 落库顺序 / ADR-0009）。

`created_at` 是 SQLite 秒级精度：并发写入同秒的多条消息会**并列**，历史与上下文的
顺序因此不确定。这个文件锁住替代方案的行为：每条消息在它所属会话内带一个**单调
递增序号**，所有读路径按它排序；老库的既有行在迁移时**回填**。

Seam（都是既有缝，不新增）：
- `database.run_migration_sequence` —— 唯一权威的迁移顺序（CONTEXT §3.6）；
- `ConversationManager.add_message` / `get_context_messages` —— 写入分配 + LLM 上下文读序；
- `GET /api/conversations/{id}/messages` —— 历史接口读序（TestClient）；
- `MemoryExtractor.load_window` —— 群补账读窗口。
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from conftest import OLD_MESSAGES_DDL
from database import Base

# create_all 只建"已导入模型"的表（init_db 同样先导入）。
from models import character, conversation, group, message  # noqa: F401


async def _make_old_db(rows: str):
    """一个「加 seq 之前」的库：messages 还是老形状（无 seq），里面塞好既有数据。"""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP TABLE messages"))
        await conn.execute(text(OLD_MESSAGES_DDL))
        await conn.execute(text(rows))
    return engine


async def _migrate(engine):
    from database import run_migration_sequence

    async with engine.begin() as conn:
        await run_migration_sequence(conn)


async def _seqs(engine):
    """(conversation_id, id, seq)，按写入顺序（rowid）——比较列表时顺序是确定的。"""
    async with engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT conversation_id, id, seq FROM messages ORDER BY rowid"
        ))).fetchall()
    return rows


class TestSequenceBackfillMigration:
    @pytest.mark.asyncio
    async def test_backfills_old_rows_per_conversation_in_created_at_order(self):
        """老库回填：每会话从 1 开始，按 (created_at, 写入顺序) 升序 —— 两条会话独立编号。

        既有数据的顺序在迁移后必须与它原来的时间顺序一致（1:1 可见行为是纯增量）。
        """
        engine = await _make_old_db(
            # conv-a：同一秒三条（原本并列）+ 一条更早的
            "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES"
            " ('a2', 'conv-a', 'user', '第二句', '2026-09-20 06:25:00'),"
            " ('a1', 'conv-a', 'user', '第一句', '2026-09-20 06:24:00'),"
            " ('a3', 'conv-a', 'assistant', '第三句', '2026-09-20 06:25:00'),"
            # conv-b：写入顺序与时间顺序**相反**，回填必须按时间
            " ('b1', 'conv-b', 'user', '晚的', '2026-09-21 10:00:00'),"
            " ('b2', 'conv-b', 'user', '早的', '2026-09-21 09:00:00')"
        )
        await _migrate(engine)

        assert dict((mid, seq) for _, mid, seq in await _seqs(engine)) == {
            "a1": 1,
            "a2": 2,
            "a3": 3,
            "b2": 1,
            "b1": 2,
        }
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_backfill_is_idempotent_and_does_not_renumber(self):
        """重复迁移安全：已有序号不动（重跑不会把顺序重排）。"""
        engine = await _make_old_db(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES"
            " ('m1', 'conv-1', 'user', '一', '2026-09-20 06:25:00'),"
            " ('m2', 'conv-1', 'user', '二', '2026-09-20 06:25:00')"
        )
        await _migrate(engine)
        before = await _seqs(engine)

        # 迁移后再手动写一条带显式序号的行，模拟"迁移后新增"——
        # 重跑回填绝不能把它重编号（那会让历史顺序漂移）。
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO messages (id, conversation_id, role, content, seq) "
                "VALUES ('m3', 'conv-1', 'user', '三', 3)"
            ))
        await _migrate(engine)

        assert await _seqs(engine) == before + [("conv-1", "m3", 3)]
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_backfill_renumbers_a_mixed_conversation_in_time_order(self):
        """混入未编号的行（旧版本写进已迁移的库）：整条会话按时间重排。

        只把未编号的行"接在末尾"是不够的 —— 那样它与读路径的兜底（未编号 = 最前）
        说法相反，真实的时间位置也会丢。有未编号行的会话整条重排，两边才一致。
        """
        engine = await _make_old_db(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES"
            " ('m1', 'conv-1', 'user', '早', '2026-09-20 06:00:00'),"
            " ('m2', 'conv-1', 'user', '晚', '2026-09-20 08:00:00')"
        )
        await _migrate(engine)

        # 迁移之后又来了一个"没有序号"的写入，时间落在两条之间
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO messages (id, conversation_id, role, content, created_at, seq) "
                "VALUES ('m3', 'conv-1', 'user', '中间', '2026-09-20 07:00:00', NULL)"
            ))
        await _migrate(engine)

        assert dict((mid, seq) for _, mid, seq in await _seqs(engine)) == {
            "m1": 1,
            "m3": 2,
            "m2": 3,
        }
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_probe_flags_a_db_that_needs_backfill(self):
        """`init_db` 靠这个探测决定"要不要先备份"：列没加、或还有待编号的行 → True。"""
        from database import messages_need_sequence_backfill

        engine = await _make_old_db(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES ('m1', 'conv-1', 'user', '老消息')"
        )
        async with engine.begin() as conn:
            assert await messages_need_sequence_backfill(conn) is True

        await _migrate(engine)
        async with engine.begin() as conn:
            assert await messages_need_sequence_backfill(conn) is False  # 全编号了 → 不用再备份
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_probe_is_false_on_a_fresh_db(self, engine):
        """新库（create_all 直接带 seq 列、无数据）→ 不触发备份。"""
        from database import messages_need_sequence_backfill

        async with engine.begin() as conn:
            assert await messages_need_sequence_backfill(conn) is False


class TestSequenceAssignment:
    @pytest.mark.asyncio
    async def test_numbers_each_conversation_independently(self, session_factory):
        """写入分配：会话内 1,2,3…，每条会话各数各的。"""
        from core.conversation_manager import ConversationManager

        mgr = ConversationManager()
        async with session_factory() as s:
            a1 = await mgr.add_message(s, "conv-a", "user", "一")
            a2 = await mgr.add_message(s, "conv-a", "assistant", "二")
            a3 = await mgr.add_message(s, "conv-a", "user", "三")
            b1 = await mgr.add_message(s, "conv-b", "user", "另一条会话")

        assert (a1.seq, a2.seq, a3.seq) == (1, 2, 3)
        assert b1.seq == 1

    @pytest.mark.asyncio
    async def test_continues_after_backfilled_history(self):
        """迁移后新增：接着回填出来的最大序号继续，不从头开始（否则与老消息并列）。"""
        from core.conversation_manager import ConversationManager
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        engine = await _make_old_db(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES"
            " ('a1', 'conv-a', 'user', '老的一', '2026-09-20 06:24:00'),"
            " ('a2', 'conv-a', 'user', '老的二', '2026-09-20 06:25:00')"
        )
        await _migrate(engine)

        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            fresh = await ConversationManager().add_message(s, "conv-a", "user", "新的")

        assert fresh.seq == 3
        await engine.dispose()


#: 三条消息写进去的顺序 = 序号顺序，但时间戳**故意反着来**：
#: 同秒并列 + 写入错位都会造成这种局面，按时间戳排会得到完全相反的顺序。
_ADVERSARIAL_STAMPS = {
    1: (2026, 9, 20, 6, 25, 5),
    2: (2026, 9, 20, 6, 25, 3),
    3: (2026, 9, 20, 6, 25, 1),
}


async def _seed_adversarial(factory, conv_id: str = "conv-h"):
    """按序号 1/2/3 写入「第一句/第二句/第三句」，再把时间戳改成倒序。"""
    from datetime import datetime

    from sqlalchemy import update

    from core.conversation_manager import ConversationManager
    from models.message import Message

    mgr = ConversationManager()
    async with factory() as s:
        for text in ("第一句", "第二句", "第三句"):
            await mgr.add_message(s, conv_id, "user", text)
    async with factory() as s:
        for seq, (y, mo, d, h, mi, sec) in _ADVERSARIAL_STAMPS.items():
            await s.execute(
                update(Message)
                .where(Message.conversation_id == conv_id, Message.seq == seq)
                .values(created_at=datetime(y, mo, d, h, mi, sec))
            )
        await s.commit()


class TestReadOrder:
    """顺序一律以**序号**为准，`created_at` 只是无序号老行的兜底（ADR-0009）。"""

    @pytest.mark.asyncio
    async def test_history_api_orders_by_sequence(self, client, session_factory):
        """历史记录接口：时间戳反着写也不影响顺序 —— 谁先说的由序号决定。"""
        await _seed_adversarial(session_factory)

        resp = await client.get("/api/conversations/conv-h/messages")
        assert resp.status_code == 200
        assert [m["content"] for m in resp.json()] == ["第一句", "第二句", "第三句"]

    @pytest.mark.asyncio
    async def test_history_api_is_stable_across_rereads(self, client, session_factory):
        """反复重读顺序一致（同秒并列不再抖动）。"""
        await _seed_adversarial(session_factory)

        orders = []
        for _ in range(3):
            resp = await client.get("/api/conversations/conv-h/messages")
            orders.append([m["content"] for m in resp.json()])
        assert orders[0] == orders[1] == orders[2]

    @pytest.mark.asyncio
    async def test_llm_context_orders_by_sequence(self, session_factory):
        """喂 LLM 的上下文与历史记录同序（同一份顺序规则）。"""
        from core.conversation_manager import ConversationManager

        await _seed_adversarial(session_factory)

        async with session_factory() as s:
            ctx = await ConversationManager().get_context_messages(s, "conv-h")

        assert [c["content"].split("]: ")[-1] for c in ctx] == ["第一句", "第二句", "第三句"]

    @pytest.mark.asyncio
    async def test_compact_trims_the_oldest_by_sequence(self, session_factory):
        """摘要裁剪按序号取"最老的"：按时间戳会裁错人。"""
        from core.conversation_manager import ConversationManager

        await _seed_adversarial(session_factory)

        async with session_factory() as s:
            result = await ConversationManager().summarize_and_trim(
                s, "conv-h", keep_count=1, llm_service=None
            )
        async with session_factory() as s:
            left = (await s.execute(
                text("SELECT content FROM messages WHERE conversation_id = 'conv-h'")
            )).fetchall()

        assert result["deleted"] == 2
        assert [row[0] for row in left] == ["第三句"]

    @pytest.mark.asyncio
    async def test_memory_window_orders_by_sequence(self, session_factory, monkeypatch):
        """群补账读窗口（`load_window`）按序号 —— 记忆提取看到的顺序与聊天一致。"""
        import services.memory_extractor as me_mod
        from services.memory_extractor import MemoryExtractor
        from services.memory_store import MemoryStore

        await _seed_adversarial(session_factory)
        monkeypatch.setattr(me_mod, "async_session", session_factory)

        messages = await MemoryExtractor(MemoryStore()).load_window("conv-h")
        assert [m.content for m in messages] == ["第一句", "第二句", "第三句"]


class TestConcurrentWrites:
    """并发落库（ADR-0009 的起因）：序号不并列，顺序不抖动。

    **必须用文件库**（`ws_env` / `file_client`）：内存库（`sqlite+aiosqlite://`）走
    SQLAlchemy 的 StaticPool，所有 session 共享同一条连接 —— 那样根本没有并发，
    测试既红不了也验证不到写锁。
    """

    @pytest.mark.asyncio
    async def test_concurrent_writes_never_share_a_sequence(self, ws_env):
        """同一秒并发写 6 条：序号恰好是 1..6，谁也不与谁并列。

        这是群聊并发化的前提：多个成员同时落库时，"谁先说的"必须唯一确定。
        序号在**写锁**下分配（add_message 先推进 conversations.updated_at）——
        少了那一步，并发的 writer 会各自读到同一个 MAX 而全部写出 1（实测如此）。
        """
        import asyncio

        from core.conversation_manager import ConversationManager
        from models.conversation import Conversation

        _, factory = ws_env
        async with factory() as s:
            s.add(Conversation(id="conv-c", character_id=None))
            await s.commit()

        async def write(text: str):
            async with factory() as s:
                return await ConversationManager().add_message(s, "conv-c", "user", text)

        written = await asyncio.gather(*(write(f"第{i}句") for i in range(6)))

        assert sorted(m.seq for m in written) == [1, 2, 3, 4, 5, 6]

    @pytest.mark.asyncio
    async def test_concurrent_burst_reads_back_in_sequence_order(self, ws_env, file_client):
        """并发写入后反复重读：顺序稳定，且等于序号顺序（= 落库顺序）。"""
        import asyncio

        from core.conversation_manager import ConversationManager
        from models.conversation import Conversation

        _, factory = ws_env
        async with factory() as s:
            s.add(Conversation(id="conv-c2", character_id=None))
            await s.commit()

        async def write(text: str):
            async with factory() as s:
                return await ConversationManager().add_message(s, "conv-c2", "user", text)

        written = await asyncio.gather(*(write(f"第{i}句") for i in range(6)))
        by_seq = [m.content for m in sorted(written, key=lambda m: m.seq)]

        orders = []
        for _ in range(3):
            resp = await file_client.get("/api/conversations/conv-c2/messages")
            orders.append([m["content"] for m in resp.json()])

        assert orders[0] == orders[1] == orders[2] == by_seq


class TestOneToOneUnchanged:
    """1:1 的可见行为必须是**纯增量**（ticket #56）：顺序与载荷形状都不变。"""

    @pytest.mark.asyncio
    async def test_history_order_and_payload_are_unchanged(self, client, session_factory):
        """同一秒的 1:1 三条：按说话顺序返回，字段一个不多一个不少（不泄漏 seq）。"""
        from datetime import datetime

        from sqlalchemy import update

        from core.conversation_manager import ConversationManager
        from models.message import Message

        async with session_factory() as s:
            for text in ("第一句", "第二句", "第三句"):
                await ConversationManager().add_message(s, "conv-1to1", "user", text)
        async with session_factory() as s:
            # 1:1 快速对话本来就常落在同一秒里：这正是改造前会并列的场景
            await s.execute(
                update(Message)
                .where(Message.conversation_id == "conv-1to1")
                .values(created_at=datetime(2026, 9, 20, 6, 25, 0))
            )
            await s.commit()

        resp = await client.get("/api/conversations/conv-1to1/messages")
        body = resp.json()

        assert [m["content"] for m in body] == ["第一句", "第二句", "第三句"]
        # 载荷 = 既有契约（api/conversation.py::get_messages）+ 一个刻意的增量：`seq`
        # （ticket #11 要按序号把在途消息插到正确位置；1:1 拿到也会忽略它）。
        assert set(body[0]) == {
            "id", "role", "content", "tool_calls", "token_count",
            "created_at", "speaker_id", "speaker", "transcript", "seq",
        }
