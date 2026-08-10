# Daily Greeting 防重复修复

> 日期：2026-08-07 | 状态：待实现 | 关联：`pending-fixes.md` #1, #3

## Problem Statement

每次打开应用时，每日问候（daily greeting）会被触发两次，导致：

1. 用户收到两条不同的问候消息
2. 记忆提取（`extract_and_store`）运行两次，浪费 LLM token
3. 对话 compact（`summarize_and_trim`）运行两次
4. 天气 API 被调用两次

**根因**：后端 `handle_daily_greeting()` 缺少 in-flight guard。前端在 React StrictMode 下 `useDailyGreeting` 可能发送两次 `daily_greeting` 消息，后端照单全收，两个并发任务都通过了 `last_daily_greeting_date` 检查（日期在第一次 LLM 调用完成前还没写入数据库）。

**时序**：

```
Frontend                    Backend Task A                Backend Task B
  |                             |                             |
  |-- daily_greeting ---------->|                             |
  |                             |-- read last_date=yesterday  |
  |-- daily_greeting ---------->|                             |
  |                             |-- extract_and_store()       |-- read last_date=yesterday
  |                             |-- summarize_and_trim()      |-- extract_and_store()
  |                             |-- orch.run() → LLM call     |-- summarize_and_trim()
  |                             |-- mark today, commit        |-- orch.run() → LLM call
  |                             |                             |-- mark today, commit
```

## Solution

**后端为主、前端为辅**：

- **后端**：在 WebSocket handler 中加 in-flight guard（`asyncio.Task` 字典），同一连接上并发 `daily_greeting` 请求只执行第一个，后续直接返回 `daily_greeting_skip`。
- **前端**：在 `useDailyGreeting` 中检查 `localStorage` 的 `daily_greeting_date`，当天已标记则跳过发送。消除 StrictMode 双 mount 导致的重复发送。

## User Stories

1. 作为用户，打开应用时我只收到一条每日问候，不会同时看到两条内容不同的问候。
2. 作为用户，每日的记忆提取只在后台运行一次，不会浪费 LLM token 重复提取相同对话。
3. 作为用户，天气 API 调用不会因为重复触发而浪费配额。
4. 作为开发者，`handle_daily_greeting` 是幂等的——无论前端发送多少次 `daily_greeting` 消息，后端只执行一个任务。
5. 作为开发者，当 in-flight guard 拦截重复请求时，后端发送 `daily_greeting_skip` 事件，前端正确更新 `daily_greeting_date` 到 localStorage。

## Implementation Decisions

### 后端：in-flight guard

- 在 WebSocket handler（`chat.py`）中维护一个连接级别的 in-flight 字典：`_daily_greeting_tasks: dict[str, asyncio.Task]`，key 为 `conversation_id`。
- 收到 `daily_greeting` 消息时：
  - 检查是否已有未完成的 task。若有 → 跳过，发送 `{"type": "daily_greeting_skip", "reason": "in_flight"}`。
  - 若无 → 创建 task，存入字典，添加 `add_done_callback` 自动清理。
- 这个 guard 保护的是 `handle_daily_greeting()` 的整个过程：上下文收集 → 记忆提取 → compact → 问候生成。不需要在子模块（`GreetingOrchestrator`、`MemoryExtractor`）中各自加锁。
- `GreetingOrchestrator.run()` 中已有的 `last_daily_greeting_date == today` 检查保留，作为第二道防线（跨应用重启场景）。

### 前端：localStorage guard

- 在 `useDailyGreeting.ts` 中，发送 `daily_greeting` 消息前检查 `localStorage.getItem('daily_greeting_date')`。
- 如果值等于今天的日期 → 跳过发送，不再启动轮询。
- 这个 guard 放在 `setInterval` 回调的第一行，也放在 visibility handler 的入口。

### 不改的部分

- `GreetingOrchestrator` 的 `last_daily_greeting_date` 检查逻辑不变。
- `extract_and_store` 和 `summarize_and_trim` 的调用位置不变——in-flight guard 从上游阻止了重入，它们不需要内部改动。
- 不做全局（跨连接）的 daily greeting 去重——`last_daily_greeting_date` 已经在数据库层面处理了。

## Testing Decisions

### 测试原则

- 只测外部行为：后端收到 N 次请求 → 只执行一次 greeting 流程。
- 用 Python 标准库的 `asyncio` 模拟并发发送两条 `daily_greeting` 消息。
- 前端用现有的 `useDailyGreeting` hook 测试模式（手动启动验证即可，不做自动化 UI 测试）。

### Seam

| Seam | 位置 | 用途 |
|------|------|------|
| S1 — WebSocket 并发消息 | `TestClient` + `asyncio.gather` | 发送两条 `daily_greeting`，验证只生成一条问候 |
| S2 — localStorage guard | 前端 `useDailyGreeting` | 当天已标记 → 不发送 |

### 测试用例

**后端测试** (`backend/tests/test_daily_greeting_guard.py`)：

1. **并发两条 daily_greeting 消息**：用 `asyncio.gather` 同时发送两条 `{"type": "daily_greeting"}` → 验证：
   - 只有一条 `done` 事件带 `daily_greeting: true`
   - 至少收到一条 `daily_greeting_skip`（原因 `in_flight`）
   - `extract_and_store` 只被调用一次（通过 mock LLM 的调用次数验证）
2. **顺序发送两条**：第一条完成后发第二条 → 验证第二条返回 `already_greeted` skip。
3. **非 daily_greeting 消息不受 guard 影响**：发送 chat 消息不受 in-flight 字典干扰。

**前端验证**（手动）：

1. 打开应用 → 收到一条问候。
2. 最小化再恢复窗口 → 不会重复发送（localStorage 已有今天的日期）。
3. 跨天场景：第二天打开 → 正常收到新问候。

## Out of Scope

- 跨连接的全局 daily greeting 去重（已有数据库 `last_daily_greeting_date` 覆盖）。
- 修复 bug #2（问候被记忆提取阻塞）——那是并发顺序问题，独立处理。
- 修复 bug #4、#5——与本次无关。

## Further Notes

- Bug #3（记忆提取重复）与 bug #1 同根因，修复 in-flight guard 后一并解决。
- 修复后可关闭 `pending-fixes.md` 中的 #1 和 #3。
- `daily_greeting_skip` 新增 `reason: "in_flight"` 变体，前端已有的 `daily_greeting_skip` case 无需修改即可处理。
