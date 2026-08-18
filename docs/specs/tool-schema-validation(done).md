# Tool Schema Validation — 工具参数 JSON Schema 校验 + LLM 自纠

> 日期：2026-08-14 | 状态：待实现 | 父迭代：`iteration-plan2.md` Workflow F

## Problem Statement

当前 LLM 调工具时，参数直接透传给 handler，没有任何校验。LLM 一旦传错参数——例如把
`read_file(path=123)` 的路径传成 int、`write_file` 漏传 `content`、`save_memory` 的
`importance` 传成 15——参数会原样进入 handler，然后：

1. handler 崩溃（`TypeError: expected str, got int`）或被 `dispatch()` 捕获后回一个
   **Python 报错原文**，LLM 只能靠猜去修正，甚至反复用错误参数重试同一工具。
2. 有些错误参数甚至能"成功"执行但产生错误副作用（如越界的 `max_results`），事后难以排查。
3. 没有给 LLM 一个**结构化、可自纠**的反馈通道——它看到的错误信息不是"哪个字段、错在哪、
   正确类型是什么"，而是一坨异常堆栈。

（LLM API 重试/熔断是 Workflow E，token 计数是 Workflow G，均不在本 spec 范围内。）

## Solution

在 `ToolRuntime.dispatch()` 中、调用 `handler(**arguments)` 之前，插入一步 **JSON Schema
校验**：用工具注册时已有的 `parameters`（JSON Schema），通过 `jsonschema` 库校验
`arguments`。

- 校验通过 → 照常执行 handler。
- 校验失败 → **不执行 handler**，返回 `{"error": "Schema validation failed: <详细原因>"}`。
- 该错误作为 `tool_result` 回传给 LLM（现有 `Agent` 的 `is_error` 解包逻辑无需改动），
  LLM 在下一轮自然修正参数后重调。
- 不自设纠错次数上限——由 Agent loop 现有的 5 轮预算兜底。

对 Agent 和前端完全透明：`ToolRuntime` 对外接口不变，新增能力收敛在 `dispatch()` 内部
和 `ToolRegistry` 的一个只读 getter 上。

## User Stories

1. 作为用户，当 LLM 把文件路径传成数字（`read_file(path=123)`）时，我希望系统拦截并在
   不崩溃的情况下告诉 LLM 修正，而不是得到一堆 Python 报错。
2. 作为用户，当 LLM 调用 `write_file` 漏传 `content` 时，我希望系统在**真正写文件之前**
   拦截，避免写出空文件或崩溃。
3. 作为用户，当 LLM 传了越界的参数（如 `save_memory(importance=15)`）时，我希望系统拦截，
   让 LLM 回到 1–10 的范围内。
4. 作为用户，当 LLM 传了 `enum` 之外的非法值（如 `memory_type="random"`）时，我希望系统拦截。
5. 作为用户，只有当参数全部合法时，工具才真正执行——校验失败的工具调用**绝不**产生副作用。
6. 作为用户，校验失败不应该让其他工具受影响，也不应该被算作"工具失败"触发熔断。
7. 作为开发者，校验失败时应有一条 `tool_runs` trace（`success=False`、`error_message` 为校验原因），
   方便排查"为什么 LLM 一直在重复调某个工具"。
8. 作为用户，我希望校验错误信息足够清晰（指出字段路径和正确类型），LLM 能据此自纠，而不是
   反复用同样的错误参数。
9. 作为用户，MCP 工具（`mcp__<server>__<tool>`）也应该享受同样的参数校验（用它们的
   `inputSchema`），而不是只有内置工具被校验。
10. 作为用户，某个 MCP 工具没有提供 inputSchema 时，校验应静默跳过，不能因为校验而把好工具搞坏。
11. 作为用户，校验本身不应拖慢对话——它在后台线程执行，不阻塞事件循环。
12. 作为开发者，校验逻辑能用假工具/假 registry 单元测试，不依赖真实 LLM API 或真实 MCP server。
13. 作为用户，我不希望校验误伤——`Agent` 内部注入的隐藏参数（如 `character_id`）不能被当成
    非法字段拒绝掉，否则记忆工具会坏。

## Implementation Decisions

### F1：新增 `jsonschema` 依赖

`requirements.txt` 增加 `jsonschema>=4.20`。这是唯一的新依赖。校验用 `jsonschema.validate`。

### F2：`ToolRegistry` 暴露只读 schema getter

`ToolRegistry` 目前把 `parameters` 存在 `_tools[name]["parameters"]`，但没有公开的读取方法。
新增一个只读 getter（不改任何现有方法）：

```python
# core/tool_registry.py
def get_parameters(self, name: str) -> dict | None:
    """Return the JSON Schema registered for *name*, or None if unknown."""
    tool = self._tools.get(name)
    return tool["parameters"] if tool else None
```

### F3：`ToolRuntime` 新增 `_validate_args` + 错误格式化

在 `core/tool_runtime.py` 顶部 `import jsonschema`，新增一个模块级格式化函数和一个方法。
下面这段来自 prototype，直接编码了校验策略和错误消息格式：

```python
# core/tool_runtime.py (module level)
import jsonschema


def _format_validation_error(exc: jsonschema.exceptions.ValidationError) -> str:
    """Turn a jsonschema error into a short, LLM-friendly message.

    Uses the JSON pointer path (e.g. ``path``, ``$`` for the root) so the
    LLM knows exactly which argument is wrong, plus jsonschema's own
    human-readable message (e.g. ``'123' is not of type 'string'``).
    """
    loc = "/".join(str(p) for p in exc.path) if exc.path else "$"
    return f"Schema validation failed: {loc} — {exc.message}"
```

```python
# core/tool_runtime.py (ToolRuntime method)
async def _validate_args(self, name: str, arguments: dict) -> str | None:
    """Validate *arguments* against *name*'s registered JSON Schema.

    Returns an error message when the arguments are invalid, or ``None``
    when they pass (or the tool is unknown / has no schema — those paths
    fall through to the normal dispatch flow).
    """
    schema = self._registry.get_parameters(name)
    if not schema:
        return None
    try:
        await asyncio.to_thread(jsonschema.validate, instance=arguments, schema=schema)
    except jsonschema.exceptions.ValidationError as exc:
        return _format_validation_error(exc)
    return None
```

要点：

- **`asyncio.to_thread`**：`jsonschema.validate` 是同步的，放到后台线程跑，避免阻塞事件循环
  （决策：校验是 CPU-bound 的纯计算，不涉及 IO）。
- **跳过条件**：工具未知（`get_parameters` 返回 `None`）或 schema 为空 → 返回 `None`，
  交给 `dispatch()` 现有的 "Unknown tool" / 正常执行路径处理。
- **只捕获 `ValidationError`**：schema 本身若非法会抛 `SchemaError`（另一种异常），
  不吞掉——让它走 `dispatch()` 现有的兜底 `except Exception`，并写一条失败 trace。

### F4：`dispatch()` 集成位置与语义

校验放在 `dispatch()` 里 `retry_count = 0` 初始化之后、熔断检查**之前**。prototype 片段：

```python
# core/tool_runtime.py — inside ToolRuntime.dispatch(), right after `retry_count = 0`

        # Schema validation (Workflow F): reject malformed arguments before the
        # handler runs, so the LLM can correct them on a later round.
        validation_error = await self._validate_args(name, arguments)
        if validation_error is not None:
            success = False
            error_msg = validation_error
            result = json.dumps({"error": validation_error}, ensure_ascii=False)
            await self._persist_trace(
                call_id, name, arguments, result, start, success, error_msg,
                conversation_id, retry_count,
            )
            return result
```

语义决策（按优先级）：

1. **不调用 handler**：校验失败直接返回，工具 handler 完全不被触发，零副作用。
2. **写 trace**：`success=False`、`error_message=validation_error`、`retry_count=0`——与
   `dispatch()` 其他失败路径（unknown tool、handler 异常、超时）一致，保持 trace 完整。
3. **不重试**：校验是确定性的——同样的错误参数每次都会失败，重试无意义。因此校验放在
   retry 循环之外，且 `retry_count` 恒为 0。
4. **不触碰熔断器**：参数错误是 LLM 的责任，不是工具健康的信号。校验失败**不**调用
   `breaker.record_failure()`，否则一个健康工具会因 LLM 反复传错参数被误熔断。也因此校验
   放在熔断检查**之前**，从根本上不经过 breaker。
5. **错误信封复用现有约定**：返回 `json.dumps({"error": ...})`，`Agent._execute_tools_with_approval`
   会自动把它解包成 `tool_result` + `is_error=True` 回传给 LLM，无需改动 Agent。

### F5：宽松校验（不强制 `additionalProperties: false`）

使用 `jsonschema.validate` 的**默认**行为：只校验 `required` 是否存在、已声明 property 的
`type` / `enum` / `minimum` / `maximum` 等；**不拒绝未声明的额外字段**。

理由（重要，实现者必读）：

- `Agent._execute_tools_with_approval` 会给 `save_memory` / `search_memory` 的参数注入
  `character_id`（LLM 不知道、schema 里也没声明）。若强制 `additionalProperties: false`，
  这两个记忆工具会被自己的校验误杀。
- MCP 工具的 `inputSchema` 由外部 server 提供，未必声明 `additionalProperties: false`；
  强制收紧会比 server 本身更严格，可能拒绝 server 本可接受的参数。

因此本 spec **不改任何工具的 schema**，也不加 `additionalProperties: false`。未知字段会
透传给 handler（若 handler 不接受，则按现有 handler 异常路径处理，LLM 依然能自纠）。

### F6：错误消息格式

`Schema validation failed: <json路径> — <jsonschema消息>`。

示例（LLM 实际收到的 `tool_result.result`）：

- `Schema validation failed: path — '123' is not of type 'string'`
- `Schema validation failed: $ — 'content' is a required property`
- `Schema validation failed: importance — 15 is greater than the maximum of 10`
- `Schema validation failed: memory_type — 'random' is not one of ['user_fact', 'user_preference', 'important_event']`

### F7：MCP 工具自动覆盖

`McpManager._connect_one` 已经用 `parameters=tool.inputSchema` 注册 MCP 工具，因此
`get_parameters(name)` 能直接取到，校验对 MCP 工具自动生效。无 inputSchema 的 MCP 工具
（`inputSchema` 为 `None`）经 `not schema` 分支跳过。**无需改动 mcp_manager.py。**

### 数据模型 / API / Agent 变更

无。本 Workflow 不新增表、不改 API、不改 `Agent`。改动集中在 `tool_registry.py`、
`tool_runtime.py`、`requirements.txt`。

## Testing Decisions

### 测试原则

- 只测外部行为，不测实现细节：测"传错参数时 handler 是否被调用 / 返回的 error 是否可读 /
  trace 是否记为失败"，不测 `_validate_args` 内部是否用了 `to_thread`。
- 复用现有注入缝：`ToolRuntime(registry=隔离ToolRegistry)`，与
  `test_tool_runtime.py` / `test_tool_retry.py` 同款。
- 校验是纯逻辑，无需真实 LLM / 真实 MCP / 真实 HTTP。

### 测试缝

| 缝 | 位置 | 测试内容 | 参照 |
|---|---|---|---|
| S1 ToolRuntime 注入 | `ToolRuntime(registry=ToolRegistry())`，`enable_tracing=False, enable_sandbox=False` | 类型错误/缺必填/越界/enum 非法被拦截、handler 不被调用、错误消息格式、校验通过后执行、无 schema 跳过、未知工具跳过 | `test_tool_runtime.py` 的隔离 registry |
| S2 trace 注入 | `ToolRuntime` + 内存 SQLite `ToolTraceStore` | 校验失败写 `success=False` trace、`retry_count=0`、`error_message` 为校验原因 | `test_tool_retry.py` 的 `_make_runtime` |
| S3 CB 联动 | `ToolRuntime(enable_circuit_breaker=True, circuit_threshold=1)` | 校验失败**不**计数、不打开熔断器 | `test_circuit_breaker.py` 的 `FakeClock` |
| S4 Agent 全链路 | `Agent(llm_service=FakeLLMService)` + 注入 runtime | 错误参数 → 下一轮自纠 → 成功，走通 `agent.run` | `test_agent_tools.py` 的 `FakeLLMService` |

### 测试文件

新建 `backend/tests/test_tool_schema.py`，覆盖以下用例（每条对应一个用户故事场景）：

- 类型错误：`read_file(path=123)` → 拦截，handler 不调用，error 含 `not of type 'string'`。
- 缺必填：`write_file(path="x")`（无 `content`）→ 拦截，error 含 `required property`。
- 越界：`save_memory(content="...", importance=15)` → 拦截，error 含 `maximum of 10`。
- enum 非法：`save_memory(memory_type="random")` → 拦截，error 含 `not one of [...]`。
- 校验通过后执行：合法参数 → handler 正常执行，返回真实结果。
- 校验失败不调用 handler：用 `calls={"n": 0}` 计数验证 handler 未被触发。
- 未知字段被容忍（宽松）：`read_file(path="x", extra=1)` → 不因 `extra` 而失败（透传）。
- 无 schema 跳过：注册 `parameters=None` 或空 → 不校验，正常执行。
- 未知工具跳过：dispatch 未注册工具 → 走现有 "Unknown tool" 路径，不报 schema 错误。
- trace：校验失败写 `success=False`、`error_message` 含 `Schema validation failed`、
  `retry_count=0`。
- 不触发熔断：`enable_circuit_breaker=True, circuit_threshold=1`，连续校验失败后 breaker
  仍 CLOSED、工具不熔断。
- MCP inputSchema 生效：以 `{"type":"object","properties":{...},"required":[...]}` 注册
  `mcp__x__y` → 校验生效；`parameters=None` 注册 → 跳过。
- Agent 自纠集成（S4）：FakeLLM 第一轮发 `{"path": 123}`（错误参数）→ 收到校验错误
  `tool_result` → 第二轮发 `{"path": "/tmp/x"}` → 工具成功，最终 `done`。

### 回归

`backend/tests/test_tool_runtime.py`、`test_tool_retry.py`、`test_circuit_breaker.py`、
`core/test_agent_tools.py` 必须全绿——校验插入 `dispatch()` 后不得破坏现有行为。

## Out of Scope

- **Structured output / LLM 回复 schema 约束**（Workflow F 不做「校验 LLM 输出结构」）。
- **强制 `additionalProperties: false`**——见 F5，本 spec 明确不收紧额外字段。
- **校验失败重试**——校验是确定性的，不重试，直接回传错误。
- **自纠次数硬限制**——不自设上限，靠 Agent loop 的 5 轮预算兜底。
- **前端展示校验失败**——不新增 UI；仅通过 `tool_runs` trace（已有工具日志面板）可见。
- **改工具 schema**——本 spec 复用现有 `parameters`，不修改任何工具的 JSON Schema。

## Further Notes

### 改动清单

| 文件 | 改动 |
|------|------|
| `backend/requirements.txt` | 新增 `jsonschema>=4.20` |
| `backend/core/tool_registry.py` | 新增 `get_parameters(name) -> dict | None` |
| `backend/core/tool_runtime.py` | `import jsonschema`；新增 `_format_validation_error()`；新增 `ToolRuntime._validate_args()`；`dispatch()` 在 `retry_count = 0` 之后插入校验步骤 |
| `backend/tests/test_tool_schema.py` | **新建** — 校验单元测试 + Agent 自纠集成测试 |

### 验证命令

```bash
cd backend && pip install "jsonschema>=4.20"
cd backend && python -m pytest tests/test_tool_schema.py -v
cd backend && python -m pytest tests/test_tool_runtime.py tests/test_tool_retry.py tests/test_circuit_breaker.py tests/core/test_agent_tools.py -v
```

### 运行方式

校验对用户透明。验证方式：

```bash
# 故意让 LLM 传错参数，在工具调用日志中看到校验失败：
curl "http://localhost:8722/api/tool-runs?tool_name=read_file&success=false&limit=10"
# error_message 形如：Schema validation failed: path — '123' is not of type 'string'

# 正常对话中：LLM 第一次调 read_file 传了 int，收到校验错误后在下一轮自动改传 string，
# 工具成功执行，用户在聊天框看到最终回复（自纠全程无感）。
```
