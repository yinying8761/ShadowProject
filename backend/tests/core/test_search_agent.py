"""
Tests for Ticket 01 — SubAgent + SearchAgent + Router placeholder.

Covers:
- Seam 1: SearchAgent + FakeLLMService (tool loop, result format)
- Seam 3: Agent + SearchAgent handler (full pipeline)
- Seam 4: RouterAgent placeholder + _resolve_handler hook
- SubAgent base: tool loop behaviour
"""

import json

import pytest

from core.tool_registry import ToolRegistry
from core.sub_agent import SubAgent


# ── Fake LLM ────────────────────────────────────────────────────────────────


class FakeLLMService:
    """Yields a preset sequence of events for testing sub-agents.

    *events* can be:
    - A flat list of dicts → yielded on the first call, subsequent calls
      return empty (convenient for single-round LLMs).
    - A list of lists → each inner list is one round of tool-loop.
    """

    def __init__(self, events):
        self._events = events
        self._round = 0

    async def stream_chat(self, messages, tools=None, on_retry=None):
        # Determine which round to play
        if self._is_multi_round():
            if self._round >= len(self._events):
                return
            round_events = self._events[self._round]
        else:
            if self._round > 0:
                return
            round_events = self._events

        self._round += 1
        for event in round_events:
            yield event

    async def estimate_prompt_tokens(self, messages) -> int:
        """Stub for Workflow G — fake LLMs emit no usage events, so the
        Agent never persists; returning 0 keeps the loop well-defined."""
        return 0

    async def chat_sync(self, messages, model=None, max_tokens=1024, temperature=0.7):
        """Return a canned grounded summary (SearchAgent's summarise step)."""
        return "Rust 2024 edition 已发布，更多特性见官方博客。[confidence: high]"

    def _is_multi_round(self) -> bool:
        return (
            len(self._events) > 0
            and isinstance(self._events[0], list)
        )


class FakeSearchTool:
    """Fake search backend that returns canned results (list of dicts)."""

    def __init__(self, results: list[dict] | None = None):
        self._results = results or [
            {"title": "Test Result", "url": "https://example.com", "snippet": "A test result."},
        ]
        self.called = False

    async def __call__(self, query: str = "", limit: int = 5, **kwargs):
        self.called = True
        self.last_query = query
        return self._results


class FakeFetchTool:
    """Fake URL fetcher."""

    def __init__(self, content: str = "Fetched content"):
        self.content = content
        self.called = False

    async def __call__(self, **kwargs):
        self.called = True
        self.last_url = kwargs.get("url", "")
        return json.dumps({"url": self.last_url, "text": self.content})


# ── Seam 1: SearchAgent + FakeLLMService ───────────────────────────────────


class TestSearchAgent:
    """SearchAgent with fake LLM and tools."""

    @pytest.mark.asyncio
    async def test_search_agent_returns_structured_json(self):
        """SearchAgent.run() returns JSON with answer/sources/confidence."""
        from core.search_agent import SearchAgent

        fake_llm = FakeLLMService([
            {"type": "token", "content": "搜索结果显示 Python 3.14 发布了新特性。"},
            {"type": "token", "content": "[confidence: high]"},
        ])

        search_agent = SearchAgent(llm_service=fake_llm)
        result = await search_agent.run("Python 3.14")

        data = json.loads(result)
        assert "answer" in data
        assert "sources" in data
        assert "confidence" in data
        assert data["confidence"] == "high"
        assert "Python 3.14" in data["answer"]

    @pytest.mark.asyncio
    async def test_search_agent_calls_search_tool(self):
        """When the LLM emits a tool_use for 'search', it is dispatched."""
        from core.search_agent import SearchAgent

        # Patch _do_search before creating SearchAgent
        import tools.search_tools as st

        original = st._do_search
        fake_results = [
            {"title": "Rust Blog", "url": "https://blog.rust-lang.org", "snippet": "Rust 2024 edition"},
        ]
        st._do_search = FakeSearchTool(fake_results)

        try:
            # Multi-round: round 1 → tool call, round 2 → final answer
            fake_llm = FakeLLMService([
                [
                    {"type": "token", "content": "让我搜索一下。"},
                    {"type": "tool_use", "id": "c1", "name": "search",
                     "arguments": {"query": "Rust 2024 edition"}},
                ],
                [
                    {"type": "token", "content": "Rust 2024 edition 已发布。"},
                    {"type": "token", "content": "[confidence: high]"},
                ],
            ])

            agent = SearchAgent(llm_service=fake_llm)
            result = await agent.run("Rust 2024 edition")

            data = json.loads(result)
            assert data["confidence"] == "high"
            assert "Rust 2024" in data["answer"]
            assert st._do_search.called
        finally:
            st._do_search = original

    @pytest.mark.asyncio
    async def test_search_agent_no_results(self):
        """When the LLM returns nothing useful, confidence is low."""
        from core.search_agent import SearchAgent

        fake_llm = FakeLLMService([
            {"type": "token", "content": ""},  # empty response
        ])

        agent = SearchAgent(llm_service=fake_llm)
        result = await agent.run("xyzzy")

        data = json.loads(result)
        assert data["answer"] is None or data["answer"] == ""
        assert data["confidence"] == "low"


# ── SubAgent base class ─────────────────────────────────────────────────────


class TestSubAgent:
    """SubAgent base class — tool loop, registration."""

    @pytest.mark.asyncio
    async def test_tool_loop_exits_after_max_rounds(self):
        """When LLM keeps calling tools, the loop stops at max_tool_rounds."""

        async def echo(**kw) -> str:
            return f"echo: {kw}"

        # Each round: LLM calls a tool (5 rounds of events, but max=2)
        events_per_round = []
        for i in range(5):
            events_per_round.append([
                {"type": "token", "content": f"round{i}"},
                {"type": "tool_use", "id": f"c{i}", "name": "echo",
                 "arguments": {"msg": str(i)}},
            ])

        fake_llm = FakeLLMService(events_per_round)
        sub = SubAgent(llm_service=fake_llm, max_tool_rounds=2)
        sub.register_tool("echo", "echo", {"type": "object", "properties": {}}, echo)

        final_text, tool_outputs = await sub._run_tool_loop("system", "user")
        # Should stop after 2 rounds (index 0 and 1)
        assert "round0" in final_text or "round1" in final_text
        assert len(tool_outputs) == 2  # two echo tool results collected

    @pytest.mark.asyncio
    async def test_tool_loop_no_tools(self):
        """When no tools are registered, LLM gets no tools and responds directly."""
        fake_llm = FakeLLMService([
            {"type": "token", "content": "直接回答。"},
        ])

        sub = SubAgent(llm_service=fake_llm)
        final_text, tool_outputs = await sub._run_tool_loop("system", "user")
        assert "直接回答" in final_text
        assert tool_outputs == []

    @pytest.mark.asyncio
    async def test_register_tool_adds_to_registry(self):
        """register_tool populates the internal tool registry."""

        async def dummy(**kw) -> str:
            return "ok"

        sub = SubAgent()
        sub.register_tool("dummy", "desc", {"type": "object", "properties": {}}, dummy)

        tools = sub._tool_definitions()
        assert len(tools) == 1
        assert tools[0]["name"] == "dummy"


# ── Seam 3: Agent + SearchAgent handler ────────────────────────────────────


class TestAgentWithSearchAgent:
    """Agent.run() triggers research → SearchAgent handler."""

    @pytest.mark.asyncio
    async def test_research_calls_search_agent(self, monkeypatch):
        """When the main Agent calls research, it goes through SearchAgent.run."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from database import Base
        from services.memory_service import memory_service as memory_svc
        from core.agent import Agent
        from core.tool_runtime import ToolRuntime

        async def fake_mem_search(session, query="", top_k=3, character_id=None):
            return []

        # ── In-memory DB ────────────────────────────────────────
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            from models.character import CharacterProfile
            from models.conversation import Conversation
            from models.user_config import UserConfig

            char = CharacterProfile(id="char-sa", name="SA", personality="x",
                                     role="companion", archetype="friend")
            conv = Conversation(id="conv-sa", character_id="char-sa")
            cfg = UserConfig(id=1)
            session.add_all([char, conv, cfg])
            await session.commit()

            monkeypatch.setattr(memory_svc, "search", fake_mem_search)

            # ── SearchAgent as research handler ──────────────────
            from core.search_agent import SearchAgent
            search_agent = SearchAgent(
                llm_service=FakeLLMService([
                    {"type": "token", "content": "查到了。"},
                    {"type": "token", "content": "[confidence: medium]"},
                ]),
            )

            reg = ToolRegistry()
            reg.register("research", "search web", {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            }, search_agent.run, require_approval=False)

            rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=False)

            # Multi-round: round 1 → tool_use research, round 2 → final text
            fake_llm = FakeLLMService([
                [
                    {"type": "token", "content": "让我查一下。"},
                    {"type": "tool_use", "id": "c_res", "name": "research",
                     "arguments": {"query": "天气"}},
                ],
                [
                    {"type": "token", "content": "查到了，今天天气不错。"},
                ],
            ])

            agent = Agent(llm_service=fake_llm, tool_registry=rt)

            events = []
            async for event in agent.run(
                session=session,
                user_message="今天天气怎么样",
                conversation_id="conv-sa",
                character_id="char-sa",
            ):
                events.append(event)

            # ── Verify tool_result chain ────────────────────────
            tool_results = [e for e in events if e["type"] == "tool_result"]
            assert len(tool_results) == 1
            assert tool_results[0]["name"] == "research"
            assert tool_results[0]["is_error"] is False

            # The result should be the SearchAgent's JSON
            research_result = json.loads(tool_results[0]["result"])
            assert "answer" in research_result
            assert "confidence" in research_result

        await engine.dispose()


# ── Seam 4: RouterAgent placeholder + _resolve_handler hook ─────────────────


class TestRouterPlaceholder:
    """RouterAgent is importable and has the expected interface."""

    def test_router_agent_importable(self):
        from core.router_agent import RouterAgent
        router = RouterAgent()
        assert router is not None

    @pytest.mark.asyncio
    async def test_router_agent_raises_not_implemented(self):
        """RouterAgent.run() raises NotImplementedError (reserved)."""
        from core.router_agent import RouterAgent
        router = RouterAgent()
        with pytest.raises(NotImplementedError):
            await router.run("research", {"query": "test"})


class TestResolveHandler:
    """Agent._resolve_handler hook is present and calls dispatch."""

    def test_resolve_handler_returns_dispatch(self):
        from core.agent import Agent
        from core.tool_runtime import ToolRuntime

        agent = Agent(tool_registry=ToolRuntime(
            registry=ToolRegistry(), enable_tracing=False, enable_sandbox=False,
        ))
        handler = agent._resolve_handler("any_tool")
        assert handler is not None
        assert callable(handler)

    @pytest.mark.asyncio
    async def test_resolve_handler_dispatches(self):
        """_resolve_handler → dispatch executes the tool."""
        from core.agent import Agent
        from core.tool_runtime import ToolRuntime

        reg = ToolRegistry()

        async def echo(**kw) -> str:
            return f"echo: {kw}"

        reg.register("echo", "echo", {"type": "object", "properties": {}}, echo, False)
        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=False)
        agent = Agent(tool_registry=rt)

        handler = agent._resolve_handler("echo")
        result = await handler("echo", {"msg": "hello"})
        assert "echo:" in result
