"""
Tests for Agent — tool registry injection seam and tool interaction.

Covers: tool dispatch, approval gating, error handling, empty registry,
and default fallback to the module-level singleton.
"""

import pytest

from core.tool_registry import ToolRegistry
from core.tool_runtime import ToolRuntime
from core.agent import Agent


# ── Helper ────────────────────────────────────────────────────────────────

def _wrap(registry: ToolRegistry) -> ToolRuntime:
    """Wrap a ToolRegistry in ToolRuntime with tracing/sandbox disabled for tests."""
    return ToolRuntime(registry=registry, enable_tracing=False, enable_sandbox=False)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_registry():
    """Isolated ToolRegistry with fake tools for testing."""
    reg = ToolRegistry()

    async def fake_echo(**kwargs) -> str:
        return f"echo: {kwargs}"

    async def fake_approval_tool(**kwargs) -> str:
        return f"approved action: {kwargs}"

    async def fake_failing(**kwargs) -> str:
        raise RuntimeError("simulated tool failure")

    reg.register(
        name="fake_echo",
        description="echo back arguments",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=fake_echo,
        require_approval=False,
    )
    reg.register(
        name="fake_approval_tool",
        description="needs user approval",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=fake_approval_tool,
        require_approval=True,
    )
    reg.register(
        name="fake_failing",
        description="always throws",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=fake_failing,
        require_approval=False,
    )
    return reg


@pytest.fixture
def agent(fake_registry):
    """Agent with an injected fake tool registry, wrapped in ToolRuntime."""
    return Agent(tool_registry=_wrap(fake_registry))


# ── Tool dispatch ─────────────────────────────────────────────────────────


class TestToolDispatch:
    """Tool execution via _execute_tools_with_approval."""

    @pytest.mark.asyncio
    async def test_dispatches_to_registry(self, agent):
        """A tool_use block is dispatched and the correct tool_result is yielded."""
        blocks = [{"id": "call_1", "name": "fake_echo", "arguments": {"msg": "hi"}}]

        results = await agent._execute_tools_with_approval(blocks, None)

        assert len(results) == 1
        event, tool_msg = results[0]
        assert event["type"] == "tool_result"
        assert event["name"] == "fake_echo"
        assert event["is_error"] is False
        assert "echo:" in event["result"]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_1"

    @pytest.mark.asyncio
    async def test_multiple_tools_in_one_round(self, agent):
        """All tool_use blocks in a round are executed."""
        blocks = [
            {"id": "c1", "name": "fake_echo", "arguments": {"a": 1}},
            {"id": "c2", "name": "fake_echo", "arguments": {"b": 2}},
        ]

        results = await agent._execute_tools_with_approval(blocks, None)

        assert len(results) == 2
        assert results[0][0]["name"] == "fake_echo"
        assert results[1][0]["name"] == "fake_echo"

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_json(self, agent):
        """Dispatch to an unregistered tool yields is_error=True with the error message."""
        blocks = [{"id": "c1", "name": "nonexistent", "arguments": {}}]

        results = await agent._execute_tools_with_approval(blocks, None)

        assert len(results) == 1
        event, _ = results[0]
        assert event["is_error"] is True
        assert "Unknown tool" in event["result"]

    @pytest.mark.asyncio
    async def test_handler_exception_yields_error_event(self, agent):
        """When a tool handler raises, dispatch returns JSON error → is_error=True."""
        blocks = [{"id": "c1", "name": "fake_failing", "arguments": {}}]

        results = await agent._execute_tools_with_approval(blocks, None)

        assert len(results) == 1
        event, _ = results[0]
        assert event["is_error"] is True
        assert "simulated tool failure" in event["result"]


# ── Approval gating ───────────────────────────────────────────────────────


class TestApproval:
    """Approval callback integration with tool execution."""

    @pytest.mark.asyncio
    async def test_approved_tool_executes_normally(self, agent):
        """When approval callback returns True, the tool runs."""
        blocks = [{"id": "c1", "name": "fake_approval_tool", "arguments": {"x": 1}}]

        async def approve(_name, _args):
            return True

        results = await agent._execute_tools_with_approval(blocks, approve)

        assert len(results) == 1
        event, _ = results[0]
        assert event["is_error"] is False
        assert "approved action:" in event["result"]
        assert "denied" not in event

    @pytest.mark.asyncio
    async def test_denied_tool_yields_denied_event(self, agent):
        """When approval callback returns False, tool is NOT executed."""
        blocks = [{"id": "c1", "name": "fake_approval_tool", "arguments": {"x": 1}}]

        async def deny(_name, _args):
            return False

        results = await agent._execute_tools_with_approval(blocks, deny)

        assert len(results) == 1
        event, _ = results[0]
        assert event["is_error"] is True
        assert event["denied"] is True
        assert event["result"] == "User denied this operation."

    @pytest.mark.asyncio
    async def test_mixed_approval_one_denied_one_allowed(self, agent):
        """In a mixed batch, denied tools skip execution while allowed ones run."""
        async def approve_only_second(name, _args):
            return name == "fake_echo"

        blocks = [
            {"id": "c1", "name": "fake_approval_tool", "arguments": {}},
            {"id": "c2", "name": "fake_echo", "arguments": {"msg": "ok"}},
        ]

        results = await agent._execute_tools_with_approval(blocks, approve_only_second)

        assert len(results) == 2
        # First: denied
        assert results[0][0]["denied"] is True
        # Second: executed
        assert results[1][0]["is_error"] is False
        assert "echo:" in results[1][0]["result"]


# ── Injection seam ────────────────────────────────────────────────────────


class TestRegistryInjection:
    """Verify the tool_registry injection seam works correctly."""

    def test_empty_registry_returns_empty_tool_definitions(self):
        """An Agent with an empty registry sees no tools."""
        agent = Agent(tool_registry=_wrap(ToolRegistry()))
        assert agent.tool_registry.get_tool_definitions() == []

    def test_injected_registry_is_used(self, fake_registry):
        """Agent uses the injected registry, not the module-level singleton."""
        agent = Agent(tool_registry=_wrap(fake_registry))
        tools = agent.tool_registry.get_tool_definitions()
        tool_names = {t["name"] for t in tools}
        assert tool_names == {"fake_echo", "fake_approval_tool", "fake_failing"}

    def test_default_fallback_uses_module_singleton(self):
        """When no registry is injected, Agent falls back to the module-level singleton."""
        agent = Agent()
        # The module-level default is now a ToolRuntime instance
        assert agent.tool_registry is not None
        assert isinstance(agent.tool_registry, ToolRuntime)
        # get_tool_definitions() works (may be empty if register_tools hasn't run)
        assert isinstance(agent.tool_registry.get_tool_definitions(), list)

    def test_injected_and_default_are_different_instances(self, fake_registry):
        """Injected Agent uses its own registry, default Agent uses the singleton."""
        injected_agent = Agent(tool_registry=_wrap(fake_registry))
        default_agent = Agent()

        # They are different objects
        assert injected_agent.tool_registry is not default_agent.tool_registry
        # The injected ToolRuntime wraps the fake_registry
        assert injected_agent.tool_registry._registry is fake_registry

    @pytest.mark.asyncio
    async def test_injected_registry_isolated_from_default(self, fake_registry):
        """Modifying the injected registry does not affect the default singleton."""
        injected_agent = Agent(tool_registry=_wrap(fake_registry))
        default_agent = Agent()

        # Dispatch via injected
        blocks = [{"id": "c1", "name": "fake_echo", "arguments": {"msg": "hi"}}]
        results = await injected_agent._execute_tools_with_approval(blocks, None)
        assert results[0][0]["is_error"] is False

        # Default agent doesn't know about fake_echo
        default_results = await default_agent._execute_tools_with_approval(blocks, None)
        assert "Unknown tool" in default_results[0][0]["result"]


# ── register_tools() ─────────────────────────────────────────────────────


class TestRegisterTools:
    """register_tools() with an isolated registry."""

    def test_registers_all_tools_on_isolated_registry(self):
        """Passing an isolated ToolRegistry registers all 10 tools on it."""
        from main import register_tools
        from core.tool_registry import ToolRegistry

        reg = ToolRegistry()
        result = register_tools(registry=reg)

        assert result is reg
        tools = reg.get_tool_definitions()
        tool_names = {t["name"] for t in tools}
        expected = {
            "read_file", "write_file", "list_directory", "search_files",
            "get_current_time", "see_screen",
            "fetch_url", "research", "search_memory", "save_memory",
        }
        assert tool_names == expected
        assert len(tools) == 10

    def test_isolated_registry_does_not_affect_singleton(self):
        """register_tools(isolated) leaves the module-level singleton untouched."""
        from main import register_tools
        from core.tool_registry import ToolRegistry, tool_registry as singleton

        original_count = len(singleton.get_tool_definitions())
        reg = ToolRegistry()
        register_tools(registry=reg)

        # Isolated registry has 10 tools
        assert len(reg.get_tool_definitions()) == 10
        # Singleton is unchanged
        assert len(singleton.get_tool_definitions()) == original_count


# ── Fake LLM Service ─────────────────────────────────────────────────────


class FakeLLMService:
    """Yields a preset sequence of events from stream_chat().

    By default events are yielded once — the first call gets them,
    subsequent calls return nothing (mirrors how a real LLM stops
    returning tool calls after seeing tool results).
    Pass ``repeat=True`` for stateless replay.
    """

    def __init__(self, events: list[dict], repeat: bool = False):
        self._events = events
        self._repeat = repeat
        self._called = False

    async def stream_chat(self, messages, tools=None):
        if self._called and not self._repeat:
            return
        self._called = True
        for event in self._events:
            yield event


# ── In-memory DB helpers ────────────────────────────────────────────────


async def _seed_test_db(session):
    """Create minimal test data: character + conversation + user config."""
    from models.character import CharacterProfile
    from models.conversation import Conversation
    from models.user_config import UserConfig

    char = CharacterProfile(
        id="char-test-1",
        name="测试角色",
        personality="开朗",
        role="companion",
        archetype="friend",
    )
    conv = Conversation(id="conv-test-1", character_id="char-test-1")
    cfg = UserConfig(id=1)
    session.add_all([char, conv, cfg])
    await session.commit()


async def _fake_memory_search(session, query="", top_k=3, character_id=None):
    """Stub that returns an empty list — avoids FTS5 table dependency."""
    return []


# ── Agent.run() integration tests ────────────────────────────────────────


class TestAgentRunWithInjectedRegistry:
    """End-to-end tool interaction through the public Agent.run() API."""

    @pytest.mark.asyncio
    async def test_run_dispatches_tool_and_yields_tool_result(self, monkeypatch):
        """Agent.run() with FakeLLMService emitting tool_use — verifies the
        injected tool_registry is used through the full public API."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from database import Base
        from services.memory_service import memory_service as memory_svc

        # ── In-memory DB ────────────────────────────────────────────
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            from models.character import CharacterProfile
            from models.conversation import Conversation
            from models.user_config import UserConfig

            char = CharacterProfile(id="char-run-1", name="RunTest", personality="cool", role="companion", archetype="friend")
            conv = Conversation(id="conv-run-1", character_id="char-run-1")
            cfg = UserConfig(id=1)
            session.add_all([char, conv, cfg])
            await session.commit()

            monkeypatch.setattr(memory_svc, "search", _fake_memory_search)

            # Fake LLM: token → tool_use → token → done
            fake_llm = FakeLLMService([
                {"type": "token", "content": "好的，让我用工具"},
                {"type": "tool_use", "id": "call_run_1", "name": "fake_echo", "arguments": {"msg": "from_run"}},
                {"type": "token", "content": "返回结果了。"},
            ])

            # Build the needed fake_registry inline (same three tools)
            from core.tool_registry import ToolRegistry
            reg = ToolRegistry()
            async def _echo(**kw): return f"echo: {kw}"
            reg.register("fake_echo", "echo", {"type": "object", "properties": {}, "required": []}, _echo, False)

            agent = Agent(llm_service=fake_llm, tool_registry=_wrap(reg))

            events = []
            async for event in agent.run(
                session=session,
                user_message="帮我 echo 一下",
                conversation_id="conv-run-1",
                character_id="char-run-1",
            ):
                events.append(event)

            # ── Verify tool dispatch ────────────────────────────────
            tool_results = [e for e in events if e["type"] == "tool_result"]
            assert len(tool_results) == 1
            assert tool_results[0]["name"] == "fake_echo"
            assert tool_results[0]["is_error"] is False
            assert "echo:" in tool_results[0]["result"]

            # ── Verify full pipeline ────────────────────────────────
            event_types = [e["type"] for e in events]
            for t in ("token", "tool_use", "tool_result", "done"):
                assert t in event_types, f"expected '{t}' event in pipeline"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_run_with_empty_registry_has_no_tools(self, monkeypatch):
        """Agent.run() with empty ToolRegistry — no tools available."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from database import Base
        from services.memory_service import memory_service as memory_svc

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            from models.character import CharacterProfile
            from models.conversation import Conversation
            from models.user_config import UserConfig

            char = CharacterProfile(id="char-run-2", name="NoTools", personality="calm", role="companion", archetype="friend")
            conv = Conversation(id="conv-run-2", character_id="char-run-2")
            cfg = UserConfig(id=1)
            session.add_all([char, conv, cfg])
            await session.commit()

            monkeypatch.setattr(memory_svc, "search", _fake_memory_search)

            fake_llm = FakeLLMService([
                {"type": "token", "content": "你好！有什么可以帮你的？"},
            ])

            agent = Agent(
                llm_service=fake_llm,
                tool_registry=_wrap(ToolRegistry()),  # empty — no tools
            )

            events = []
            async for event in agent.run(
                session=session,
                user_message="你好",
                conversation_id="conv-run-2",
                character_id="char-run-2",
            ):
                events.append(event)

            tool_events = [e for e in events if e["type"] in ("tool_use", "tool_result")]
            assert len(tool_events) == 0
            assert events[-1]["type"] == "done"

        await engine.dispose()
