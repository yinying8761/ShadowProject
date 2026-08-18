# 04 — token 追踪测试（test_token_tracking.py）

**What to build:** 新建 `backend/tests/test_token_tracking.py`，覆盖 tokenizer 估算（含降级）、
`stream_chat` 的 `usage` 事件（OpenAI/Anthropic/流中断）、Agent 每轮落库、`GET /api/token-usage`
（返回 + 聚合 + 空态）。复用现有注入缝，不依赖真实 LLM API / tiktoken 下载。

**Blocked by:** #01, #02

**Status:** ready-for-agent

**参照实现（照抄模式）：**

- 假 SDK stream：`test_llm_retry.py` 的 `FakeStream` / `FakeChunk`（用 `SimpleNamespace` 捏 `usage`）
- Agent 集成：`test_agent_tools.py::TestAgentRunWithInjectedRegistry`（内存 DB + `monkeypatch` 掉
  `memory_service.search`）
- HTTP API：`test_tool_runtime.py::tool_logs_client`（内存 SQLite + `ASGITransport` + dependency override）

## Checklist

- [ ] 新建 `backend/tests/test_token_tracking.py`
- [ ] **tokenizer 估算（S1）**：`estimate_openai_tokens` 注入假 encoder（`enc.encode=lambda t: [0]*len(t)`）
      → 结果确定可预期；`tiktoken` 不可用时（monkeypatch `_openai_encoder` 返回 None）→ 走字符估算不抛异常
- [ ] **OpenAI usage 事件（S2）**：假 OpenAI stream 的最后一个 chunk 带 `usage`（`SimpleNamespace(prompt_tokens=10,
      completion_tokens=5, total_tokens=15)`）→ `stream_chat` 末尾 yield `{"type":"usage","total_tokens":15}`
- [ ] **Anthropic usage 事件（S2）**：注入假 anthropic client + 假 stream，`get_final_message().usage`
      返回 `input_tokens/output_tokens` → yield 对应 `usage` 事件
- [ ] **流中断不 yield usage（S2）**：stream 中途抛错 → 只 yield `error`，不 yield `usage`
- [ ] **Agent 每轮落库（S3）**：`Agent(llm_service=FakeLLMWithUsage, usage_store=内存store)`，
      FakeLLM yield token → usage 事件 → 无 tool_use；断言写入一条 `llm_usage`（`round_num=0`、
      `conversation_id` 正确、`estimated_prompt_tokens` == FakeLLM 返回的预估值）
- [ ] **Agent 多轮落库（S3）**：FakeLLM 按轮 yield（复用 F 的 `RoundBasedFakeLLM` 思路），两轮各带
      usage → 断言写两条记录，`round_num` 分别为 0 和 1
- [ ] **HTTP API（S4）**：`GET /api/token-usage?conversation_id=xxx` 返回逐轮记录（倒序）+ 正确的
      `summary` 聚合；无记录时 `total=0`、`summary.rounds=0`、`usage=[]`
- [ ] 全量回归：
      `python -m pytest tests/test_token_tracking.py tests/test_llm_retry.py tests/test_agent_tools.py tests/core/test_agent_tools.py -v`

**FakeLLMWithUsage 参考：**

```python
class FakeLLMWithUsage:
    """Yields a preset event list; exposes a fixed token estimate."""

    def __init__(self, events, estimate=1234):
        self._events = events
        self._estimate = estimate

    async def stream_chat(self, messages, tools=None):
        for e in self._events:
            yield e

    async def estimate_prompt_tokens(self, messages):
        return self._estimate
```
