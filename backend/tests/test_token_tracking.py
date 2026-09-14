"""
Tests for token tracking — Workflow G (#24 tokenizer+usage, #25 persistence+API).

Covers seams S1–S4: tokenizer estimation (incl. fallback), stream_chat usage
events (OpenAI / Anthropic / stream break), Agent per-round persistence, and
the GET /api/token-usage endpoint (per-round + aggregation + empty state).
No real LLM API or tiktoken download.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from services.llm_config import LLMRuntimeConfig
from services.token_counter import estimate_openai_tokens


# ── S1: tokenizer estimation ─────────────────────────────────────────────


class TestTokenizer:
    def test_estimate_openai_tokens_with_fake_encoder(self):
        # 1 token per char via the injected encoder → deterministic count.
        encoder = SimpleNamespace(encode=lambda text: [0] * len(text))
        messages = [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"arguments": '[{"a":1}]'}}],
            },
        ]
        # "hello" (5) + '[{"a":1}]' (9) = 14 chars → 14 tokens
        assert estimate_openai_tokens(messages, encoder=encoder) == 14

    def test_fallback_when_tiktoken_unavailable(self, monkeypatch):
        """tiktoken unavailable → char heuristic via the public API, never raises."""
        monkeypatch.setattr("services.token_counter._openai_encoder", lambda: None)
        assert estimate_openai_tokens([{"role": "user", "content": "你好"}]) == 1
        assert estimate_openai_tokens([{"role": "user", "content": ""}]) == 0

    def test_skips_empty_content(self):
        encoder = SimpleNamespace(encode=lambda text: [0] * len(text))
        assert estimate_openai_tokens([{"role": "user", "content": ""}], encoder=encoder) == 0

    @pytest.mark.asyncio
    async def test_anthropic_count_tokens(self):
        """Anthropic estimate goes through the SDK count_tokens (S1)."""
        from services.llm_service import LLMService

        runtime_config = LLMRuntimeConfig(provider="anthropic", model="claude-test")
        seen = {}

        class FakeAnthropicClient:
            @property
            def messages(self):
                return self

            async def count_tokens(self, **kwargs):
                seen.update(kwargs)
                return SimpleNamespace(input_tokens=42)

        llm = LLMService(clients={"anthropic": FakeAnthropicClient()}, runtime_config=runtime_config)
        result = await llm.estimate_prompt_tokens([
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hi"},
        ])
        assert result == 42
        assert seen["model"] == "claude-test"  # model is SDK-required
        # system must be split out of `messages` (Anthropic rejects it inline)
        assert seen["system"] == "you are helpful"
        assert seen["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_anthropic_count_tokens_fallback(self):
        """count_tokens failure → char heuristic, never raises."""
        from services.llm_service import LLMService

        runtime_config = LLMRuntimeConfig(provider="anthropic", model="claude-test")

        class BrokenClient:
            @property
            def messages(self):
                return self

            async def count_tokens(self, **kwargs):
                raise RuntimeError("count_tokens down")

        llm = LLMService(clients={"anthropic": BrokenClient()}, runtime_config=runtime_config)
        n = await llm.estimate_prompt_tokens([{"role": "user", "content": "你好"}])
        assert isinstance(n, int) and n > 0


# ── S2: stream_chat usage events (OpenAI) ────────────────────────────────


class _FakeOpenAIChunk:
    def __init__(self, content="", usage=None):
        self.choices = (
            [SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=None))]
            if content
            else []
        )
        self.usage = usage


class _FakeOpenAIStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        raise StopAsyncIteration


class _FakeOpenAIClient:
    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        return _FakeOpenAIStream([
            _FakeOpenAIChunk(content="hi"),
            _FakeOpenAIChunk(
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
            ),
        ])


class TestOpenAIUsage:
    @pytest.mark.asyncio
    async def test_stream_yields_usage_as_last_event(self):
        from services.llm_service import LLMService

        llm = LLMService(clients={"openai": _FakeOpenAIClient()})
        events = [e async for e in llm.stream_chat([{"role": "user", "content": "hi"}])]

        assert [e["type"] for e in events] == ["token", "usage"]
        u = events[-1]
        assert u["type"] == "usage"
        assert u["prompt_tokens"] == 10
        assert u["completion_tokens"] == 5
        assert u["total_tokens"] == 15
        assert isinstance(u["model"], str)


# ── S2: stream_chat usage events (Anthropic) ─────────────────────────────


class TestAnthropicUsage:
    @pytest.mark.asyncio
    async def test_stream_yields_usage_from_final_message(self, monkeypatch):
        from services.llm_service import LLMService

        runtime_config = LLMRuntimeConfig(provider="anthropic", model="claude-test")

        class FakeStreamManager:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration  # no text events

            def get_final_message(self):
                return SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                    content=[],
                )

        class FakeAnthropicClient:
            @property
            def messages(self):
                return self

            def stream(self, **kwargs):
                return FakeStreamManager()

        llm = LLMService(clients={"anthropic": FakeAnthropicClient()}, runtime_config=runtime_config)
        monkeypatch.setattr(llm, "_get_formatter", lambda: None)

        events = [e async for e in llm.stream_chat([{"role": "user", "content": "hi"}])]

        assert [e["type"] for e in events] == ["usage"]
        u = events[0]
        assert u["prompt_tokens"] == 10  # input_tokens
        assert u["completion_tokens"] == 5  # output_tokens
        assert u["total_tokens"] == 15


# ── S2: mid-stream break yields no usage ─────────────────────────────────


class TestStreamBreak:
    @pytest.mark.asyncio
    async def test_error_has_no_usage_event(self):
        from services.llm_service import LLMService

        class BoomStream(_FakeOpenAIStream):
            async def __anext__(self):
                if self._chunks:
                    return self._chunks.pop(0)
                raise RuntimeError("mid-stream boom")

        class BoomClient(_FakeOpenAIClient):
            async def create(self, **kwargs):
                return BoomStream([_FakeOpenAIChunk(content="部分")])

        llm = LLMService(clients={"openai": BoomClient()})
        events = [e async for e in llm.stream_chat([{"role": "user", "content": "hi"}])]

        assert [e["type"] for e in events] == ["token", "error"]
        assert not any(e["type"] == "usage" for e in events)


# ── S3: Agent per-round persistence ──────────────────────────────────────


def _make_db():
    """In-memory SQLite with llm_usage + agent tables registered."""
    import models.llm_usage  # noqa: F401 — ensure table on Base.metadata
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    return engine


async def _seed_agent(session):
    from models.character import CharacterProfile
    from models.conversation import Conversation
    from models.user_config import UserConfig

    session.add_all([
        CharacterProfile(id="char-u", name="T", personality="p", role="companion", archetype="friend"),
        Conversation(id="conv-u", character_id="char-u"),
        UserConfig(id=1),
    ])
    await session.commit()


class FakeLLMWithUsage:
    """Yields a preset event list; exposes a fixed token estimate."""

    def __init__(self, events, estimate=1234):
        self._events = events
        self._estimate = estimate

    async def stream_chat(self, messages, tools=None, on_retry=None):
        for e in self._events:
            yield e

    async def estimate_prompt_tokens(self, messages):
        return self._estimate


class RoundBasedFakeLLM:
    """Pops one preset round per stream_chat call; exposes a fixed estimate."""

    def __init__(self, rounds, estimate=5678):
        self._rounds = list(rounds)
        self._idx = 0
        self._estimate = estimate

    async def stream_chat(self, messages, tools=None, on_retry=None):
        if self._idx >= len(self._rounds):
            return
        events = self._rounds[self._idx]
        self._idx += 1
        for e in events:
            yield e

    async def estimate_prompt_tokens(self, messages):
        return self._estimate


class TestAgentPersistence:
    @pytest.mark.asyncio
    async def test_persists_single_round(self, monkeypatch):
        from services.memory_service import memory_service

        from core.agent import Agent
        from core.tool_registry import ToolRegistry
        from core.tool_runtime import ToolRuntime
        from models.llm_usage import LLMUsage
        from services.usage_store import LLMUsageStore

        engine = _make_db()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as session:
            await _seed_agent(session)

        async def _fake_search(*a, **k):
            return []

        monkeypatch.setattr(memory_service, "search", _fake_search)

        rt = ToolRuntime(registry=ToolRegistry(), enable_tracing=False, enable_sandbox=False)
        fake = FakeLLMWithUsage([
            {"type": "token", "content": "hi"},
            {"type": "usage", "model": "m1", "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ], estimate=1234)
        agent = Agent(
            llm_service=fake,
            tool_registry=rt,
            usage_store=LLMUsageStore(session_factory=factory),
        )

        async with factory() as session:
            async for _ in agent.run(
                session=session, user_message="hi",
                conversation_id="conv-u", character_id="char-u",
            ):
                pass

        async with factory() as s:
            rows = (await s.execute(select(LLMUsage))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.round_num == 0
        assert row.conversation_id == "conv-u"
        assert row.estimated_prompt_tokens == 1234
        assert row.prompt_tokens == 10 and row.completion_tokens == 5 and row.total_tokens == 15
        assert row.model == "m1"
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_persists_multiple_rounds(self, monkeypatch):
        from services.memory_service import memory_service

        from core.agent import Agent
        from core.tool_registry import ToolRegistry
        from core.tool_runtime import ToolRuntime
        from models.llm_usage import LLMUsage
        from services.usage_store import LLMUsageStore

        engine = _make_db()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as session:
            await _seed_agent(session)

        async def _fake_search(*a, **k):
            return []

        monkeypatch.setattr(memory_service, "search", _fake_search)

        # Round 0 yields a tool_use (so the loop continues); round 1 ends it.
        reg = ToolRegistry()

        async def fake_echo(**kw):
            return "echo"

        reg.register("fake_echo", "echo", {"type": "object", "properties": {}}, fake_echo, False)
        rt = ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=False)

        fake = RoundBasedFakeLLM([
            [
                {"type": "token", "content": "t"},
                {"type": "tool_use", "id": "c1", "name": "fake_echo", "arguments": {"a": 1}},
                {"type": "usage", "model": "m1", "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ],
            [
                {"type": "token", "content": "done"},
                {"type": "usage", "model": "m1", "prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26},
            ],
        ], estimate=5678)
        agent = Agent(
            llm_service=fake,
            tool_registry=rt,
            usage_store=LLMUsageStore(session_factory=factory),
        )

        async with factory() as session:
            async for _ in agent.run(
                session=session, user_message="hi",
                conversation_id="conv-u", character_id="char-u",
            ):
                pass

        async with factory() as s:
            rows = (await s.execute(
                select(LLMUsage).where(LLMUsage.conversation_id == "conv-u")
            )).scalars().all()
        assert len(rows) == 2
        by_round = {r.round_num: r for r in rows}
        assert 0 in by_round and 1 in by_round
        assert by_round[0].estimated_prompt_tokens == 5678
        assert by_round[0].total_tokens == 15
        assert by_round[1].total_tokens == 26
        await engine.dispose()


# ── S4: GET /api/token-usage ─────────────────────────────────────────────


@pytest.fixture
async def token_usage_client():
    from httpx import ASGITransport, AsyncClient

    import models.llm_usage  # noqa: F401 — ensure table registered

    engine = _make_db()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from main import app
    from database import get_session

    async def override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_usage(factory, *rows):
    from models.llm_usage import LLMUsage
    import uuid

    async with factory() as s:
        for r in rows:
            defaults = {
                "id": str(uuid.uuid4()),
                "conversation_id": "conv-api",
                "round_num": 0,
                "model": "m1",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "estimated_prompt_tokens": 9,
            }
            defaults.update(r)
            s.add(LLMUsage(**defaults))
        await s.commit()


class TestTokenUsageAPI:
    @pytest.mark.asyncio
    async def test_returns_per_round_and_summary(self, token_usage_client):
        client, factory = token_usage_client
        await _seed_usage(
            factory,
            {"round_num": 0, "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            {"round_num": 1, "prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26},
        )

        resp = await client.get("/api/token-usage", params={"conversation_id": "conv-api"})
        assert resp.status_code == 200
        body = resp.json()

        assert body["total"] == 2
        assert body["summary"] == {
            "rounds": 2,
            "prompt_tokens": 30,
            "completion_tokens": 11,
            "total_tokens": 41,
        }
        # newest round first (round 1)
        assert [u["round_num"] for u in body["usage"]] == [1, 0]

    @pytest.mark.asyncio
    async def test_empty_state(self, token_usage_client):
        client, _ = token_usage_client
        resp = await client.get("/api/token-usage", params={"conversation_id": "conv-empty"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["usage"] == []
        assert body["summary"] == {
            "rounds": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }
