"""
Seam 3 — Agent.run() loads UserProfile and passes it to build_system_prompt.

Verifies the full integration: Agent → DB load → PromptManager → system prompt.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from services.memory_service import memory_service as memory_svc


# ── Fake LLM that captures the system prompt ─────────────────────────────


class CapturingLLM:
    """Records the messages passed to stream_chat for assertion."""

    def __init__(self, response_text: str = "你好！"):
        self._response = response_text
        self.messages: list[dict] = []

    async def stream_chat(self, messages, tools=None):
        self.messages = messages
        yield {"type": "token", "content": self._response}


# ── Helpers ──────────────────────────────────────────────────────────────


async def _fake_memory_search(session, query="", top_k=3, character_id=None):
    return []


async def _seed_db(session, *, char_id="char-1", conv_id="conv-1", profile=None):
    """Seed minimal test data: character, conversation, config, optional UserProfile."""
    from models.character import CharacterProfile
    from models.conversation import Conversation
    from models.user_config import UserConfig

    char = CharacterProfile(
        id=char_id, name="测试角色", personality="开朗",
        role="companion", archetype="friend",
    )
    conv = Conversation(id=conv_id, character_id=char_id)
    cfg = UserConfig(id=1)
    session.add_all([char, conv, cfg])
    if profile:
        session.add(profile)
    await session.commit()


# ── Tests ────────────────────────────────────────────────────────────────


class TestAgentLoadsUserProfile:
    """Agent.run() loads the UserProfile and injects it via build_system_prompt."""

    @pytest.mark.asyncio
    async def test_profile_injected_into_system_prompt(self, monkeypatch):
        """When a UserProfile exists for the character, the system prompt
        contains key-value profile fields."""
        from models.user_profile import UserProfile
        from core.agent import Agent
        from core.tool_registry import ToolRegistry

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            profile = UserProfile(
                character_id="char-1",
                user_name="小明",
                user_gender="男",
                user_occupation="大学生",
                user_bio="喜欢 Rust 和数学。",
                user_relationship="朋友",
            )
            await _seed_db(session, profile=profile)
            monkeypatch.setattr(memory_svc, "search", _fake_memory_search)

            llm = CapturingLLM("你好小明！")
            agent = Agent(llm_service=llm, tool_registry=ToolRegistry())

            async for _ in agent.run(
                session=session,
                user_message="你好",
                conversation_id="conv-1",
                character_id="char-1",
            ):
                pass

            system_prompt = llm.messages[0]["content"]
            assert "- Name: 小明" in system_prompt
            assert "- Gender: 男" in system_prompt
            assert "- Identity: 大学生" in system_prompt
            assert "- Relationship: 朋友" in system_prompt
            assert "喜欢 Rust 和数学。" in system_prompt
            # Old format must NOT appear
            assert "The user you're talking to is named" not in system_prompt

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_fallback_to_default_profile(self, monkeypatch):
        """When no character-specific profile exists, the default
        (character_id=NULL) profile is used."""
        from models.user_profile import UserProfile
        from core.agent import Agent
        from core.tool_registry import ToolRegistry

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            # Only a default profile, NOT character-specific
            default = UserProfile(
                character_id=None,
                user_name="DefaultName",
                user_relationship="buddy",
            )
            await _seed_db(session, char_id="char-no-profile", conv_id="conv-fallback", profile=default)
            monkeypatch.setattr(memory_svc, "search", _fake_memory_search)

            llm = CapturingLLM("你好！")
            agent = Agent(llm_service=llm, tool_registry=ToolRegistry())

            async for _ in agent.run(
                session=session,
                user_message="你好",
                conversation_id="conv-fallback",
                character_id="char-no-profile",
            ):
                pass

            system_prompt = llm.messages[0]["content"]
            assert "- Name: DefaultName" in system_prompt
            assert "- Relationship: buddy" in system_prompt

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_no_profile_uses_legacy_format(self, monkeypatch):
        """When no UserProfile exists at all, the old flat format is used."""
        from core.agent import Agent
        from core.tool_registry import ToolRegistry

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            await _seed_db(session, char_id="char-no-prof", conv_id="conv-no-prof")
            monkeypatch.setattr(memory_svc, "search", _fake_memory_search)

            llm = CapturingLLM("你好！")
            agent = Agent(llm_service=llm, tool_registry=ToolRegistry())

            async for _ in agent.run(
                session=session,
                user_message="你好",
                conversation_id="conv-no-prof",
                character_id="char-no-prof",
            ):
                pass

            system_prompt = llm.messages[0]["content"]
            # Falls back to old format (default user_name="User")
            assert "The user you're talking to is named User" in system_prompt
            assert "- Name:" not in system_prompt

        await engine.dispose()
