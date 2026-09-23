"""api/character.py：名字校验（审查 §B1.1）与删除级联（审查 §B2.4）。

两级：
- **名字必填**：`name: str` 只保证"字段在"，`""` / `"   "` 原本都能建出角色；
  而说话人取自 `CharacterProfile.name`，于是它的每句话都渲染成无主语的一行
  （兜底成 `[AI]`，见 `core/transcript.py`）。
- **删除级联**：SQLite 上 FK 强制从不开启、模型也不再声明 `ondelete=`（ADR-0005），
  所以"父行没了、子行还在"完全由 `_delete_character_dependents` 兜住。
"""

from sqlalchemy import select

from models.character import CharacterProfile
from models.conversation import Conversation
from models.group import Group, GroupMember
from models.memory import Memory
from models.message import Message
from models.user_profile import UserProfile


async def _ids(session_factory, model) -> list[str]:
    async with session_factory() as s:
        return sorted(r.id for r in (await s.execute(select(model))).scalars().all())


async def _seed(session_factory) -> None:
    """c1 / c2 各自的会话与消息，外加一个含两人的群与一条群对话。"""
    async with session_factory() as s:
        s.add(CharacterProfile(id="c1", name="小柔"))
        s.add(CharacterProfile(id="c2", name="阿B"))
        s.add(Conversation(id="conv-1", character_id="c1", title="c1 的对话"))
        s.add(Conversation(id="conv-2", character_id="c2", title="c2 的对话"))
        s.add(Message(id="m-1", conversation_id="conv-1", role="user", content="你好"))
        s.add(Message(id="m-2", conversation_id="conv-1", role="assistant", content="在", speaker_id="c1"))
        s.add(Message(id="m-3", conversation_id="conv-2", role="user", content="另一条"))
        s.add(Group(id="g1", name="小群"))
        s.add(GroupMember(id="gm-1", group_id="g1", character_id="c1", position=0))
        s.add(GroupMember(id="gm-2", group_id="g1", character_id="c2", position=1))
        s.add(Conversation(id="conv-g", character_id=None, group_id="g1"))
        # 群里 c1 说过的话：删 c1 后消息要留下，但说话人标识置空
        s.add(Message(id="m-g1", conversation_id="conv-g", role="assistant", content="我来说", speaker_id="c1"))
        s.add(Memory(id="mem-1", content="c1 的事实", character_id="c1"))
        s.add(Memory(id="mem-2", content="c2 的事实", character_id="c2"))
        s.add(Memory(id="mem-3", content="全局事实", character_id=None))
        # 跨角色引用：c2 的记忆从 c1 的会话里提取
        s.add(Memory(id="mem-4", content="从 conv-1 提取", character_id="c2", source_conversation_id="conv-1"))
        s.add(UserProfile(id="up-1", character_id="c1", user_name="甲"))
        s.add(UserProfile(id="up-global", character_id=None, user_name="乙"))
        await s.commit()


class TestCharacterNameValidation:
    async def test_blank_name_is_rejected_on_create(self, client):
        for blank in ("", "   ", "\t"):
            res = await client.post("/api/characters", json={"name": blank})
            assert res.status_code == 400, repr(blank)
            assert res.json()["detail"] == "Character name cannot be empty"
        assert (await client.get("/api/characters")).json() == []

    async def test_missing_name_is_still_a_schema_error(self, client):
        """回归：字段缺失仍是 422（校验只补"空白"，不改变必填语义）。"""
        assert (await client.post("/api/characters", json={})).status_code == 422

    async def test_name_is_trimmed_on_create(self, client):
        assert (await client.post("/api/characters", json={"name": "  小柔  "})).status_code == 200
        assert (await client.get("/api/characters")).json()[0]["name"] == "小柔"

    async def test_blank_name_is_rejected_on_update_and_keeps_the_old_one(self, client):
        await client.post("/api/characters", json={"name": "小柔"})
        char_id = (await client.get("/api/characters")).json()[0]["id"]

        res = await client.put(f"/api/characters/{char_id}", json={"name": "  "})
        assert res.status_code == 400
        assert (await client.get(f"/api/characters/{char_id}")).json()["name"] == "小柔"

    async def test_other_fields_still_update_without_a_name(self, client):
        """回归：exclude_none 的更新路径不被新校验顺带改掉。"""
        await client.post("/api/characters", json={"name": "小柔"})
        char_id = (await client.get("/api/characters")).json()[0]["id"]

        res = await client.put(f"/api/characters/{char_id}", json={"personality": "温柔"})
        assert res.status_code == 200
        assert (await client.get(f"/api/characters/{char_id}")).json()["personality"] == "温柔"


class TestCharacterDeleteCascade:
    async def test_delete_removes_only_what_belongs_to_that_character(self, client, session_factory):
        await _seed(session_factory)

        assert (await client.delete("/api/characters/c1")).status_code == 200

        assert await _ids(session_factory, CharacterProfile) == ["c2"]
        # 会话：只有 c1 名下的走掉；群对话属于群，留着
        assert await _ids(session_factory, Conversation) == ["conv-2", "conv-g"]
        assert await _ids(session_factory, Message) == ["m-3", "m-g1"]
        # 记忆：c1 的删掉，全局的与 c2 的（含从 c1 会话提取的那条）留着
        assert await _ids(session_factory, Memory) == ["mem-2", "mem-3", "mem-4"]
        # 群与成员资格：群保留（可再编辑加人），只少一个成员
        assert await _ids(session_factory, Group) == ["g1"]
        assert await _ids(session_factory, GroupMember) == ["gm-2"]
        # 用户画像：专属的删掉，全局回退的留着
        assert await _ids(session_factory, UserProfile) == ["up-global"]

    async def test_group_message_survives_without_its_speaker(self, client, session_factory):
        """群对话里的内容属于群，不随成员删除而消失；只说不出是谁说的。"""
        await _seed(session_factory)
        await client.delete("/api/characters/c1")

        async with session_factory() as s:
            msg = await s.get(Message, "m-g1")
            assert msg is not None and msg.content == "我来说"
            assert msg.speaker_id is None

    async def test_surviving_memory_no_longer_points_at_a_deleted_conversation(self, client, session_factory):
        """c2 的记忆从 c1 的会话提取 —— 删 c1 后内容留下、来源置空（不悬空）。"""
        await _seed(session_factory)
        await client.delete("/api/characters/c1")

        async with session_factory() as s:
            mem = await s.get(Memory, "mem-4")
            assert mem is not None and mem.character_id == "c2"
            assert mem.source_conversation_id is None

    async def test_unknown_character_is_still_404(self, client):
        assert (await client.delete("/api/characters/nope")).status_code == 404
