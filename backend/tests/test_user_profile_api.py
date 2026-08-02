"""
Seam 2 — UserProfile API CRUD and fallback logic.

Tests GET/PUT /api/user-profile endpoints with in-memory SQLite,
following the FastAPI TestClient pattern.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base, get_session
from models import CharacterProfile, UserProfile


@pytest.fixture
async def engine():
    """In-memory SQLite engine shared across the test session."""
    e = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
async def session_factory(engine):
    """Session factory bound to the in-memory engine."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def client(engine, session_factory):
    """FastAPI TestClient that uses the in-memory DB."""
    from main import app

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def seed_char(session_factory):
    """Seed a single character so CASCADE FK works."""
    async with session_factory() as s:
        char = CharacterProfile(
            name="测试角色",
            personality="测试",
            role="companion",
            archetype="friend",
        )
        s.add(char)
        await s.commit()
        await s.refresh(char)
        return char.id


@pytest.fixture
async def seed_default_profile(session_factory):
    """Seed the default (character_id=NULL) profile."""
    async with session_factory() as s:
        profile = UserProfile(
            character_id=None,
            user_name="DefaultUser",
            user_relationship="friend",
        )
        s.add(profile)
        await s.commit()
        await s.refresh(profile)
        return profile.id


class TestGetProfile:
    async def test_get_default_profile(self, client, seed_default_profile):
        """GET without character_id returns the default profile."""
        r = await client.get("/api/user-profile")
        assert r.status_code == 200
        data = r.json()
        assert data["user_name"] == "DefaultUser"
        assert data["character_id"] is None

    async def test_get_profile_for_character(self, client, seed_char, seed_default_profile):
        """GET with character_id that has no profile falls back to default."""
        r = await client.get(f"/api/user-profile?character_id={seed_char}")
        assert r.status_code == 200
        data = r.json()
        # Falls back to default
        assert data["user_name"] == "DefaultUser"
        assert data["character_id"] is None

    async def test_no_profile_returns_404(self, client):
        """GET with no profiles at all returns 404."""
        r = await client.get("/api/user-profile")
        assert r.status_code == 404


class TestPutProfile:
    async def test_create_new_profile(self, client, seed_char, seed_default_profile):
        """PUT creates a new profile for a character."""
        r = await client.put(
            f"/api/user-profile?character_id={seed_char}",
            json={
                "user_name": "小明",
                "user_gender": "男",
                "user_occupation": "大学生",
                "user_bio": "喜欢 Rust",
                "user_relationship": "朋友",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["user_name"] == "小明"
        assert data["user_gender"] == "男"
        assert data["user_occupation"] == "大学生"
        assert data["user_bio"] == "喜欢 Rust"
        assert data["character_id"] == seed_char

    async def test_update_existing_profile(self, client, seed_char):
        """PUT on existing profile updates it."""
        # Create first
        await client.put(
            f"/api/user-profile?character_id={seed_char}",
            json={"user_name": "小明", "user_relationship": "朋友"},
        )
        # Update
        r = await client.put(
            f"/api/user-profile?character_id={seed_char}",
            json={
                "user_name": "大明",
                "user_gender": "男",
                "user_occupation": "打工人",
                "user_bio": "",
                "user_relationship": "助手和用户",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["user_name"] == "大明"
        assert data["user_occupation"] == "打工人"
        assert data["user_relationship"] == "助手和用户"

    async def test_put_updates_default_profile(self, client, seed_default_profile):
        """PUT on existing default profile updates it."""
        r = await client.put(
            "/api/user-profile",
            json={"user_name": "NewDefault", "user_relationship": "friend"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["user_name"] == "NewDefault"

        # Verify with GET
        r2 = await client.get("/api/user-profile")
        assert r2.json()["user_name"] == "NewDefault"
