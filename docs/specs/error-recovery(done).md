# Error Recovery & Resilience — LLM 重试 + 工具 retry + Circuit Breaker + MCP 恢复

> 日期：2026-08-14 | 状态：待实现 | 父迭代：`iteration-plan2.md` Workflow E

## Problem Statement

当前 ShadowProject 的 Agent Harness 缺少生产级容错能力，任何一次临时故障都会直接暴露给用户：

1. **LLM 调用无重试**：网络抖动、429 限流、5xx 服务端错误会直接抛异常，流式对话中断，用户只能手动重发。
2. **工具无熔断保护**：同一个工具连续失败不会被拦截，Agent loop 会反复调用坏工具，浪费 LLM token 和用户等待时间。
3. **工具无重试**：读文件、搜索等幂等操作遇到瞬时 IO/网络错误就直接失败，本可自动重试一次成功。
4. **MCP 连接无自愈**：MCP vision 服务器断开后不会自动重连，需要重启应用才能恢复 `see_screen`。

（schema 校验和 token 计数分别是 Workflow F/G，不在本 spec 范围内。）

## Solution

分四个相互解耦的改动补齐容错能力：

1. **E1 LLM API 重试** — 在 LLM 调用外层加指数退避重试，区分可重试/不可重试错误；流式中途断开保留已输出文本。
2. **E2 工具 retry** — 工具注册时可选声明 `retry_config`，幂等工具重试一次、非幂等不重试，超时也走重试。
3. **E3 Circuit Breaker** — 实装 ToolRuntime 的 D3 预留位：按工具名独立熔断，达到阈值拒绝执行，冷却后试探恢复。
4. **E4 MCP 恢复** — 周期性健康检查 + 自动重连，与熔断器联动。

对 Agent 和前端透明：`ToolRuntime` 对外接口不变，新增能力全部收敛在 `dispatch()` 内部和 LLM 调用封装层。

## User Stories

1. 作为用户，当 LLM API 返回 429 限流或 5xx 服务端错误时，我希望后端自动按指数退避重试，而不是让我看到错误或手动重发。
2. 作为用户，当网络临时抖动导致请求失败时，我希望对话不中断——后端重试后恢复，我只感觉到回复稍慢了一点。
3. 作为用户，当流式输出中途断开时，我希望已经输出的文字不丢失，能在聊天框里看到，并知道这次回复不完整。
4. 作为用户，当 API Key 无效（401）或请求格式错误（400）时，我不希望系统无意义地重试多次——应快速失败并给出清晰错误。
5. 作为用户，当一个工具连续失败达到阈值（默认 5 次）后，我希望系统自动熔断该工具一段时间，而不是每次都卡住等超时。
6. 作为用户，当熔断的工具进入半开试探并且试探成功时，我希望它自动恢复，不需要我重启应用。
7. 作为用户，当我拒绝了某个工具的执行（如写文件），我不希望这个"拒绝"被当成一次失败计入熔断统计。
8. 作为用户，当一个工具熔断时，我不希望其他工具受影响——只有那个坏工具被暂停。
9. 作为用户，当读文件、搜索等幂等操作遇到瞬时 IO/网络错误时，我希望系统自动重试一次，而不是直接失败。
10. 作为用户，当写文件等非幂等操作失败时，我不希望系统盲目重试（可能造成重复写入）——应直接返回失败让我知晓。
11. 作为用户，当 MCP vision 服务器断开时，我希望 `see_screen` 自动降级到旧版视觉方案，不影响对话。
12. 作为用户，当 MCP 服务器恢复正常后，我希望系统自动重新连接并恢复它的工具，不需要重启应用。
13. 作为用户，当某个 MCP 服务器断开时，我不希望其他 MCP 服务器或本地工具受影响。
14. 作为开发者，我希望每次工具调用实际发生的重试次数被持久化到 `tool_runs`，用于排查哪些工具经常失败。
15. 作为开发者，我希望 Circuit Breaker 的状态转换（关闭/打开/半开）在日志中可见，方便定位问题。
16. 作为开发者，我希望重试、熔断都能用假 LLM/假工具单元测试，不依赖真实 LLM API 或真实 MCP 服务器。

## Implementation Decisions

### E1：LLM API 重试

- 新增一个异步 retry 封装（指数退避），包裹 `LLMService` 的请求调用。
- 默认最多重试 5 次，退避间隔 1s → 2s → 4s → 8s → 16s（2 的幂）。
- **可重试错误**：HTTP 429（限流）、5xx（服务端错误）、网络超时/连接错误（连接重置、超时、DNS 失败等）。
- **不可重试错误**：HTTP 401（API Key 无效）、400（请求格式错误）—— 立即失败不重试。
- 错误分类依据：SDK 异常对象上的 `status_code`（若有）优先；无状态码的按异常类型归为网络瞬态错误。
- 非流式 `chat_sync`：整个请求按退避重试，耗尽后抛错。
- 流式 `stream_chat`：
  - **连接建立阶段失败**（尚未产出 token）：按退避重试整个请求。
  - **流已开始、中途断开**：**不重试**（重发会重复已输出的 token 且语义错乱），保留已收到的文本，结束流并回传错误事件；Agent 层复用现有 `partial_error` 机制（已保存部分文本 + `partial_error: True`）。
  - 重试耗尽：yield `{"type": "error", "message": ...}`。
- 不做 fallback provider（零额外配置，只重试不切 provider）。

### E2：工具 retry

- `ToolRuntime.register()` 新增可选参数 `retry_config: dict | None`，含：
  - `max_retries: int`（默认 0，不重试）
  - `retryable_exceptions: tuple[type, ...]`（可选；未指定时用内置瞬态异常集合：`asyncio.TimeoutError` + 常见网络/IO 异常）。sandbox 超时始终视为可重试。
- 幂等工具（read_file、search_files、list_directory、search_memory、fetch_url、research、get_current_time）注册时 `max_retries=1`。
- 非幂等工具（write_file、save_memory）`max_retries=0`（避免重复写入副作用）。
- 仅当 handler **抛出异常/超时**才重试；handler 返回 `{"error": ...}` JSON（如 "Unknown tool"）属于确定性失败，不重试。
- 重试退避复用 E1 的 retry 封装（间隔更短，如 0.5s → 1s）。

### E3：Circuit Breaker（实装 D3 占位）

状态机（按工具名独立，内存字典存储）：

```
CLOSED     --连续 failure_count >= circuit_threshold--> OPEN
OPEN       --冷却 circuit_open_sec 后--> HALF_OPEN
HALF_OPEN  --允许 1 次试探--
    试探成功 → CLOSED（重置计数）
    试探失败 → OPEN（重新计时冷却）
```

- `circuit_threshold` 默认 **5**（沿用 D3 占位默认，用户已确认）。
- `circuit_open_sec` 默认 **60**（新增参数，可配置）。
- `enable_circuit_breaker` 构造参数默认保持 `False`（向后兼容，测试与 eval 不受影响）；生产接线在 `main.py` 中显式开启 `True`。
- OPEN 状态：`dispatch()` 短路拒绝，不调用 handler，返回 `{"error": "Circuit breaker open for <tool>: ..."}`，trace 记 `success=False`。
- HALF_OPEN：仅放行 1 次试探调用，其余仍拒绝。
- 用户拒绝（`denied: True`）不计入 failure_count —— 现有架构天然区分（拒绝路径在 Agent `_execute_tools_with_approval` 提前返回，不经过 `ToolRuntime.dispatch`）。
- 状态变更打印日志：`[CircuitBreaker] <tool> OPEN (5 consecutive failures)` / `HALF_OPEN (probing...)` / `CLOSED (probe succeeded)`。

### E4：MCP 恢复

- 单个 MCP server 断开不影响其他 server（沿用现有 `connect_all` 的逐 server 隔离）。
- 周期性健康检查（后台 asyncio 任务，间隔默认 30s，可配置）：用 `session.list_tools()` 探测连通性。
- 单次检查失败 → 对该 server 尝试重连（复用现有 `_connect_one`）。
- 重连成功 → 关闭该 server 全部工具的熔断状态 + 重新 `list_tools` 并重注册工具。
- 与熔断器联动：MCP 工具的连续失败同样计入其各自 CircuitBreaker（复用 E3，无需单独机制）。
- `see_screen` 的旧版 vision_service 降级保持不变（MCP vision 不可用/熔断时走 fallback）。

### 数据模型变更

`tool_runs` 表新增 `retry_count`（Integer，默认 0），记录该次工具调用实际发生的重试次数：

| 列 | 类型 | 说明 |
|---|---|---|
| `retry_count` | Integer, default 0 | 本次 dispatch 实际重试次数（0 = 未重试） |

- 通过 `ADDITIVE_MIGRATIONS` 增量迁移（无需重建表）。
- `GET /api/tool-runs` 响应中 `runs[]` 每项增加 `retry_count` 字段（沿用现有端点与筛选/分页语义）。

### 生产接线

- `main.py` 中 `ToolRuntime` 以 `enable_circuit_breaker=True`（阈值 5、冷却 60s）实例化。
- 幂等工具在 `register_tools()` 里传 `retry_config`；MCP 健康检查任务在 lifespan 启动。

## Testing Decisions

### 测试原则

- 只测外部行为，不测实现细节：测"重试了几次""熔断后是否拒绝执行""部分文本是否保留"，不测 retry 封装内部循环写法。
- 复用现有注入缝：`FakeLLMService`、隔离 `ToolRegistry`、内存 SQLite、mock MCP session。
- 熔断、重试均可用假对象单元测试，不依赖真实 LLM API / 真实 MCP server。

### 测试缝

| 缝 | 位置 | 测试内容 | 参照 |
|---|---|---|---|
| S1 LLM 注入 | `Agent(llm_service=FakeLLM)` + `LLMService` 注入假 SDK client | 流式错误保留文本 + `partial_error`；重试次数、可/不可重试分类、指数退避、耗尽 | `test_agent_tools.py` 的 `FakeLLMService`；`test_mcp_manager.py` 的 `patch.object`/`AsyncMock` |
| S2 ToolRuntime 注入 | `ToolRuntime(registry=隔离ToolRegistry)` | 工具 retry（重试后成功/耗尽、超时重试）、CB 集成（OPEN 拒绝、HALF_OPEN 试探）、`retry_count` 落表 | `test_tool_runtime.py` 的隔离 registry + 内存 SQLite |
| S3 独立 CircuitBreaker | `CircuitBreaker` 纯类 | 状态机 CLOSED→OPEN→HALF_OPEN→CLOSED/OPEN、拒绝不计入、半开单次试探 | `test_tool_runtime.py` 的 `TestD3Placeholders`（纯逻辑无 Mock） |
| S4 McpManager 注入 | `McpManager(registry)` + `patch.object(manager, "_connect_stdio", fake)` | 健康检查失败→重连→成功→关 CB + 重注册；单 server 断开不影响其他 | `test_mcp_manager.py` 的 mock session |

### 测试文件

- `backend/tests/test_llm_retry.py` — S1：可/不可重试错误分类、指数退避次数、重试耗尽、流式部分文本保留。
- `backend/tests/test_circuit_breaker.py` — S3：状态机纯逻辑 + S2 的 CB 集成（dispatch 短路拒绝、半开试探、拒绝不计入）。
- 工具 retry 用例并入现有 `backend/tests/test_tool_runtime.py`（或 `test_circuit_breaker.py`，按实现归组）。
- MCP 恢复用例并入现有 `backend/tests/test_mcp_manager.py`（S4）。

### 测试用例（覆盖用户故事）

每条测试对应一个明确的用户故事场景，优先用 FakeLLMService / mock MCP session 等现有测试工具：429/5xx 重试成功、401/400 不重试、重试耗尽后错误、流中断保留部分文本、幂等工具重试一次后成功、非幂等不重试、超时重试、连续 5 次失败熔断、冷却后半开试探成功恢复、试探失败重新冷却、拒绝不计入计数、OPEN 时 dispatch 短路、`retry_count` 写入、MCP 健康检查重连成功、MCP 单 server 隔离。

## Out of Scope

- **Schema 校验（Workflow F）** — 工具参数 JSON Schema 校验与 LLM 自纠。
- **Token 计数与成本（Workflow G）** — 预计算、usage 回传、`llm_usage` 表、前端用量面板。
- **Fallback provider 切换** — 只重试当前 provider，不在 provider 间切换。
- **rate_limit 限流** — ToolRuntime 的 `enable_rate_limit` 占位继续保持不实装（非本 Workflow 目标）。
- **熔断状态持久化** — 熔断状态只存内存，进程重启后清零（可接受，后续可加）。
- **熔断 UI 展示** — 前端不新增熔断状态面板；仅日志可见。
- **金额换算** — 纯记 token/重试次数，不涉及成本金额。

## Further Notes

### 改动清单

| 文件 | 改动 |
|------|------|
| `backend/services/retry.py`（新建） | 指数退避 retry 封装 + 可重试错误分类 |
| `backend/services/llm_service.py` | `chat_sync` + `stream_chat` 连接阶段接入 retry；流式中断保留部分文本 |
| `backend/core/circuit_breaker.py`（新建） | `CircuitBreaker` 类（状态机） |
| `backend/core/tool_runtime.py` | `dispatch()` 接入 CB 检查 + 工具 retry + `retry_count`；`register()` 接受 `retry_config`；D3 占位参数实装（新增 `circuit_open_sec`） |
| `backend/models/tool_run.py` | 新增 `retry_count` 列 |
| `backend/services/tool_trace_store.py` | `save()` 记录 `retry_count` |
| `backend/database.py` | `ADDITIVE_MIGRATIONS` 新增 `tool_runs.retry_count` |
| `backend/services/mcp_manager.py` | 健康检查 + 自动重连 + 熔断联动 |
| `backend/main.py` | ToolRuntime 以 `enable_circuit_breaker=True` 接线；幂等工具传 `retry_config`；lifespan 启动 MCP 健康检查任务 |
| `backend/api/tool_logs.py` | `_run_to_dict` 增加 `retry_count` |

### 验证命令

```bash
cd backend && python -m pytest tests/test_llm_retry.py tests/test_circuit_breaker.py -v
cd backend && python -m pytest tests/test_tool_runtime.py tests/test_mcp_manager.py -v
```

### 运行方式

```bash
# Circuit Breaker 状态（日志可见）
# 工具连续失败 5 次后在日志中看到：
#   [CircuitBreaker] search_files OPEN (5 consecutive failures, retry in 60s)
#   [CircuitBreaker] search_files HALF_OPEN (probing...)
#   [CircuitBreaker] search_files CLOSED (probe succeeded)

# 查看工具调用日志（含 retry_count）
curl "http://localhost:8722/api/tool-runs?tool_name=search_files&limit=50"
```
