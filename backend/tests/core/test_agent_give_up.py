"""失败账本 + 轮次耗尽收尾（ticket #71 / #72）：主循环的硬限制，不靠 prompt。

Seam: `Agent.run` + 一个**脚本化假 LLM**（按轮吐 tool_use / token）+ 一个假工具注册表
（工具什么时候失败由测试决定）。要验证的是主循环自己的行为：

1. 同一工具连续失败 2 次后不再真的执行它（模型再怎么要也不给）；
2. 停用后允许换一个不同工具；替补也失败 → 立刻停手；
3. 用户拒绝审批不计入失败账本，但该工具本轮停用；
4. 轮次耗尽 / 主动停手**必定给出非空回答**；收尾请求不带工具；收尾拿不到内容时用固定文案。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.agent import Agent
from core.tool_registry import ToolRegistry
from core.tool_runtime import ToolRuntime
from database import Base
from models.character import CharacterProfile
from models.conversation import Conversation
from models.message import Message

#: 收尾请求的标记（WRAP_UP_PROMPT 里的一句话）
_WRAP_MARKER = "工具尝试已经结束"


class ScriptedLLM:
    """按脚本逐轮回事件；识别出收尾请求时用 `wrap_up` 那段文本回答。"""

    def __init__(self, rounds: list[list[dict]], *, wrap_up: str = "我没拿到内容。"):
        self._rounds = list(rounds)
        self._wrap_up = wrap_up
        self.requests: list[dict] = []

    async def estimate_prompt_tokens(self, messages) -> int:
        return 1

    def _is_wrap_up(self, messages) -> bool:
        last = messages[-1] if messages else {}
        return _WRAP_MARKER in str(last.get("content", ""))

    async def stream_chat(self, messages, tools=None, on_retry=None):
        wrap = self._is_wrap_up(messages)
        self.requests.append({"tools": tools, "wrap": wrap, "messages": list(messages)})
        if wrap:
            for char in self._wrap_up:
                yield {"type": "token", "content": char}
            return
        index = min(
            len([r for r in self.requests if not r["wrap"]]) - 1,
            len(self._rounds) - 1,
        )
        for event in self._rounds[index]:
            yield event


def _call(name: str, *, call_id: str = "c1", **arguments) -> dict:
    return {
        "type": "tool_use",
        "id": call_id,
        "name": name,
        "arguments": arguments or {"path": "x"},
    }


def _text(text: str) -> dict:
    return {"type": "token", "content": text}


class FlakyTools:
    """按脚本决定每次调用成功/失败的假工具（记下真实执行次数）。"""

    def __init__(self, behaviour: dict[str, list[bool]] | None = None, *, default_ok=False):
        self._behaviour = {k: list(v) for k, v in (behaviour or {}).items()}
        self._default_ok = default_ok
        self.calls: dict[str, int] = {}

    def _ok(self, name: str) -> bool:
        self.calls[name] = self.calls.get(name, 0) + 1
        script = self._behaviour.get(name)
        if not script:
            return self._default_ok
        return script[min(self.calls[name] - 1, len(script) - 1)]

    def registry(self, *, approval: tuple[str, ...] = ()) -> ToolRegistry:
        registry = ToolRegistry()

        def make(name: str):
            async def handler(**_kwargs) -> str:
                if self._ok(name):
                    return f"{name} ok"
                raise RuntimeError(f"{name} boom")

            return handler

        for name in ("read_file", "fetch_url", "write_file"):
            registry.register(
                name=name,
                description=f"fake {name}",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=make(name),
                require_approval=name in approval,
            )
        return registry


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory) -> str:
    async with factory() as s:
        s.add(CharacterProfile(id="c1", name="小柔", personality="温柔", role="companion", archetype="friend"))
        s.add(Conversation(id="conv-g", character_id="c1"))
        await s.commit()
    return "conv-g"


def _agent(llm, registry: ToolRegistry) -> Agent:
    return Agent(
        llm_service=llm,
        tool_registry=ToolRuntime(registry, enable_tracing=False, enable_sandbox=False),
    )


async def _run(llm, factory, registry, *, approval=None):
    conv_id = await _seed(factory)
    events = []
    async with factory() as session:
        async for event in _agent(llm, registry).run(
            session,
            user_message="帮我读一下文件",
            conversation_id=conv_id,
            character_id="c1",
            approval_callback=approval,
        ):
            events.append(event)
    reply = "".join(e["content"] for e in events if e["type"] == "token").strip()
    return events, reply, conv_id


def _tool_rounds(name: str, count: int) -> list[list[dict]]:
    return [[_call(name, call_id=f"c{i}")] for i in range(1, count + 1)]


class TestFailureLedgerInTheLoop:
    """ticket #71：同一个工具连续失败 2 次就不再执行（硬限制）。"""

    @pytest.mark.asyncio
    async def test_the_same_tool_is_not_executed_more_than_twice(self, factory):
        tools = FlakyTools({"read_file": [False]})
        llm = ScriptedLLM(_tool_rounds("read_file", 5))

        events, _reply, _ = await _run(llm, factory, tools.registry())

        assert tools.calls["read_file"] == 2      # 后三次被账本拦下
        blocked = [e for e in events if e.get("disabled")]
        assert blocked and "本轮已停用" in blocked[0]["result"]

    @pytest.mark.asyncio
    async def test_a_disabled_tool_is_dropped_from_the_offered_tools(self, factory):
        tools = FlakyTools({"read_file": [False]})
        llm = ScriptedLLM(_tool_rounds("read_file", 5))

        await _run(llm, factory, tools.registry())

        normal = [r for r in llm.requests if not r["wrap"]]
        offered_first = {t["name"] for t in (normal[0]["tools"] or [])}
        offered_after = {t["name"] for t in (normal[2]["tools"] or [])}
        assert "read_file" in offered_first
        assert "read_file" not in offered_after

    @pytest.mark.asyncio
    async def test_a_success_resets_the_failure_streak(self, factory):
        # 失败 → 成功（清零）→ 失败 → 失败（这时才停用）
        tools = FlakyTools({"read_file": [False, True, False]})
        llm = ScriptedLLM(_tool_rounds("read_file", 5))

        events, _reply, _ = await _run(llm, factory, tools.registry())

        assert tools.calls["read_file"] == 4        # 比"连续两次就停"多跑一次，证明清零生效
        blocked = [e for e in events if e.get("disabled")]
        assert blocked and "连续失败 2 次" in blocked[0]["result"]

    @pytest.mark.asyncio
    async def test_a_failing_substitute_tool_stops_the_loop(self, factory):
        tools = FlakyTools({"read_file": [False], "fetch_url": [False]})
        llm = ScriptedLLM([
            [_call("read_file", call_id="c1")],
            [_call("read_file", call_id="c2")],
            [_call("fetch_url", call_id="c3")],
            [_call("write_file", call_id="c4")],       # 不该再有机会
        ])

        events, _reply, _ = await _run(llm, factory, tools.registry())

        assert tools.calls == {"read_file": 2, "fetch_url": 1}   # 替补失败后立刻停
        normal = [r for r in llm.requests if not r["wrap"]]
        assert len(normal) == 3                        # 3 个工具轮 + 1 次收尾

    @pytest.mark.asyncio
    async def test_a_denied_approval_disables_the_tool_without_counting_it(self, factory):
        tools = FlakyTools(default_ok=True)
        llm = ScriptedLLM([
            [_call("write_file", call_id="c1")],
            [_call("write_file", call_id="c2")],
        ])

        async def deny(_name, _args):
            return False

        events, _reply, _ = await _run(
            llm, factory, tools.registry(approval=("write_file",)), approval=deny
        )

        assert tools.calls.get("write_file", 0) == 0     # 拒绝了就不该执行
        assert [e for e in events if e.get("denied")]    # 第一次：审批被拒
        blocked = [e for e in events if e.get("disabled")]
        assert blocked and "用户拒绝审批" in blocked[0]["result"]   # 第二次直接拦下
