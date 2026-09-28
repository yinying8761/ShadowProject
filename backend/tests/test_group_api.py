"""群实体 API（ticket #52, spec S5：TestClient + 内存 SQLite）。

Seam: REST /api/groups —— 建群（名称+成员顺序）、编辑群（改名/增删成员/调序，
替换式管理操作）、群列表与详情（含成员与对话概要）、为群创建对话
（一群多条、归属 group_id、character_id NULL）、删群（只删这个群的内容）。
错误语义与现有 REST 一致：对象不存在 → 404；非法输入 → 400 带明确 detail。

Fixtures `engine` / `session_factory` / `client` live in `conftest.py`.
"""

import pytest
from sqlalchemy import select

from models.character import CharacterProfile
from models.conversation import Conversation
from models.group import Group, GroupMember
from models.memory import Memory
from models.message import Message


async def _seed_characters(session_factory):
    async with session_factory() as s:
        s.add(CharacterProfile(id="c1", name="小柔", personality="", role="companion", archetype="friend"))
        s.add(CharacterProfile(id="c2", name="阿B", personality="", role="companion", archetype="friend"))
        s.add(CharacterProfile(id="c3", name="老王", personality="", role="companion", archetype="friend"))
        await s.commit()


class TestCreateGroup:
    @pytest.mark.asyncio
    async def test_create_roundtrip_with_ordered_members(self, client, session_factory):
        """建群往返：成员按给定顺序派生 position（0 起），带角色名返回。"""
        await _seed_characters(session_factory)

        r = await client.post("/api/groups", json={
            "name": "周末火锅群",
            "members": [{"character_id": "c2"}, {"character_id": "c1"}],
        })
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "周末火锅群"
        assert [(m["character_id"], m["character_name"], m["position"]) for m in data["members"]] == [
            ("c2", "阿B", 0),
            ("c1", "小柔", 1),
        ]
        assert data["conversations"] == []

        # 落库验证
        async with session_factory() as s:
            members = (await s.execute(
                select(GroupMember).where(GroupMember.group_id == data["id"]).order_by(GroupMember.position)
            )).scalars().all()
            assert [m.character_id for m in members] == ["c2", "c1"]

    @pytest.mark.asyncio
    async def test_rejects_empty_name(self, client, session_factory):
        await _seed_characters(session_factory)
        r = await client.post("/api/groups", json={"name": "   ", "members": [{"character_id": "c1"}]})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_no_members(self, client, session_factory):
        """群成员至少 1 个（否则无法发言）。"""
        await _seed_characters(session_factory)
        r = await client.post("/api/groups", json={"name": "空群", "members": []})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_duplicate_member(self, client, session_factory):
        await _seed_characters(session_factory)
        r = await client.post("/api/groups", json={
            "name": "群", "members": [{"character_id": "c1"}, {"character_id": "c1"}],
        })
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_overlong_name(self, client, session_factory):
        """Group.name 是 String(100)——超长要在 API 层给出明确 400。"""
        await _seed_characters(session_factory)
        r = await client.post("/api/groups", json={
            "name": "长" * 101, "members": [{"character_id": "c1"}],
        })
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_unknown_character(self, client, session_factory):
        await _seed_characters(session_factory)
        r = await client.post("/api/groups", json={
            "name": "群", "members": [{"character_id": "ghost"}],
        })
        assert r.status_code == 400
        assert "ghost" in r.json()["detail"]


class TestListAndGetGroups:
    @pytest.mark.asyncio
    async def test_list_contains_members_and_conversation_summary(self, client, session_factory):
        await _seed_characters(session_factory)
        async with session_factory() as s:
            s.add(Group(id="g1", name="群一"))
            s.add(GroupMember(group_id="g1", character_id="c1", position=0))
            s.add(Conversation(id="conv-g1a", character_id=None, group_id="g1", title="第一段"))
            s.add(Conversation(id="conv-1to1", character_id="c1", title="1:1 会话"))
            await s.commit()

        r = await client.get("/api/groups")
        assert r.status_code == 200
        groups = r.json()
        assert len(groups) == 1  # 1:1 会话不属于任何群
        g = groups[0]
        assert g["id"] == "g1"
        assert g["members"] == [{"character_id": "c1", "character_name": "小柔", "position": 0}]
        assert [c["id"] for c in g["conversations"]] == ["conv-g1a"]

    @pytest.mark.asyncio
    async def test_list_keeps_each_groups_own_members_and_conversations(self, client, session_factory):
        """多个群一次列出：成员与对话按 group_id 归属，不串群（批量查询正确性）。"""
        await _seed_characters(session_factory)
        async with session_factory() as s:
            s.add(Group(id="g1", name="群一"))
            s.add(Group(id="g2", name="群二"))
            s.add(GroupMember(group_id="g1", character_id="c1", position=0))
            s.add(GroupMember(group_id="g2", character_id="c2", position=0))
            s.add(GroupMember(group_id="g2", character_id="c3", position=1))
            s.add(Conversation(id="conv-g1", character_id=None, group_id="g1"))
            s.add(Conversation(id="conv-g2", character_id=None, group_id="g2"))
            await s.commit()

        groups = {g["id"]: g for g in (await client.get("/api/groups")).json()}
        assert [m["character_id"] for m in groups["g1"]["members"]] == ["c1"]
        assert [m["character_id"] for m in groups["g2"]["members"]] == ["c2", "c3"]
        assert [c["id"] for c in groups["g1"]["conversations"]] == ["conv-g1"]
        assert [c["id"] for c in groups["g2"]["conversations"]] == ["conv-g2"]

    @pytest.mark.asyncio
    async def test_get_group_detail(self, client, session_factory):
        await _seed_characters(session_factory)
        async with session_factory() as s:
            s.add(Group(id="g1", name="群一"))
            s.add(GroupMember(group_id="g1", character_id="c2", position=0))
            await s.commit()

        r = await client.get("/api/groups/g1")
        assert r.status_code == 200
        assert r.json()["name"] == "群一"

    @pytest.mark.asyncio
    async def test_get_unknown_group_404(self, client):
        r = await client.get("/api/groups/nope")
        assert r.status_code == 404


class TestUpdateGroup:
    async def _make_group(self, session_factory, group_id="g1"):
        await _seed_characters(session_factory)
        async with session_factory() as s:
            s.add(Group(id=group_id, name="旧群名"))
            s.add(GroupMember(group_id=group_id, character_id="c1", position=0))
            await s.commit()

    @pytest.mark.asyncio
    async def test_rename_keeps_members(self, client, session_factory):
        await self._make_group(session_factory)
        r = await client.put("/api/groups/g1", json={"name": "新群名"})
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "新群名"
        assert [m["character_id"] for m in data["members"]] == ["c1"]

    @pytest.mark.asyncio
    async def test_replace_members_adds_removes_reorders(self, client, session_factory):
        """替换式编辑：一次 PUT 覆盖增、删、调序（管理操作）。"""
        await self._make_group(session_factory)
        r = await client.put("/api/groups/g1", json={
            "members": [{"character_id": "c3"}, {"character_id": "c2"}, {"character_id": "c1"}],
        })
        assert r.status_code == 200
        assert [(m["character_id"], m["position"]) for m in r.json()["members"]] == [
            ("c3", 0), ("c2", 1), ("c1", 2),
        ]

    @pytest.mark.asyncio
    async def test_replace_members_rejects_empty(self, client, session_factory):
        await self._make_group(session_factory)
        r = await client.put("/api/groups/g1", json={"members": []})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_rename_rejects_blank(self, client, session_factory):
        await self._make_group(session_factory)
        r = await client.put("/api/groups/g1", json={"name": "  "})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_rename_rejects_overlong_name(self, client, session_factory):
        """Group.name 是 String(100)——超长要在 API 层给出明确 400。"""
        await self._make_group(session_factory)
        r = await client.put("/api/groups/g1", json={"name": "长" * 101})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_update_unknown_group_404(self, client):
        r = await client.put("/api/groups/nope", json={"name": "x"})
        assert r.status_code == 404


class TestGroupConversations:
    @pytest.mark.asyncio
    async def test_create_conversation_belongs_to_group(self, client, session_factory):
        """为群创建对话：character_id NULL、group_id 指向群；一群可开多条。"""
        await _seed_characters(session_factory)
        async with session_factory() as s:
            s.add(Group(id="g1", name="群一"))
            await s.commit()

        r1 = await client.post("/api/groups/g1/conversations")
        assert r1.status_code == 200
        conv1 = r1.json()
        assert conv1["group_id"] == "g1" and conv1["character_id"] is None

        r2 = await client.post("/api/groups/g1/conversations")
        assert r2.status_code == 200
        assert r2.json()["id"] != conv1["id"]

        async with session_factory() as s:
            convs = (await s.execute(
                select(Conversation).where(Conversation.group_id == "g1")
            )).scalars().all()
            assert len(convs) == 2
            assert all(c.character_id is None for c in convs)

        # 群详情的对话概要能看到两条
        detail = (await client.get("/api/groups/g1")).json()
        assert sorted(c["id"] for c in detail["conversations"]) == sorted(
            [conv1["id"], r2.json()["id"]]
        )

    @pytest.mark.asyncio
    async def test_create_conversation_unknown_group_404(self, client):
        r = await client.post("/api/groups/nope/conversations")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_group_conversations_carry_the_default_title_flag(
        self, client, session_factory
    ):
        """群侧对话概要也带 `is_default_title`（审查 S6）：与 /api/conversations 同一判定。"""
        async with session_factory() as s:
            s.add(Group(id="g1", name="群一"))
            await s.commit()

        created = (await client.post("/api/groups/g1/conversations")).json()
        assert created["is_default_title"] is True  # 新建就是默认标题

        from models.conversation import Conversation

        async with session_factory() as s:
            conv = await s.get(Conversation, created["id"])
            conv.title = "用户起的名字"
            await s.commit()

        detail = (await client.get("/api/groups/g1")).json()
        assert [(c["title"], c["is_default_title"]) for c in detail["conversations"]] == [
            ("用户起的名字", False)
        ]


class TestDeleteGroup:
    """删群 = 删**这个群的内容**：群 + 成员资格 + 群对话 + 群消息。

    明确**不删**：角色档案、它们的 1:1 会话与消息、角色记忆（只解绑来源会话）。
    """

    async def _seed(self, session_factory):
        await _seed_characters(session_factory)
        async with session_factory() as s:
            s.add(Group(id="g1", name="要删的群"))
            s.add(GroupMember(group_id="g1", character_id="c1", position=0))
            s.add(GroupMember(group_id="g1", character_id="c2", position=1))
            # 该群的两段群对话 + 消息
            s.add(Conversation(id="conv-g1", character_id=None, group_id="g1"))
            s.add(Conversation(id="conv-g2", character_id=None, group_id="g1"))
            s.add(Message(id="gm1", conversation_id="conv-g1", role="user", content="群里第 1 句"))
            s.add(Message(id="gm2", conversation_id="conv-g1", role="assistant",
                          content="群里回一句", speaker_id="c1"))
            s.add(Message(id="gm3", conversation_id="conv-g2", role="user", content="群里第 2 句"))
            # 成员的 1:1 会话 + 消息（必须原样留着）
            s.add(Conversation(id="conv-1to1", character_id="c1", title="1:1 会话"))
            s.add(Message(id="m1", conversation_id="conv-1to1", role="user", content="单聊第 1 句"))
            s.add(Message(id="m2", conversation_id="conv-1to1", role="assistant",
                          content="单聊回一句", speaker_id="c1"))
            # 记忆：一条来自群对话、一条来自 1:1（前者只解绑，后者不许动）
            s.add(Memory(id="mem-g", content="从群里提取的事实", character_id="c1",
                         source_conversation_id="conv-g1"))
            s.add(Memory(id="mem-1to1", content="从单聊提取的事实", character_id="c1",
                         source_conversation_id="conv-1to1"))
            await s.commit()

    @pytest.mark.asyncio
    async def test_delete_removes_the_group_content(self, client, session_factory):
        await self._seed(session_factory)

        r = await client.delete("/api/groups/g1")

        assert r.status_code == 200
        assert r.json() == {
            "status": "deleted",
            "conversations_deleted": 2,
            "messages_deleted": 3,
        }
        assert (await client.get("/api/groups/g1")).status_code == 404
        assert (await client.get("/api/groups")).json() == []
        # 群对话没了（消息接口按会话查，空 = 消息确实删了）
        assert (await client.get("/api/conversations/conv-g1/messages")).json() == []
        assert (await client.get("/api/conversations/conv-g2/messages")).json() == []

        async with session_factory() as s:
            assert (await s.execute(select(Conversation))).scalars().all()  # 只剩 1:1
            assert [c.id for c in (await s.execute(select(Conversation))).scalars().all()] == [
                "conv-1to1"
            ]
            assert (await s.execute(select(GroupMember))).scalars().all() == []
            assert (await s.execute(select(Message))).scalars().all()  # 群消息全清
            left = [m.id for m in (await s.execute(select(Message))).scalars().all()]
            assert left == ["m1", "m2"]

    @pytest.mark.asyncio
    async def test_characters_and_one_to_one_are_untouched(self, client, session_factory):
        """删群不许牵连角色与 1:1 会话 —— 这是这个端点的红线。"""
        await self._seed(session_factory)

        await client.delete("/api/groups/g1")

        chars = (await client.get("/api/characters")).json()
        assert sorted(c["id"] for c in chars) == ["c1", "c2", "c3"]
        convs = (await client.get("/api/conversations?character_id=c1")).json()
        assert [c["id"] for c in convs] == ["conv-1to1"]
        msgs = (await client.get("/api/conversations/conv-1to1/messages")).json()
        assert [(m["id"], m["content"]) for m in msgs] == [
            ("m1", "单聊第 1 句"),
            ("m2", "单聊回一句"),
        ]
        assert msgs[1]["speaker_id"] == "c1"  # 1:1 的 speaker_id 也不受影响

    @pytest.mark.asyncio
    async def test_memories_survive_with_the_source_unbound(self, client, session_factory):
        """记忆属于角色，不属于某段群聊：留着，只把来源会话解绑。"""
        await self._seed(session_factory)

        await client.delete("/api/groups/g1")

        async with session_factory() as s:
            mem_g = await s.get(Memory, "mem-g")
            mem_1to1 = await s.get(Memory, "mem-1to1")
            assert mem_g is not None and mem_g.source_conversation_id is None
            assert mem_1to1 is not None and mem_1to1.source_conversation_id == "conv-1to1"

    @pytest.mark.asyncio
    async def test_unknown_group_404(self, client):
        r = await client.delete("/api/groups/nope")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_deleting_an_empty_group_is_fine(self, client, session_factory):
        """没有对话的群也能删（计数为 0，不报错）。"""
        await _seed_characters(session_factory)
        async with session_factory() as s:
            s.add(Group(id="g-empty", name="空群"))
            s.add(GroupMember(group_id="g-empty", character_id="c1", position=0))
            await s.commit()

        r = await client.delete("/api/groups/g-empty")

        assert r.status_code == 200
        assert r.json()["conversations_deleted"] == 0
        assert (await client.get("/api/groups/g-empty")).status_code == 404
