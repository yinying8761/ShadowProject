"""群实体 API（ticket #52, spec S5：TestClient + 内存 SQLite）。

Seam: REST /api/groups —— 建群（名称+成员顺序）、编辑群（改名/增删成员/调序，
替换式管理操作）、群列表与详情（含成员与对话概要）、为群创建对话
（一群多条、归属 group_id、character_id NULL）。错误语义与现有 REST 一致：
对象不存在 → 404；非法输入 → 400 带明确 detail。
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base, get_session
from models.character import CharacterProfile
from models.conversation import Conversation
from models.group import Group, GroupMember


@pytest.fixture
async def engine():
    e = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def client(engine, session_factory):
    from main import app

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


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
