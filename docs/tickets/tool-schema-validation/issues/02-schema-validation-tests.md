# 02 — Schema 校验测试（test_tool_schema.py + Agent 自纠集成）

**What to build:** 新建 `backend/tests/test_tool_schema.py`，覆盖 Workflow F 的全部外部行为，
并加一条 `Agent.run` 全链路自纠集成测试。复用现有注入缝（隔离 `ToolRegistry`、内存 SQLite
`ToolTraceStore`、`FakeLLMService`），不依赖真实 LLM / MCP / HTTP。

**Blocked by:** #01

**Status:** ready-for-agent

**参照实现（照抄模式）：**

- 隔离 runtime：`test_tool_runtime.py` 的 `ToolRuntime(registry=reg, enable_tracing=False, enable_sandbox=False)`
- trace 注入：`test_tool_retry.py::_make_runtime`（内存 `ToolTraceStore`）
- 熔断注入：`test_circuit_breaker.py::TestCircuitBreakerIntegration`（`FakeClock` + `enable_circuit_breaker=True`）
- Agent 集成：`test_agent_tools.py::TestAgentRunWithInjectedRegistry`（内存 DB + `monkeypatch` 掉 `memory_service.search`；假 LLM 用下方 `RoundBasedFakeLLM`）

## Checklist

- [ ] 新建 `backend/tests/test_tool_schema.py`
- [ ] **类型错误拦截**：schema 要求 `path: string`，`dispatch(name, {"path": 123})` →
      返回 error JSON 含 `not of type 'string'`，handler 未被调用（`calls["n"] == 0`）
- [ ] **缺必填拦截**：schema `required: ["content"]`，dispatch 缺 `content` →
      error 含 `required property`
- [ ] **越界拦截**：`importance` 有 `minimum:1, maximum:10`，传 15 → error 含 `maximum of 10`
- [ ] **enum 非法拦截**：`memory_type` enum 外值 → error 含 `not one of [...]`
- [ ] **校验通过执行**：合法参数 → handler 正常执行并返回真实结果
- [ ] **未知字段被容忍（宽松）**：`{"path": "/tmp/x", "extra": 1}` → 不因 `extra` 失败
      （不透传校验拒绝），handler 仍收到 `**kwargs`（含 `extra`）
- [ ] **无 schema 跳过**：`register(..., parameters=None)` 或空 dict → 不校验，正常执行
- [ ] **未知工具跳过**：dispatch 未注册工具 → 返回现有 `Unknown tool` 错误，而非 schema 错误
- [ ] **校验失败写 trace**：`enable_tracing=True` + 内存 store → `success=False`、
      `error_message` 含 `Schema validation failed`、`retry_count=0`
- [ ] **校验失败不触发熔断**：`enable_circuit_breaker=True, circuit_threshold=1`，
      连续多次非法参数 dispatch 后 breaker 仍 `CLOSED`，工具未被熔断，handler 始终未执行
- [ ] **MCP inputSchema 生效**：以 `mcp__x__y` 名 + `{"type":"object","properties":{...},"required":[...]}`
      注册 → 校验生效；`parameters=None` 注册 → 跳过
- [ ] **Agent 自纠集成（S4）**：用 `RoundBasedFakeLLM`（见下方），第 1 轮发 `tool_use` 参数
      `{"path": 123}` → 收集到 `tool_result`（`is_error=True`，result 含 `Schema validation failed`）→
      第 2 轮发 `{"path": "/tmp/x"}` → 工具成功、最终 `done`
- [ ] 全量回归：
      `python -m pytest tests/test_tool_schema.py tests/test_tool_runtime.py tests/test_tool_retry.py tests/test_circuit_breaker.py tests/core/test_agent_tools.py -v`

**Agent 自纠集成测试的 Fake LLM 参考：**

`test_agent_tools.py` 里的 `FakeLLMService` 会在**第一次** `stream_chat` 就把全部事件
一口气回放完，无法模拟「第 1 轮传错 → 第 2 轮自纠」的多轮循环。集成测试需要一个新的
**按轮回放**的假 LLM（每调用一次 `stream_chat` 弹出下一轮的事件）：

```python
class RoundBasedFakeLLM:
    """Yields one preset per-round event list on each stream_chat call."""

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


fake_llm = RoundBasedFakeLLM([
    # Round 0: LLM 传错参数 → 校验失败
    [
        {"type": "token", "content": "让我读一下"},
        {"type": "tool_use", "id": "c1", "name": "read_file", "arguments": {"path": 123}},
    ],
    # Round 1: 收到校验错误后自纠 → 成功
    [
        {"type": "token", "content": "我换正确路径"},
        {"type": "tool_use", "id": "c2", "name": "read_file", "arguments": {"path": "/tmp/x"}},
    ],
    # Round 2: 无工具，收尾
    [
        {"type": "token", "content": "读到了。"},
    ],
])
```

断言要点：收集到的 `tool_result` 有两条——第一条 `is_error=True` 且 result 含
`Schema validation failed`，第二条 `is_error=False`；handler（用 `calls={"n":0}` 计数）
只被调用一次且参数为合法值；最后一个事件是 `done`。
