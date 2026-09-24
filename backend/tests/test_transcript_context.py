"""Consumer ① of the transcript renderer: LLM context assembly.

Seam: ConversationManager.get_context_messages — user/assistant TEXT messages
come back with the timestamped-transcript prefix; tool messages and tool-call
carriers keep their raw content; roles are untouched (pure content increment,
1:1 semantics unchanged — spec S1' / ticket #50).
"""

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models.character import CharacterProfile
from models.conversation import Conversation
from models.message import Message


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory):
    async with factory() as s:
        s.add(CharacterProfile(id="char-ctx", name="小柔", personality="", role="companion", archetype="friend"))
        s.add(Conversation(id="conv-ctx", character_id="char-ctx"))
        # naive = UTC → 14:25 +8; strictly increasing seconds (SQLite ties
        # on same-second created_at are not order-guaranteed — pre-existing).
        base = datetime(2026, 9, 20, 6, 25, 0)
        s.add(Message(id="m1", conversation_id="conv-ctx", role="user", content="现在几点了", created_at=base))
        s.add(Message(
            id="m2", conversation_id="conv-ctx", role="assistant", content="",
            created_at=datetime(2026, 9, 20, 6, 25, 1),
            tool_calls=[{"id": "c1", "name": "get_current_time", "arguments": {}}],
        ))
        s.add(Message(
            id="m3", conversation_id="conv-ctx", role="tool",
            content='{"local_time": "2026-09-20 14:25:00"}', created_at=datetime(2026, 9, 20, 6, 25, 2), tool_call_id="c1",
        ))
        s.add(Message(id="m4", conversation_id="conv-ctx", role="assistant", content="现在是下午两点半哦", created_at=datetime(2026, 9, 20, 6, 25, 3)))
        await s.commit()


class TestGroupSpeakersInContext:
    """群对话：喂给 LLM 的历史里，每条 assistant 消息按**自己的** speaker_id 报名字。"""

    @pytest.fixture
    async def group_factory(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        yield factory
        await engine.dispose()

    async def test_each_group_message_carries_its_own_speaker(self, group_factory):
        from core.conversation_manager import ConversationManager
        from models.group import Group

        async with group_factory() as s:
            s.add(CharacterProfile(id="c1", name="小柔", personality="", role="companion", archetype="friend"))
            s.add(CharacterProfile(id="c2", name="阿B", personality="", role="companion", archetype="friend"))
            s.add(Group(id="g1", name="小群"))
            s.add(Conversation(id="conv-g", character_id=None, group_id="g1"))
            base = datetime(2026, 9, 20, 6, 25, 0)
            s.add(Message(id="gm1", conversation_id="conv-g", role="user", content="你们好", created_at=base))
            s.add(Message(
                id="gm2", conversation_id="conv-g", role="assistant", content="在的",
                speaker_id="c1", created_at=datetime(2026, 9, 20, 6, 26, 0),
            ))
            s.add(Message(
                id="gm3", conversation_id="conv-g", role="assistant", content="我也在",
                speaker_id="c2", created_at=datetime(2026, 9, 20, 6, 27, 0),
            ))
            await s.commit()

        mgr = ConversationManager()
        async with group_factory() as s:
            ctx = await mgr.get_context_messages(s, "conv-g", user_name="小明")

        assert [m["content"] for m in ctx] == [
            "2026/9/20 14:25 [小明]: 你们好",
            "2026/9/20 14:26 [小柔]: 在的",
            "2026/9/20 14:27 [阿B]: 我也在",
        ]


class TestGetContextMessagesTranscript:
    @pytest.mark.asyncio
    async def test_text_messages_prefixed_tool_raw(self, factory):
        """Text user/assistant get the prefix; tool plumbing stays raw; roles unchanged."""
        from core.conversation_manager import ConversationManager

        await _seed(factory)
        mgr = ConversationManager()
        async with factory() as s:
            ctx = await mgr.get_context_messages(
                s, "conv-ctx", user_name="小明", character_name="小柔",
            )
        assert [m["role"] for m in ctx] == ["user", "assistant", "tool", "assistant"]
        assert ctx[0]["content"] == "2026/9/20 14:25 [小明]: 现在几点了"
        assert ctx[1]["content"] == ""  # tool-call carrier: no prefix
        assert ctx[2]["content"] == '{"local_time": "2026-09-20 14:25:00"}'  # tool: raw
        assert ctx[3]["content"] == "2026/9/20 14:25 [小柔]: 现在是下午两点半哦"

    @pytest.mark.asyncio
    async def test_without_names_uses_defaults(self, factory):
        """No names passed → default user speaker; assistant falls back to 'AI'."""
        from core.conversation_manager import ConversationManager

        await _seed(factory)
        mgr = ConversationManager()
        async with factory() as s:
            ctx = await mgr.get_context_messages(s, "conv-ctx")
        assert ctx[0]["content"] == "2026/9/20 14:25 [用户]: 现在几点了"
        assert ctx[3]["content"] == "2026/9/20 14:25 [AI]: 现在是下午两点半哦"


class TestPrefixDoesNotChangeTheWindow:
    @pytest.mark.asyncio
    async def test_trim_boundary_is_unchanged_by_the_prefix(self, factory):
        """spec: 前缀是**纯增量**——滑动窗口装下哪些消息必须和加前缀之前一致。

        3 条消息内容各 40 个 ASCII 字符（原始估算 40 × 0.25 = 10 token/条）。
        max_tokens=30 时按原始内容估算刚好装下 3 条；若把前缀（约 +6 token/条）
        也算进预算，就只能装下 1 条。这里锁住前者。
        """
        from core.conversation_manager import ConversationManager

        async with factory() as s:
            s.add(Conversation(id="conv-win", character_id="char-ctx"))
            for i in range(3):
                s.add(Message(
                    id=f"w{i}", conversation_id="conv-win", role="user",
                    content="a" * 40, created_at=datetime(2026, 9, 20, 6, 25, i),
                ))
            await s.commit()

        mgr = ConversationManager()
        async with factory() as s:
            ctx = await mgr.get_context_messages(
                s, "conv-win", max_tokens=30, user_name="小明", character_name="小柔",
            )

        assert len(ctx) == 3
        assert all(m["content"].startswith("2026/9/20 14:25 [小明]: ") for m in ctx)
