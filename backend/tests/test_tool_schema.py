"""
Tests for ToolRuntime argument JSON Schema validation — Workflow F (#20/#21).

Covers (per spec Testing Decisions, seams S1–S4): interception of type errors,
missing required, out-of-range and enum violations; lenient pass-through of
unknown fields; skip when a tool has no schema or is unknown; failure traces;
no circuit-breaker interference; MCP inputSchema coverage; the malformed-schema
fallback added in #20 review; and the Agent self-correction loop.

These tests use an isolated ToolRegistry / in-memory ToolTraceStore / FakeLLM
— no real LLM, MCP server, or HTTP is involved.
"""

import json

import pytest

from core.circuit_breaker import CLOSED
from core.tool_registry import ToolRegistry
from core.tool_runtime import ToolRuntime


# ── Shared schemas (mirror the first-party tool shapes from main.py) ─────

READ_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
}

MEMORY_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "memory_type": {
            "type": "string",
            "enum": ["user_fact", "user_preference", "important_event"],
        },
        "importance": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "required": ["content"],
}


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_runtime(reg, **kwargs):
    """Isolated ToolRuntime — S1 seam (mirrors test_tool_runtime.py)."""
    defaults = {"enable_tracing": False, "enable_sandbox": False}
    defaults.update(kwargs)
    return ToolRuntime(registry=reg, **defaults)


class FakeClock:
    """Controllable monotonic clock for circuit-breaker tests."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


async def _make_tracing_runtime(reg):
    """ToolRuntime with tracing + in-memory store — S2 seam (test_tool_retry)."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from database import Base
    from services.tool_trace_store import ToolTraceStore

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    rt = ToolRuntime(registry=reg, enable_tracing=True, enable_sandbox=False)
    rt._trace_store = ToolTraceStore(session_factory=factory)
    return rt, factory, engine


class RoundBasedFakeLLM:
    """Yields one preset per-round event list on each stream_chat call.

    Unlike FakeLLMService in test_agent_tools.py (which replays everything on
    the first call), this pops the next round on every call, so it can model
    "round 1 passes wrong args -> round 2 self-corrects".
    """

    def __init__(self, rounds: list[list[dict]]):
        self._rounds = rounds
        self._idx = 0

    async def stream_chat(self, messages, tools=None):
        if self._idx >= len(self._rounds):
            return
        events = self._rounds[self._idx]
        self._idx += 1
        for event in events:
            yield event

    async def estimate_prompt_tokens(self, messages) -> int:
        """Stub for Workflow G — the Agent pre-estimates each round's prompt;
        this fake emits no usage events so nothing is persisted."""
        return 0


async def _seed_test_db(session):
    """Minimal test data: character + conversation + user config."""
    from models.character import CharacterProfile
    from models.conversation import Conversation
    from models.user_config import UserConfig

    char = CharacterProfile(
        id="char-schema-1", name="测试角色", personality="开朗",
        role="companion", archetype="friend",
    )
    conv = Conversation(id="conv-schema-1", character_id="char-schema-1")
    cfg = UserConfig(id=1)
    session.add_all([char, conv, cfg])
    await session.commit()


async def _fake_memory_search(session, query="", top_k=3, character_id=None):
    """Stub that returns an empty list — avoids FTS5 table dependency."""
    return []


# ── S1: Interception through the isolated ToolRuntime ────────────────────

class TestValidationInterception:
    """Malformed arguments are intercepted; the handler never runs."""

    @pytest.mark.asyncio
    async def test_type_error_intercepted(self):
        reg = ToolRegistry()
        rt = _make_runtime(reg)
        calls = {"n": 0}

        async def read_file(**kw):
            calls["n"] += 1
            return "content"

        rt.register("read_file", "read a file", READ_SCHEMA, read_file, False)

        result = await rt.dispatch("read_file", {"path": 123})

        parsed = json.loads(result)
        assert "Schema validation failed: path —" in parsed["error"]  # F6: pointer path + separator
        assert "not of type 'string'" in parsed["error"]
        assert calls["n"] == 0  # handler never called

    @pytest.mark.asyncio
    async def test_missing_required_intercepted(self):
        reg = ToolRegistry()
        rt = _make_runtime(reg)
        calls = {"n": 0}

        async def save_memory(**kw):
            calls["n"] += 1
            return "saved"

        rt.register("save_memory", "save", MEMORY_SCHEMA, save_memory, False)

        result = await rt.dispatch("save_memory", {})

        parsed = json.loads(result)
        assert "Schema validation failed: $ —" in parsed["error"]  # F6: root pointer + separator
        assert "required property" in parsed["error"]
        assert calls["n"] == 0

    @pytest.mark.asyncio
    async def test_out_of_range_intercepted(self):
        reg = ToolRegistry()
        rt = _make_runtime(reg)
        calls = {"n": 0}

        async def save_memory(**kw):
            calls["n"] += 1
            return "saved"

        rt.register("save_memory", "save", MEMORY_SCHEMA, save_memory, False)

        result = await rt.dispatch("save_memory", {"content": "x", "importance": 15})

        parsed = json.loads(result)
        assert "Schema validation failed" in parsed["error"]
        assert "maximum of 10" in parsed["error"]
        assert calls["n"] == 0

    @pytest.mark.asyncio
    async def test_enum_violation_intercepted(self):
        reg = ToolRegistry()
        rt = _make_runtime(reg)
        calls = {"n": 0}

        async def save_memory(**kw):
            calls["n"] += 1
            return "saved"

        rt.register("save_memory", "save", MEMORY_SCHEMA, save_memory, False)

        result = await rt.dispatch("save_memory", {"content": "x", "memory_type": "random"})

        parsed = json.loads(result)
        assert "Schema validation failed" in parsed["error"]
        assert "not one of" in parsed["error"]
        assert calls["n"] == 0

    @pytest.mark.asyncio
    async def test_valid_arguments_execute(self):
        reg = ToolRegistry()
        rt = _make_runtime(reg)
        calls = {"n": 0}

        async def read_file(**kw):
            calls["n"] += 1
            return "content of " + kw["path"]

        rt.register("read_file", "read a file", READ_SCHEMA, read_file, False)

        result = await rt.dispatch("read_file", {"path": "/tmp/x"})

        assert result == "content of /tmp/x"
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_unknown_fields_tolerated(self):
        """Lenient validation (F5): undeclared fields pass through to the handler."""
        reg = ToolRegistry()
        rt = _make_runtime(reg)
        captured = {}

        async def read_file(**kw):
            captured.update(kw)
            return "content of " + kw["path"]

        rt.register("read_file", "read a file", READ_SCHEMA, read_file, False)

        result = await rt.dispatch("read_file", {"path": "/tmp/x", "extra": 1})

        assert result == "content of /tmp/x"
        assert captured == {"path": "/tmp/x", "extra": 1}  # extra reached the handler

    @pytest.mark.asyncio
    async def test_no_schema_skipped(self):
        """Tools with parameters=None or an empty dict skip validation entirely."""
        reg = ToolRegistry()
        rt = _make_runtime(reg)
        calls = {"n": 0}

        async def tool(**kw):
            calls["n"] += 1
            return "ok"

        rt.register("no_schema", "ns", None, tool, False)
        rt.register("empty_schema", "es", {}, tool, False)

        assert await rt.dispatch("no_schema", {"anything": 1}) == "ok"
        assert await rt.dispatch("empty_schema", {"anything": 1}) == "ok"
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_unknown_tool_skipped(self):
        """Unregistered tools take the existing 'Unknown tool' path, not a schema error."""
        reg = ToolRegistry()
        rt = _make_runtime(reg)

        result = await rt.dispatch("nonexistent", {})

        parsed = json.loads(result)
        assert "Unknown tool" in parsed["error"]
        assert "Schema validation failed" not in parsed["error"]

    @pytest.mark.asyncio
    async def test_injected_character_id_not_rejected(self):
        """US13: hidden args injected by Agent (e.g. character_id) must not be
        rejected as undeclared fields — lenient validation lets them through."""
        reg = ToolRegistry()
        rt = _make_runtime(reg)
        captured = {}

        async def save_memory(**kw):
            captured.update(kw)
            return "saved"

        rt.register("save_memory", "save", MEMORY_SCHEMA, save_memory, False)

        result = await rt.dispatch("save_memory", {"content": "用户喜欢咖啡", "character_id": "char-1"})

        assert result == "saved"
        assert captured == {"content": "用户喜欢咖啡", "character_id": "char-1"}


# ── S2: Failure traces ───────────────────────────────────────────────────

class TestValidationTrace:
    """Validation failures are persisted as failure traces (S2 seam)."""

    @pytest.mark.asyncio
    async def test_validation_failure_writes_trace(self):
        from sqlalchemy import select

        from models.tool_run import ToolRun

        reg = ToolRegistry()
        rt, factory, engine = await _make_tracing_runtime(reg)
        calls = {"n": 0}

        async def read_file(**kw):
            calls["n"] += 1
            return "content"

        rt.register("read_file", "read a file", READ_SCHEMA, read_file, False)

        await rt.dispatch("read_file", {"path": 123})

        async with factory() as s:
            run = (await s.execute(select(ToolRun))).scalars().one()

        assert run.tool_name == "read_file"
        assert run.success is False
        assert "Schema validation failed" in (run.error_message or "")
        assert run.retry_count == 0  # validation is deterministic — never retried
        assert calls["n"] == 0
        await engine.dispose()


# ── S3: No circuit-breaker interference ─────────────────────────────────

class TestValidationAndCircuitBreaker:
    @pytest.mark.asyncio
    async def test_validation_failure_does_not_touch_breaker(self):
        """Argument errors are the LLM's fault, not a tool-health signal:
        repeated invalid dispatches must not count toward tripping the breaker."""
        reg = ToolRegistry()
        clock = FakeClock()
        rt = _make_runtime(
            reg,
            enable_circuit_breaker=True,
            circuit_threshold=1,  # a single handler failure would open it
            clock=clock,
        )
        calls = {"n": 0}

        async def read_file(**kw):
            calls["n"] += 1
            return "ok"

        rt.register("read_file", "read a file", READ_SCHEMA, read_file, False)

        # A valid call first — creates the per-tool breaker and succeeds.
        assert await rt.dispatch("read_file", {"path": "/tmp/x"}) == "ok"
        assert calls["n"] == 1

        # Hammer with invalid args — any one of these would open the breaker
        # if validation failures counted toward it.
        for _ in range(3):
            result = await rt.dispatch("read_file", {"path": 123})
            assert "Schema validation failed" in json.loads(result)["error"]
        assert calls["n"] == 1  # handler not called for invalid args

        breaker = rt._circuit_breakers["read_file"]
        assert breaker.state == CLOSED  # threshold=1 — any recorded failure would open it

        # Tool still executes normally — not short-circuited.
        assert await rt.dispatch("read_file", {"path": "/tmp/x"}) == "ok"
        assert calls["n"] == 2


# ── MCP inputSchema coverage (F7) ────────────────────────────────────────

class TestMcpInputSchema:
    """MCP tools get the same validation via their inputSchema (F7)."""

    @pytest.mark.asyncio
    async def test_mcp_input_schema_validated(self):
        reg = ToolRegistry()
        rt = _make_runtime(reg)
        calls = {"n": 0}

        async def mcp_tool(**kw):
            calls["n"] += 1
            return "mcp ok"

        rt.register(
            "mcp__server__tool",
            "mcp tool",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            mcp_tool,
            False,
        )

        result = await rt.dispatch("mcp__server__tool", {"query": 42})
        assert "Schema validation failed" in json.loads(result)["error"]
        assert calls["n"] == 0

        assert await rt.dispatch("mcp__server__tool", {"query": "hello"}) == "mcp ok"
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_mcp_without_input_schema_skipped(self):
        """A server tool with no inputSchema must not be broken by validation."""
        reg = ToolRegistry()
        rt = _make_runtime(reg)

        async def mcp_tool(**kw):
            return "mcp ok"

        rt.register("mcp__server__tool", "mcp tool", None, mcp_tool, False)

        assert await rt.dispatch("mcp__server__tool", {"anything": 1}) == "mcp ok"


# ── Malformed schema fallback (#20 review fix) ───────────────────────────

class TestMalformedSchema:
    """A broken schema degrades to a generic failure, never a raise (F3/#20 fix)."""

    @pytest.mark.asyncio
    async def test_malformed_schema_falls_back_to_generic_failure(self):
        """A broken schema (e.g. a bad MCP inputSchema) raises SchemaError /
        RefResolutionError. dispatch must not raise and must not blame the
        arguments — it returns a generic error envelope instead."""
        reg = ToolRegistry()
        rt = _make_runtime(reg)
        calls = {"n": 0}

        async def tool(**kw):
            calls["n"] += 1
            return "ok"

        # `required` must be an array → SchemaError
        rt.register("mcp__bad__tool", "bad",
                     {"type": "object", "properties": {}, "required": "notalist"},
                     tool, False)
        # Unresolvable $ref → RefResolutionError
        rt.register("mcp__badref__tool", "badref",
                     {"type": "object", "properties": {"x": {"$ref": "#/definitions/nope"}}},
                     tool, False)

        # Malformed schema (`required` not an array) → SchemaError
        result = await rt.dispatch("mcp__bad__tool", {})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Schema validation failed" not in parsed["error"]  # not blamed on args

        # Unresolvable $ref → RefResolutionError (only triggered when the
        # instance actually carries the property that references it)
        result = await rt.dispatch("mcp__badref__tool", {"x": 1})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Schema validation failed" not in parsed["error"]

        assert calls["n"] == 0


# ── S4: Agent self-correction loop ───────────────────────────────────────

class TestAgentSelfCorrection:
    """End-to-end: the LLM sees a validation error and fixes its call (S4)."""

    @pytest.mark.asyncio
    async def test_agent_self_corrects_after_validation_error(self, monkeypatch):
        """Round 1 sends a bad path -> validation error tool_result; round 2
        self-corrects to a valid path -> tool succeeds; run ends with done."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from core.agent import Agent
        from database import Base
        from services.memory_service import memory_service as memory_svc

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            await _seed_test_db(session)
            monkeypatch.setattr(memory_svc, "search", _fake_memory_search)

            reg = ToolRegistry()
            calls = {"n": 0}

            async def read_file(**kw):
                calls["n"] += 1
                return "file contents"

            reg.register("read_file", "read a file", READ_SCHEMA, read_file, False)
            rt = _make_runtime(reg)

            fake_llm = RoundBasedFakeLLM([
                # Round 0: LLM sends wrong args -> validation failure
                [
                    {"type": "token", "content": "让我读一下"},
                    {"type": "tool_use", "id": "c1", "name": "read_file", "arguments": {"path": 123}},
                ],
                # Round 1: LLM sees the validation error and self-corrects
                [
                    {"type": "token", "content": "我换正确路径"},
                    {"type": "tool_use", "id": "c2", "name": "read_file", "arguments": {"path": "/tmp/x"}},
                ],
                # Round 2: no tools — wrap up
                [
                    {"type": "token", "content": "读到了。"},
                ],
            ])

            agent = Agent(llm_service=fake_llm, tool_registry=rt)

            events = []
            async for event in agent.run(
                session=session,
                user_message="帮我读文件",
                conversation_id="conv-schema-1",
                character_id="char-schema-1",
            ):
                events.append(event)

            tool_results = [e for e in events if e["type"] == "tool_result"]
            assert len(tool_results) == 2

            # Round 1: validation error surfaced as a clean, LLM-readable result
            assert tool_results[0]["is_error"] is True
            assert "Schema validation failed" in tool_results[0]["result"]
            assert "path" in tool_results[0]["result"]

            # Round 2: corrected args -> tool succeeds
            assert tool_results[1]["is_error"] is False
            assert tool_results[1]["result"] == "file contents"

            # Handler ran exactly once, with the valid arguments
            assert calls["n"] == 1

            assert events[-1]["type"] == "done"

        await engine.dispose()
