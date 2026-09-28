"""Consumer ② of the transcript renderer: history API (ticket #50, spec S2).

Seam: GET /api/conversations/{id}/messages — each message carries
`transcript` (shared-renderer line, null for tool plumbing) and
`speaker` (resolved name). Also covers the additive `messages.speaker_id`
migration (nullable, data preserved) through the authoritative migration
sequence (ADR-0004).

Fixtures `engine` / `session_factory` / `client` and the pre-group DDL
constants live in `conftest.py`.
"""

import warnings
from datetime import datetime

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import create_async_engine

from conftest import OLD_MESSAGES_DDL
from database import Base
from models.character import CharacterProfile
from models.conversation import Conversation
from models.group import Group, GroupMember
from models.message import Message
from models.user_profile import UserProfile

BASE_TIME = datetime(2026, 9, 20, 6, 25, 0)  # naive UTC → 14:25 in +8


async def _seed(factory, *, profile_user_name="小明"):
    """Character + conversation + one exchange (+ tool plumbing)."""
    async with factory() as s:
        s.add(CharacterProfile(id="char-h", name="小柔", personality="", role="companion", archetype="friend"))
        s.add(Conversation(id="conv-h", character_id="char-h"))
        if profile_user_name is not None:
            s.add(UserProfile(character_id="char-h", user_name=profile_user_name))
        s.add(Message(id="hm1", conversation_id="conv-h", role="user", content="现在几点了", created_at=BASE_TIME))
        s.add(Message(
            id="hm2", conversation_id="conv-h", role="assistant", content="",
            created_at=BASE_TIME.replace(second=1),
            tool_calls=[{"id": "c1", "name": "get_current_time", "arguments": {}}],
        ))
        s.add(Message(
            id="hm3", conversation_id="conv-h", role="tool",
            content='{"local_time": "2026-09-20 14:25:00"}',
            created_at=BASE_TIME.replace(second=2), tool_call_id="c1",
        ))
        s.add(Message(id="hm4", conversation_id="conv-h", role="assistant", content="现在是下午两点半哦",
                      created_at=BASE_TIME.replace(second=3)))
        await s.commit()


async def _seed_group(factory):
    """Group with two members + one group conversation carrying a speaker."""
    async with factory() as s:
        s.add(CharacterProfile(id="char-a", name="小柔", personality="", role="companion", archetype="friend"))
        s.add(CharacterProfile(id="char-b", name="阿B", personality="", role="companion", archetype="friend"))
        s.add(Group(id="grp-1", name="周末火锅群"))
        s.add(GroupMember(group_id="grp-1", character_id="char-a", position=0))
        s.add(GroupMember(group_id="grp-1", character_id="char-b", position=1))
        s.add(Conversation(id="conv-g", character_id=None, group_id="grp-1"))
        s.add(UserProfile(character_id=None, user_name="小明"))
        s.add(Message(id="gm1", conversation_id="conv-g", role="user", content="你们怎么看",
                      created_at=BASE_TIME))
        s.add(Message(id="gm2", conversation_id="conv-g", role="assistant", content="我不同意",
                      speaker_id="char-b", created_at=BASE_TIME.replace(second=1)))
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
        await _seed(session_factory)
        async with session_factory() as s:
            s.add(Message(
                id="hm5", conversation_id="conv-h", role="assistant",
                content="我不同意", speaker_id="char-b",
                created_at=BASE_TIME.replace(second=4),
            ))
            await s.commit()

        r = await client.get("/api/conversations/conv-h/messages")
        msgs = r.json()
        by_id = {m["id"]: m for m in msgs}
        assert by_id["hm5"]["speaker_id"] == "char-b"


class TestGroupHistoryTranscript:
    """群聊 Phase 1: 群 AI 消息 → 该条消息的发言角色名（spec L24/L76）。"""

    @pytest.mark.asyncio
    async def test_group_message_speaks_as_its_member(self, client, session_factory):
        """speaker_id picks the speaking member, not the (absent) conversation character."""
        await _seed_group(session_factory)

        r = await client.get("/api/conversations/conv-g/messages")
        assert r.status_code == 200
        by_id = {m["id"]: m for m in r.json()}
        assert by_id["gm1"]["speaker"] == "小明"
        assert by_id["gm1"]["transcript"] == "2026/9/20 14:25 [小明]: 你们怎么看"
        assert by_id["gm2"]["speaker"] == "阿B"
        assert by_id["gm2"]["transcript"] == "2026/9/20 14:25 [阿B]: 我不同意"

    @pytest.mark.asyncio
    async def test_removed_member_keeps_their_name(self, client, session_factory):
        """成员被移出群后，他此前说的话仍显示自己的角色名，而不是 AI。

        speaker_id 是「这条消息是谁说的」的权威来源；成员资格是可变的
        （「编辑群」是覆盖式管理操作），所以说话人按消息的 speaker_id 解析，
        而不是按当前成员列表。
        """
        await _seed_group(session_factory)
        async with session_factory() as s:
            # 「编辑群」把 char-b 移出成员列表——他那条 gm2 仍在历史里
            await s.execute(delete(GroupMember).where(GroupMember.character_id == "char-b"))
            await s.commit()

        r = await client.get("/api/conversations/conv-g/messages")
        assert r.status_code == 200
        by_id = {m["id"]: m for m in r.json()}
        assert by_id["gm2"]["speaker"] == "阿B"
        assert by_id["gm2"]["transcript"] == "2026/9/20 14:25 [阿B]: 我不同意"

    @pytest.mark.asyncio
    async def test_group_history_never_looks_up_a_null_primary_key(self, client, session_factory):
        """群对话没有单一角色：不得拿 character_id=None 去查角色。

        SQLAlchemy 会为此发 `SAWarning: fully NULL primary key identity…`
        并预告将来报错——所以这里把"没有该警告"锁成行为。
        """
        await _seed_group(session_factory)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            r = await client.get("/api/conversations/conv-g/messages")

        assert r.status_code == 200
        null_pk = [str(w.message) for w in caught if "NULL primary key" in str(w.message)]
        assert null_pk == []


class TestAssistantReplyPrefixIsStripped:
    """助手消息落库前剥掉行首的对话记录前缀（2026-09-27 群聊实测的泄漏）。

    模型偶尔把喂给它的记录格式当输出写出来（`2026/9/27 21:19 [小樱]: …`）。
    `ConversationManager.add_message` 是所有助手写入的唯一入口，所以在这里收口。
    """

    @pytest.mark.asyncio
    async def test_add_message_strips_the_prefix_for_assistant(self, client, session_factory):
        from core.conversation_manager import ConversationManager

        await _seed(session_factory)
        mgr = ConversationManager()
        async with session_factory() as s:
            await mgr.add_message(
                s, "conv-h", "assistant",
                "2026/9/27 21:19 [小柔]: 话说今天合肥有34度",
            )
            await s.commit()

        rows = (await client.get("/api/conversations/conv-h/messages")).json()
        assert rows[-1]["content"] == "话说今天合肥有34度"
        assert rows[-1]["transcript"].endswith("[小柔]: 话说今天合肥有34度")

    @pytest.mark.asyncio
    async def test_user_messages_are_left_alone(self, client, session_factory):
        from core.conversation_manager import ConversationManager

        await _seed(session_factory)
        mgr = ConversationManager()
        async with session_factory() as s:
            await mgr.add_message(s, "conv-h", "user", "[小柔]: 这是我打的字")
            await s.commit()

        rows = (await client.get("/api/conversations/conv-h/messages")).json()
        assert rows[-1]["content"] == "[小柔]: 这是我打的字"


class TestSpeakerIdMigration:
    @pytest.mark.asyncio
    async def test_sequence_adds_nullable_speaker_id_preserving_data(self):
        """Old DB without messages.speaker_id → the authoritative sequence adds it
        (nullable, existing rows intact)."""
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Simulate a pre-ticket DB: rebuild messages without speaker_id
            # (SQLite cannot DROP a FOREIGN KEY column).
            await conn.execute(text("DROP TABLE messages"))
            await conn.execute(text(OLD_MESSAGES_DDL))
            # Seed a row the old way (no speaker_id).
            await conn.execute(text(
                "INSERT INTO messages (id, conversation_id, role, content) "
                "VALUES ('old-1', 'conv-x', 'user', '旧数据')"
            ))

        from database import run_migration_sequence
        async with engine.begin() as conn:
            await run_migration_sequence(conn)

        async with engine.begin() as conn:
            info = await conn.execute(text("PRAGMA table_info(messages)"))
            assert "speaker_id" in {row[1] for row in info.fetchall()}
            rows = (await conn.execute(text("SELECT id, speaker_id FROM messages"))).fetchall()
            assert rows == [("old-1", None)]  # nullable + data preserved
        await engine.dispose()
