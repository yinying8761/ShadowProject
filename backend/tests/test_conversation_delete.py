"""删除会话后，记忆保留但不再指向它（审查 §B2.5 / ADR-0005）。

会话删除一直走 ORM 的 "all, delete-orphan"（消息确实跟着走，审查 §B2.2 证伪了
"删会话没删干净"）；问题在 `memories.source_conversation_id` —— 模型声明的是
`ON DELETE SET NULL`，而 SQLite 从不强制 FK，于是留下悬空引用。现在由服务层显式置空。
"""

from sqlalchemy import select

from models.character import CharacterProfile
from models.conversation import Conversation
from models.memory import Memory
from models.message import Message


async def test_delete_conversation_detaches_memories_instead_of_orphaning_them(client, session_factory):
    async with session_factory() as s:
        s.add(CharacterProfile(id="c1", name="小柔"))
        s.add(Conversation(id="conv-1", character_id="c1"))
        s.add(Message(id="m-1", conversation_id="conv-1", role="user", content="你好"))
        s.add(Memory(id="mem-1", content="一条事实", character_id="c1", source_conversation_id="conv-1"))
        await s.commit()

    assert (await client.delete("/api/conversations/conv-1")).status_code == 200

    async with session_factory() as s:
        assert await s.get(Conversation, "conv-1") is None
        assert await s.get(Message, "m-1") is None
        mem = await s.get(Memory, "mem-1")
        assert mem is not None and mem.character_id == "c1"
        assert mem.source_conversation_id is None


async def test_other_conversations_memories_are_untouched(client, session_factory):
    async with session_factory() as s:
        s.add(CharacterProfile(id="c1", name="小柔"))
        s.add(Conversation(id="conv-1", character_id="c1"))
        s.add(Conversation(id="conv-2", character_id="c1"))
        s.add(Memory(id="mem-1", content="来自 conv-1", character_id="c1", source_conversation_id="conv-1"))
        s.add(Memory(id="mem-2", content="来自 conv-2", character_id="c1", source_conversation_id="conv-2"))
        await s.commit()

    await client.delete("/api/conversations/conv-1")

    async with session_factory() as s:
        kept = await s.get(Memory, "mem-2")
        assert kept is not None and kept.source_conversation_id == "conv-2"
