"""Shared pytest fixtures and constants for backend tests."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base, get_session

# Pre-group conversations shape (what create_all built before ADR-0004):
# character_id NOT NULL, no group_id / last_extract_at. Migration tests use it
# to simulate an old database (SQLite cannot DROP a NOT NULL / FK column, so
# the old table is rebuilt by hand rather than altered).
OLD_CONVERSATIONS_DDL = (
    "CREATE TABLE conversations ("
    " id VARCHAR(36) NOT NULL PRIMARY KEY,"
    " character_id VARCHAR(36) NOT NULL"
    "   REFERENCES character_profiles(id) ON DELETE CASCADE,"
    " title VARCHAR(200) NOT NULL,"
    " summary TEXT,"
    " created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,"
    " updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"
)

# Pre-group messages shape: no speaker_id (added additively by ticket #50).
OLD_MESSAGES_DDL = (
    "CREATE TABLE messages ("
    " id VARCHAR(36) PRIMARY KEY,"
    " conversation_id VARCHAR(36) NOT NULL"
    "   REFERENCES conversations(id) ON DELETE CASCADE,"
    " role VARCHAR(20) NOT NULL,"
    " content TEXT,"
    " tool_calls JSON,"
    " tool_call_id VARCHAR(100),"
    " token_count INTEGER,"
    " created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
)


@pytest.fixture
async def engine():
    """In-memory SQLite carrying the current schema (create_all)."""
    e = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def ws_env(tmp_path):
    """WS/TestClient 测试用的库：**文件库**，不是内存库。

    TestClient 在 portal 线程里跑自己的事件循环，测试代码又用自己的循环读写同一个
    库；内存库（`sqlite+aiosqlite://`）在这种多 loop 场景下会被重新创建，表现为
    `no such table`。文件库天然共享，且仍然是一次性的 tmp 路径。
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ws-test.db'}", echo=False)

    async def _create_all():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield engine, factory
    asyncio.run(engine.dispose())


@pytest.fixture
async def client(engine, session_factory):
    """ASGI client whose request sessions come from this test's engine."""
    from main import app

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
