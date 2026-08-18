# Token Counting & Cost — token 预计算 + usage 回传 + 会话/轮次级统计

> 日期：2026-08-17 | 状态：待实现 | 父迭代：`iteration-plan2.md` Workflow G

## Problem Statement

当前 ShadowProject 无法知道每次对话实际消耗了多少 token：

1. **只有粗略估算**：`ConversationManager.estimate_tokens()` 用"每字符≈0.25 token"的字符级估算，
   误差大，且只用于上下文裁剪，从不持久化。
2. **不知道实际消耗**：LLM API 响应里明明带有权威的 `usage`（prompt/completion token 数），
   但被 `llm_service` 直接丢弃。
3. **无法回答"花了多少"**：用户和开发者都看不到单次对话、单个轮次消耗了多少 token，
   无法评估成本、优化上下文、诊断上下文是否过大。
4. **没有对比基准**：发送前预估多少、实际回传多少，二者偏差无法追踪，无法判断 tokenizer
   预计算是否靠谱。

（LLM 重试/熔断是 Workflow E，schema 校验是 Workflow F，均已完成。）

## Solution

分两阶段补齐 token 计数，端到端（后端 + 前端）：

1. **阶段 1 预计算**（发送前）：用 provider 对应的 tokenizer 估算 prompt messages 的 token 数，
   写入日志与 `llm_usage.estimated_prompt_tokens`，用于和实际值对比。
2. **阶段 2 usage 回传**（完成后）：从 API 流式响应的最终 usage 提取
   `prompt_tokens` / `completion_tokens` / `total_tokens`，写入新表 `llm_usage`（按会话 + 轮次）。
3. **查询与展示**：新增 `GET /api/token-usage` 端点；前端设置面板新增「用量统计」标签页，
   展示当前会话的累计消耗与逐轮明细。

已确认的约束：

- 预计算**仅用于日志/展示与对比**，**不**触发 compact、**不**中断请求
  （沿用现有 `msg_count>30` 的 compact 触发）。
- 只统计 **Agent 主循环每轮**（`stream_chat`）；后台摘要/记忆提取（`chat_sync`）不在本次范围。
- **不做金额换算**（不同 provider 价格不同，纯记 token 数）。

## User Stories

1. 作为用户，我想在设置里看到当前对话累计消耗了多少 token（prompt/completion/总数），
   so that 我能了解使用量。
2. 作为用户，我想看到每次 LLM 调用（轮次）分别消耗了多少 token，so that 我能定位"哪一轮最费 token"。
3. 作为用户，我想看到发送前预估 token 数与实际回传 token 数的对比，so that 我能判断估算是否靠谱。
4. 作为开发者，我希望每次 Agent 循环轮次的实际 token 消耗被持久化到数据库，so that 我能调试与优化。
5. 作为开发者，我希望预计算在请求发送前完成并打印到日志，so that 我能观察上下文是否过大。
6. 作为开发者，我希望 OpenAI 兼容 provider（DeepSeek/Qwen/Zhipu/Moonshot/custom）用 tiktoken 估算，
   Anthropic 用 SDK count_tokens 估算，so that 估算贴近各 provider 真实分词。
7. 作为用户，当某个 tokenizer 不可用时（如 tiktoken 离线下载失败），我希望系统优雅降级为字符估算，
   so that 不会崩溃。
8. 作为开发者，我希望流式响应结束后能拿到权威 usage，且流式过程中不额外阻塞，so that 对话延迟不受影响。
9. 作为开发者，我希望 token 追踪不破坏现有 `LLMService`/`Agent` 的调用方，so that 新增能力向后兼容。
10. 作为开发者，我希望能用假 LLM / 假 tokenizer / 内存 SQLite 单元测试 token 追踪，
    so that 不依赖真实 LLM API 或 tiktoken 下载。
11. 作为用户，当对话还没产生任何轮次时（无 usage 记录），用量页应显示空态而不是报错。
12. 作为用户，切换会话时用量页应刷新为当前会话的数据。
13. 作为用户，一轮对话结束（收到 `done`）后，用量页应自动更新为最新数据，无需手动刷新。

## Implementation Decisions

### G1：tokenizer 预计算

- 新增 token 计数模块，提供 OpenAI 兼容路径的纯函数估算接口（tiktoken `cl100k_base` 编码，
  对每条 message 的 `content` 以及 assistant 的 `tool_calls` 参数 JSON 编码求和）。
- **OpenAI 兼容**（`sdk_type != "anthropic"`，即 deepseek/qwen/zhipu/moonshot/custom/openai）：
  用 `tiktoken` 的 `cl100k_base`。
- **Anthropic**：用 Anthropic SDK 内置 `count_tokens`（返回 `input_tokens`），由 `LLMService`
  持有 client、直接调用。
- **懒加载 + 降级**：tiktoken BPE 编码首次加载可能联网；加载失败或 `tiktoken` 不可用时，
  回退到字符级估算（复用 `ConversationManager.estimate_tokens` 同款启发式），保证不崩。
- 预计算用途：写入 `llm_usage.estimated_prompt_tokens` + 打印日志
  `[Token] estimated ~N tokens for round R`。**不**用于触发 compact、**不**中断请求。
- 依赖：`requirements.txt` 新增 `tiktoken>=0.7`。
- `LLMService` 新增异步方法 `estimate_prompt_tokens(messages) -> int`：内部解析当前
  `sdk_type`/`model` 并分发到 tiktoken / SDK count_tokens；OpenAI 兼容路径用
  `asyncio.to_thread` 包裹以避免首次加载阻塞事件循环。Agent 无需感知 provider 细节。

### G2：usage 提取（流式）

- `stream_chat` 在流结束后额外 yield 一个 `{"type": "usage", "model": ..., "prompt_tokens": ...,
  "completion_tokens": ..., "total_tokens": ...}` 事件，作为该次流式调用的最后一个事件。
  向后兼容：现有消费者（Agent/SubAgent）对未知事件类型静默忽略。
- **OpenAI 流式**：请求加 `stream_options={"include_usage": True}`，在流中捕获最后一个 chunk 的
  `usage`（此时 `choices` 为空）。
- **Anthropic 流式**：用 `stream.get_final_message().usage` 的 `input_tokens`/`output_tokens`
  （`prompt_tokens = input_tokens`，`completion_tokens = output_tokens`）。
- 流中途出错（走既有 `error` 事件路径）时不产出 usage——该轮次不落库（best-effort）。
- `chat_sync` 保持返回 `str` 不变（后台摘要/记忆提取不在本次统计范围）。

### G3：llm_usage 落表 + 写入 + API

**数据模型**（新表，`create_all` 自动建表，无需 additive migration）：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | String(36) PK | UUID |
| `conversation_id` | String(36), index | 关联会话 |
| `round_num` | Integer | Agent loop 轮次（0 起） |
| `model` | String(100) | 实际模型名 |
| `prompt_tokens` | Integer | API 回传 prompt token |
| `completion_tokens` | Integer | API 回传 completion token |
| `total_tokens` | Integer | prompt + completion |
| `estimated_prompt_tokens` | Integer | 预计算值（对比用） |
| `created_at` | DateTime | 时间戳 |

- 新增 `LLMUsageStore`（`session_factory` 可注入，同 `ToolTraceStore` 模式），`save(...)` 异步写入。
- `Agent` 构造函数新增 `usage_store` 注入缝（默认懒创建 `LLMUsageStore()`）。每轮
  `stream_chat` 前调 `estimate_prompt_tokens(messages)` 得预估；流结束后若收到 `usage` 事件，
  `await usage_store.save(conversation_id, round_num, model, prompt_tokens, completion_tokens,
  total_tokens, estimated_prompt_tokens)`。
- 新增 `GET /api/token-usage?conversation_id=xxx&limit=50&offset=0`，响应：

```
{
  "total": N,
  "usage": [
    { "id", "conversation_id", "round_num", "model", "prompt_tokens",
      "completion_tokens", "total_tokens", "estimated_prompt_tokens", "created_at" }
  ],
  "summary": { "rounds": N, "prompt_tokens": X, "completion_tokens": Y, "total_tokens": Z }
}
```

`summary` 为该会话全部记录的聚合求和；`usage` 按 `created_at` 倒序（最新轮次在前）。

### G4：前端「用量统计」标签页

- 设置面板新增「用量统计」标签：`SettingsNav` 加 `usage` 项、`SettingsPanel` 加 `usage` 分支、
  新增 `UsagePanel` 组件。
- `api.ts` 加 `fetchTokenUsage(conversationId)`；`types` 加 `TokenUsageEntry` / `TokenUsageSummary`。
- 展示：当前会话的 `summary`（prompt/completion/总数）+ 逐轮明细（轮次、模型、
  prompt/completion/total、预估 vs 实际）。
- 刷新时机（已确认）：打开标签页时、切换会话时、收到 `done`（一轮结束）时——通过观察
  `chatStore.streaming` 从 true→false 触发重新拉取；纯 REST，不新增 WS 事件。
- **不做**：按工具调用的 token 分布图表（预留接口，后续迭代）。

## Testing Decisions

### 测试原则

- 只测外部行为：测"usage 事件是否正确 yield"、"usage 是否落库"、"API 是否返回聚合"，
  不测 tokenizer 内部循环写法。
- 复用现有注入缝：假 SDK client（`test_llm_retry.py` 的 `FakeStream`/`FakeChunk`）、假 tokenizer、
  内存 SQLite、`FakeLLMService`（`test_agent_tools.py`）。
- 不依赖真实 LLM API / 真实 tiktoken 下载。

### 测试缝

| 缝 | 位置 | 测试内容 | 参照 |
|---|---|---|---|
| S1 tokenizer 纯函数 | `estimate_*_tokens(messages, ...)` + 注入假 encoder | 确定性输出、anthropic 走 count_tokens、tiktoken 不可用降级 | `test_relative_dates.py` 的 pure func |
| S2 LLM usage 提取 | `LLMService(clients=假client)` | `stream_chat` 末尾 yield `usage`（OpenAI/Anthropic 各自计数来源）、流中断不 yield usage | `test_llm_retry.py` 的 `FakeStream`/`FakeChunk` |
| S3 Agent 落库 | `Agent(llm_service=FakeLLM→usage, usage_store=内存store)` | 每轮写一条 `llm_usage`（round_num/conversation_id/estimated 正确） | `test_agent_tools.py` 的 `FakeLLMService` + 内存 SQLite |
| S4 HTTP API | `TestClient` + 内存 SQLite | `GET /api/token-usage` 逐轮返回 + `summary` 聚合 + 空态 | `test_tool_runtime.py` 的 `tool_logs_client` |

### 测试文件

新建 `backend/tests/test_token_tracking.py`，覆盖：tokenizer 估算（含降级）、`stream_chat`
usage 事件（OpenAI/Anthropic/流中断）、Agent 每轮落库（round_num/conversation_id/estimated）、
`GET /api/token-usage`（返回 + 聚合 + 空态）。

### 回归

`test_llm_retry.py`（llm_service 改动）、`test_agent_tools.py` / `core/test_agent_tools.py`
（Agent 改动）必须全绿。

## Out of Scope

- **金额换算**（纯记 token 数，不乘价格）。
- **Token 预算硬限制 / 自动中断请求**（预计算仅日志/展示/对比）。
- **用预计算触发 compact**（沿用现有 `msg_count>30` 触发）。
- **后台摘要/记忆提取（`chat_sync`）的 token 统计**（仅统计 Agent 主循环）。
- **前端 tool 分布图表**（预留接口，后续迭代）。
- **按 provider/模型的跨会话聚合报表**（本次只按单会话查询）。

## Further Notes

### 改动清单

| 文件 | 改动 |
|---|---|
| `backend/requirements.txt` | 新增 `tiktoken>=0.7` |
| `backend/services/token_counter.py`（新建） | OpenAI 兼容 tokenizer 估算 + 懒加载/降级 |
| `backend/services/llm_service.py` | `estimate_prompt_tokens()`；`stream_chat` yield `usage` 事件（OpenAI `stream_options.include_usage`、Anthropic `get_final_message().usage`） |
| `backend/models/llm_usage.py`（新建） | `LLMUsage` 模型 |
| `backend/services/usage_store.py`（新建） | `LLMUsageStore` 异步写入 |
| `backend/core/agent.py` | `usage_store` 注入缝；每轮预计算 + 落库 |
| `backend/database.py` | `init_db()` 导入 `llm_usage` 建表 |
| `backend/api/token_usage.py`（新建） | `GET /api/token-usage` |
| `backend/main.py` | 注册 token_usage 路由 |
| `frontend/src/components/settings/UsagePanel.tsx`（新建） | 用量统计标签页 |
| `frontend/src/components/settings/SettingsNav.tsx` | 加 `usage` 项 |
| `frontend/src/components/settings/SettingsPanel.tsx` | 加 `usage` 分支 |
| `frontend/src/services/api.ts` | `fetchTokenUsage()` |
| `frontend/src/types/index.ts` | `TokenUsageEntry` / `TokenUsageSummary` |
| `backend/tests/test_token_tracking.py`（新建） | 测试 |

### 验证命令

```bash
cd backend && python -m pytest tests/test_token_tracking.py -v
cd backend && python -m pytest tests/test_llm_retry.py tests/test_agent_tools.py tests/core/test_agent_tools.py -v
```

### 运行方式

```bash
# 查看某会话的 token 用量
curl "http://localhost:8722/api/token-usage?conversation_id=xxx"

# 日志中看到每轮预估：
#   [Token] estimated ~3200 tokens for round 0
```
