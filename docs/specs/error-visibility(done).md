# 错误可见性 — 重试进度瞬态提示 + 失败内联错误气泡 (Workflow I)

> 日期：2026-09-09 | 状态：待实现 | 来源：`docs/specs/iteration-plan3.md` Workflow I

## Problem Statement

网络出错时用户完全不知情：LLM 层的 5 次指数退避重试（1+2+4+8+16=31s）全程静默，用户体感是「卡了半天然后没有回复」，不知道发生了什么、该不该重发；重试全部失败后，错误只打到浏览器控制台，聊天界面毫无反应。用户不知道是网络断了、接口超时，还是应用坏了。

## Solution

两条通道，把错误从「黑盒」变成「看得见」：

- **重试期间（瞬态）**：聊天区显示「网络连接失败，正在重试 (1/5)」之类的进度提示，重试一结束（成功或彻底失败）立即消失——告诉用户应用在努力，而不是卡死。
- **全部失败（持久）**：聊天流里出现内联红色错误气泡，人话主文案（网络连接失败 / 响应超时 / 出错了）+ 可展开的原始错误信息，手动关闭。刷新即清，不写数据库。

## User Stories

1. 作为用户，当网络抖动导致请求重试时，我想看到「正在重试 (n/5)」的瞬态提示，so that 我知道应用在努力而不是卡死。
2. 作为用户，重试成功后提示应立即消失，so that 正常的回复流不被错误信息打扰。
3. 作为用户，重试全部失败时，聊天界面应明确告诉我失败了，so that 我不用干等或反复重发。
4. 作为用户，错误气泡的文案应是人话（如「网络连接失败，请检查网络或稍后重试」），so that 我能对症处理而不是看一串堆栈。
5. 作为用户，我想展开错误气泡看原始错误信息，so that 反馈问题时能提供线索。
6. 作为用户，错误气泡应持久显示直到我手动关闭，so that 切回来时还能看到刚才发生了什么。
7. 作为用户，错误应只存在于当前会话的界面里、刷新即清，so that 数据库里不留下任何错误记录，重启后干干净净。
8. 作为用户，切换会话时错误提示不应跟着我走，so that 新会话不被上一个会话的错误污染。
9. 作为用户，主动陪伴和每日问候出错时不要打扰我，so that 没在看屏幕的时候不被错误通知轰炸。
10. 作为用户，我希望进度提示在聊天界面内的位置清晰（如输入区上方），so that 我打字时也能注意到。
11. 作为开发者，重试进度的回传是现有关键词重试层的纯函数扩展，so that 我能用 RecordingSleep 精确断言回调次数与参数。
12. 作为开发者，重试回传不进 Agent 事件流，so that 不为纯 UI 瞬态扩大事件管道的复杂度。
13. 作为开发者，前端错误映射集中在人话文案表里，so that 未来加错误类型只改一处。

## Implementation Decisions

### 两条通道

- **瞬态通道（重试期间）**：
  - `retry()` 新增可选回调 `on_retry(attempt, max_retries, exc)`——每次实际退避重试前调用；成功、不可重试错误、重试耗尽均不调用「失败」回调（耗尽表现为最终抛出原异常，与现状一致）。
  - `LLMService` 的 `stream_chat` / `chat_sync` 把 `on_retry` 透传给内部重试包装（覆盖流建立与同步调用的重试；流中途断开不属于重试，维持现状不回调）。
  - `Agent.run` 新增可选 `on_llm_retry` 参数，传入 `stream_chat`。
  - WS 聊天处理器构造 `on_llm_retry` → `send_json({"type": "llm_retry", attempt, max_retries})`。
  - **直通 send_json 而不进 Agent 事件流**：回调发生在 generator 深处的 await 链内，无法从回调处 yield；且它是纯 UI 瞬态，不值得扩事件管道。但 CONTEXT.md §5.1 的事件表仍登记 `llm_retry`（它是客户端可见的服务端事件）。
- **持久通道（全部失败）**：现有 `error` 事件（含 WS 与 partial_error 路径）→ 前端内联红色错误气泡。

### 错误文案映射（前端）

- 按原始 message 关键词分类：连接类错误 → 「网络连接失败，请检查网络或稍后重试」；超时 → 「响应超时，请稍后重试」；其余 → 「出错了，请稍后重试」。
- 原始 message 放进气泡的可展开详情（兼做半个调试视图）。映射表集中一处，应用默认中文（不做英文版）。

### 范围与状态

- **仅聊天链路接 `on_llm_retry`**：主动陪伴 / 每日问候无人在看，不打扰（不传回调即不回传）。
- 重试提示与错误气泡**都只存前端 chatStore 内存**：切换会话即清；数据库照旧只存用户/助手消息与 partial 内容（现有行为不变）。
- 重试提示的清除时机：任何后续事件（下一个 token / done / error）到达即清除；错误气泡手动关闭。
- 无数据库迁移、无新 REST 端点；唯一新契约是 WS 事件 `llm_retry`，登记进 CONTEXT.md §5.1。

## Testing Decisions

### 测试原则

只测外部行为，复用现有 seam，不新增缝。重试语义的最深行为在 `retry()` 纯函数层，一条缝覆盖：

| 缝 | 注入 | 测什么 | 参照 |
|---|---|---|---|
| S1 `retry()` 回调 | `RecordingSleep` + 计数函数 | on_retry 每次退避重试前触发、次数与参数正确；成功 / 不可重试 / 耗尽路径不误触发 | `test_llm_retry.py` 现有结构 |
| S2 `LLMService` 透传 | 假 SDK client（先抛瞬态错再成功） | `stream_chat` / `chat_sync` 的 on_retry 透传生效；一次成功不触发回调 | `test_llm_retry.py` 的 FakeStream/FakeChunk |
| S3 WS 接线 | — | 纯接线（回调 → send_json），靠 S1/S2 + 现有回归兜底，不单独测 | — |

### 测试文件

- `backend/tests/test_llm_retry.py` 补 on_retry 用例（S1 + S2），不新建文件。

- 错误气泡 / 重试提示为纯前端 UI，手工验收（断网发消息 → 「正在重试 (n/5)」→ 失败出红色气泡 → 刷新即清）。

## Out of Scope

- 错误气泡「点击重试」按钮（自动重发涉及幂等与消息持久化，复杂度跳档）。
- WS 断线重连的进度提示（前端已有 5 次重连逻辑，本版不动其交互）。
- 错误文案英文版（应用默认中文）。
- 错误写入数据库 / 历史错误查询。
- 主动陪伴 / 每日问候链路的错误提示。

## Further Notes

### 改动清单

| 文件 | 改动 |
|---|---|
| `backend/services/retry.py` | `retry()` 新增可选 `on_retry(attempt, max_retries, exc)` |
| `backend/services/llm_service.py` | `stream_chat` / `chat_sync` 透传 `on_retry` |
| `backend/core/agent.py` | `run()` 新增可选 `on_llm_retry`，传入 stream_chat |
| `backend/api/chat.py` | 构造 on_llm_retry → `send_json({type:"llm_retry", attempt, max_retries})`；仅聊天链路 |
| `frontend/src/hooks/useWebSocket.ts` | 处理 `llm_retry`（设置瞬态重试状态）+ `error`（不再只 console.error，设置错误气泡状态） |
| `frontend/src/stores/chatStore.ts` | `retryState`（瞬态，后续事件即清）+ `errorBubble`（持久，手动关闭 / 切会话清除） |
| `frontend/src/components/chat/ErrorBubble.tsx`（新建） | 内联红色气泡，人话主文案 + 可展开原始 message |
| `CONTEXT.md` §5.1 | 登记新 WS 事件 `llm_retry` |
| `backend/tests/test_llm_retry.py` | 补 on_retry 用例 |

### 验证命令

```bash
cd backend && python -m pytest tests/test_llm_retry.py -v
```

### 运行方式

```bash
# 断网发消息 → 「正在重试 (n/5)」→ 全部失败出现红色错误气泡（展开可看原始 message）→ 刷新即清
```