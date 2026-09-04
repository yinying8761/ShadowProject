# 迭代计划 2 — Agent Harness 升级 Phase 2

> 日期：2026-08-06 | 状态：待实现

## 总览

三个 Workflow，按依赖链顺序执行：

```
E（Error Recovery）  →  F（Tool Schema Validation）  →  G（Token Counting & Cost）
     3-5天                   1-2天                              2-3天
```

| # | Workflow | 核心目标 | 预计工作量 |
|---|----------|---------|-----------|
| E | Error Recovery & Resilience | LLM API 重试 + 工具 retry + Circuit Breaker + MCP 恢复 | 3-5 天 |
| F | Tool Schema Validation | 工具参数 JSON Schema 校验 + LLM 自纠 | 1-2 天 |
| G | Token Counting & Cost | 预计算 + API usage 回传 + 会话/轮次级统计 | 2-3 天 |

**总计约 6-10 天（业余时间）。**

---

## Problem Statement

当前 ShadowProject 的 Agent Harness 缺少生产级容错和可观测性：

1. **无重试机制**：LLM API 网络抖动、429 限流、5xx 错误直接抛出异常，用户体验差。
2. **无熔断保护**：同一工具连续失败不会被拦截，浪费 LLM token（反复调坏工具）。
3. **无 schema 校验**：LLM 传错参数（如 `read_file(path=123)` 传 int）直接透传给 handler 崩溃，没有自纠机会。
4. **无 token 追踪**：只有粗略的 `estimate_tokens()` 估算，无法知道实际消耗、成本分布。

## Solution

分三步补齐：

- **E**：在 LLM API 和工具执行层分别加入重试策略 + Circuit Breaker + MCP 连接恢复。
- **F**：在 ToolRuntime.dispatch 前用工具注册时的 JSON Schema 校验参数，不合格不执行，让 LLM 自纠。
- **G**：发送前用各 provider tokenizer 预计算 prompt tokens；发送后从 API response 提取 `usage`；存入新表，前端面板展示。

---

## User Stories

1. 作为用户，当 LLM API 返回 429/5xx 时，后端自动重试最多 5 次（指数退避），我不需要手动重发消息。
2. 作为用户，当网络临时断开时，我不希望对话中断——后端重试后恢复正常，我只看到一条稍慢的回复。
3. 作为用户，当流式输出中途断开时，我希望至少已输出的部分不要丢失，能在聊天框里看到。
4. 作为用户，当一个工具连续失败 3 次后，我希望系统自动熔断该工具 60 秒，而不是每次都卡住等超时。
5. 作为用户，当我拒绝了工具执行（如写文件），我不希望这个"拒绝"被当成错误计入熔断统计。
6. 作为用户，当 MCP vision 服务器断开时，我希望 `see_screen` 自动降级到旧版 vision_service，不影响对话。
7. 作为用户，当 MCP 服务器恢复正常后，我希望系统自动重新连接它，不需要我重启应用。
8. 作为用户，当 LLM 传错了工具参数（如文件路径传了数字），我希望系统拦截并让 LLM 修正，而不是等到 handler 崩溃。
9. 作为用户，我希望看到每次对话消耗了多少 token，按轮次和会话维度查看。
10. 作为用户，我想在发送消息前就知道当前上下文大概占了多少 token、还剩多少额度。
11. 作为开发者，我需要每个 LLM 请求的实际 token 消耗（prompt + completion）被持久化记录，用于调试和优化。
12. 作为开发者，Circuit Breaker 的状态（关闭/打开/半开）应该在日志中可见。

---

## Implementation Decisions

### Workflow E：Error Recovery & Resilience

**LLM API 重试策略**：

- 5 次重试，**指数退避**：1s → 2s → 4s → 8s → 16s
- 可重试错误：HTTP 429、5xx、网络超时/连接错误
- 不可重试错误：HTTP 401（API Key 无效）、400（请求格式错误）
- 流式请求中断：**不重试**，保留已收到的文本内容并返回 `partial_error` 标记
- 不做 fallback provider（零额外配置）

**工具执行 retry**：

- 默认不重试。工具注册时新增 `retry_config` 可选参数：
  - `max_retries`（默认 0）
  - `retryable_exceptions`（默认空）
- 读文件、搜索等幂等操作：`max_retries=1`
- 写文件等非幂等操作：`max_retries=0`
- 超时（`asyncio.wait_for`）也走重试逻辑

**Circuit Breaker（填入 ToolRuntime D3 预留占位）**：

```
状态机：
  CLOSED → 正常执行，连续 failure_count >= 3 → OPEN
  OPEN   → 拒绝执行，60s 后 → HALF_OPEN
  HALF_OPEN → 允许 1 次试探
    → 成功 → CLOSED（重置计数器）
    → 失败 → OPEN（重新计时 60s）
```

- 熔断状态按工具名独立维护，内存字典存储
- 用户拒绝（`denied: True`）不计入 failure_count（当前架构已天然区分——拒绝路径不经过 ToolRuntime.dispatch）
- 熔断状态变更打印日志：`[CircuitBreaker] write_file OPEN (3 consecutive failures)`

**MCP 连接恢复**：

- 单个 MCP server 断开不影响其他 server 的工具调用
- 与熔断器联动：MCP 工具连续失败达到阈值也会熔断
- 定期健康检查（ping 或 list_tools）：单次检查失败 → 尝试重连 → 成功则关闭熔断、重新注册工具

**改动范围**：

- LLM retry wrapper（新增模块）
- ToolRuntime 的 `dispatch()` 加入 CB 检查
- MCP 健康检查 + 自动重连逻辑
- `tool_runs` 表新增 `retry_count` 字段

### Workflow F：Tool Schema Validation

**参数校验策略**：

- `ToolRuntime.dispatch()` 中，在 `handler(**arguments)` 之前插入 schema 校验步骤
- 使用工具注册时的 `parameters`（JSON Schema），调用 jsonschema 库校验 `arguments`
- 校验失败 → 不执行工具 → 返回 `{"error": "Schema validation failed: <详细原因>"}`
- 该错误通过 `tool_result` 回传给 LLM，LLM 自然修正参数后在下一轮重调
- 不限制自纠次数，由 Agent loop 的 5 轮预算自然兜底（校验失败不算完整一轮，约消耗 0.5 轮预算）
- 校验逻辑异步执行（`asyncio.to_thread`），避免阻塞事件循环

**注册时的 schema 来源**：

- 所有工具在 `register_tools()` 中已有 `parameters`（JSON Schema），直接复用
- MCP 工具：使用 MCP server 返回的 `inputSchema`，同样已有

**改动范围**：

- ToolRuntime 添加 `_validate_args()` 方法
- 新的依赖：`jsonschema`（Python 包）

### Workflow G：Token Counting & Cost

**两阶段计数**：

阶段 1 — **预计算**（请求发送前）：
- 用各 provider 的 tokenizer 计算 prompt messages 的 token 数
- DeepSeek / Qwen / OpenAI 兼容：使用 `tiktoken`（OpenAI tokenizer），偏差 <5%
- Anthropic：Anthropic SDK 内置 `count_tokens()`
- 结果用于：日志输出、上下文窗口判断（是否需 compact）

阶段 2 — **API usage 回传**（请求完成后）：
- 从 API response 提取 `usage.prompt_tokens` / `usage.completion_tokens` / `usage.total_tokens`
- 写入新表 `llm_usage`，含 `conversation_id`、`round_num`、`model`、`prompt_tokens`、`completion_tokens`
- 预计算的 `estimated_tokens` 也写入，用于和实际值对比

**数据模型**：

| 表 | 列 | 说明 |
|---|------|------|
| `llm_usage` | `id` (UUID PK) | |
| | `conversation_id` | 关联会话 |
| | `round_num` | Agent loop 中的轮次序号 |
| | `model` | 实际使用的模型名 |
| | `prompt_tokens` | API 回传的 prompt token 数 |
| | `completion_tokens` | API 回传的 completion token 数 |
| | `total_tokens` | prompt + completion |
| | `estimated_prompt_tokens` | 预计算值（用于对比） |
| | `created_at` | 时间戳 |

**前端展示**：

- 设置面板新增"用量统计"标签页
- 当前对话的 token 消耗（实时刷新）
- 按工具调用的 token 消耗分布（可选，后续迭代）

**改动范围**：

- 新增 `llm_usage` 模型 + 表
- `llm_service.py` 返回 token usage 信息
- `agent.py` 在每轮 LLM 调用后写入 usage 记录
- `chat.py` 结束后可汇总本次对话的 token 消耗

**不做**：金额换算（不同 provider 价格不同，纯记 token 数）。

---

## Testing Decisions

### 测试原则

- 只测外部行为，不测实现细节
- 复用现有 FakeLLMService 模式 —— 注入模拟 LLM 响应来测试重试/CB/token 逻辑
- 熔断、重试、schema 校验等都可单元测试 —— 不依赖真实 LLM API

### Seam 设计

| Seam | 位置 | 用途 |
|------|------|------|
| S1 — LLM service 注入 | `Agent(..., llm_service=FakeLLM)` | 测试重试次数、流式错误恢复 |
| S2 — ToolRuntime 注入 | `Agent(..., tool_registry=FakeRuntime)` | 测试 CB 状态转换、schema 校验 |
| S3 — HTTP API | `TestClient` + 内存 SQLite | 测试 token usage 查询端点 |
| S4 — 独立 CB 单元 | `CircuitBreaker` 类 | 纯逻辑测试，无需 Mock |

### 测试文件

| 文件 | 覆盖 |
|------|------|
| `backend/tests/test_llm_retry.py` | E: LLM API 重试（可重试/不可重试错误、指数退避、流式错误保留） |
| `backend/tests/test_circuit_breaker.py` | E: CB 状态机（CLOSED→OPEN→HALF_OPEN、拒绝不计入、超时计入） |
| `backend/tests/test_tool_schema.py` | F: schema 校验（类型错误、缺必填、unknown 字段、校验后执行成功路径） |
| `backend/tests/test_token_tracking.py` | G: Token 计数（预计算对比、usage 写入、API 端点查询） |

### 测试用例

每条测试覆盖一个明确的用户故事场景，优先用 FakeLLMService / FakeMcpDispatch 等现有测试工具。

---

## Out of Scope

- **Structured output / LLM 回复 schema 约束**（Workflow F 不做 B）
- **金额换算**（纯记 token 数）
- **Fallback provider 切换**（仅重试，不切 provider）
- **前端"用量统计"面板的 tool 分布图表**（预留接口，后续迭代）
- **Token 预算硬限制**（预计算仅用于日志和展示，不自动中断请求）

---

## 运行方式

```bash
# E: Circuit Breaker 状态（日志可见）
# 工具连续失败 3 次后在日志中看到：
#   [CircuitBreaker] search_files OPEN (3 consecutive failures, retry in 60s)
#   [CircuitBreaker] search_files HALF_OPEN (probing...)
#   [CircuitBreaker] search_files CLOSED (probe succeeded)

# F: Schema 校验（透明，校验失败时 LLM 自动修正参数）

# G: 查看 token usage
curl GET /api/tool-runs?conversation_id=xxx  # 已有
curl GET /api/token-usage?conversation_id=xxx  # 新增
```
