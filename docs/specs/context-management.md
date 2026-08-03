# Spec: 上下文管理 — 删除修复 + Compact + 时间标记

**状态:** `ready-for-agent`
**日期:** 2026-08-03
**来源:** `/grill-with-docs` → 迭代计划 Workflow B

---

## Problem Statement

三个上下文相关的缺陷让 AI 的记忆不准确：

1. **删除是假的。** 用户在历史面板删了一条消息，UI 上消失了，但 AI 在下一轮对话中仍然引用那条消息的内容。根因是前端乐观更新——先删 UI 再发 API 请求，网络失败时静默吞错，消息实际没从数据库删除。

2. **长对话让 AI 变傻。** 对话超过几十条后，旧消息堆积在上下文窗口里挤掉了最近的信息。项目已有 `summarize_and_trim` 机制（keep=20），但只在后台触发（超过 30 条消息时），且每天记忆提取时不做裁剪。用户需要类似 Claude Code `/compact` 的体验——保存记忆的同时清理旧上下文。

3. **AI 混淆时间。** 记忆和摘要没有时间标记。"前天聊的事"被 AI 当成"昨天聊的"——因为没有时间参照，AI 只能猜。用户看到的记忆列表也只有日期字符串，没有相对时间的直觉感受。

## Solution

三管齐下：

- **B1**：修复前端删除的错误处理——恢复 UI + toast 提示，确保网络失败时用户知情且 UI 与数据库一致。
- **B2**：在每天记忆提取后触发裁剪（keep=12，约为 4-6 轮对话），同时提供手动 compact REST 端点。
- **B3**：在系统提示词中给每条记忆和摘要加相对日期前缀（今天/昨天/X天前/X周前/X个月前/绝对日期），每次注入时实时计算。

## User Stories

1. As a user, I want to delete a message and know it's truly gone from AI's context, so that sensitive or mistaken messages don't haunt future conversations.
2. As a user, I want to see a visible error when deletion fails (e.g. network down), so that I'm not misled into thinking it succeeded.
3. As a user, I want the deleted message to reappear in the history if deletion fails, so that I can retry or know it's still there.
4. As a user with a long conversation history, I want old messages to be summarized and trimmed automatically, so that the AI stays smart and responsive.
5. As a user, I want to manually trigger a compact (summarize + trim) on demand, so that I can clean up context without waiting for the daily cycle.
6. As a developer, I want `summarize_and_trim` to accept a configurable `keep_count`, so that different triggers (daily auto vs manual) can use different retention windows.
7. As a user, I want memories to show when they were formed in relative terms (e.g. "3 天前"), so that I and the AI both know how recent a memory is.
8. As a user, I want conversation summaries to include a relative date indicator, so that the AI knows how fresh the summary is.
9. As a user reading memories older than 6 months, I want to see the absolute year and month instead of "180 天前", so that the date remains meaningful.
10. As a user with a busy conversation, I want relative dates to update automatically each time I open the app, so that "昨天" doesn't stay stale.
11. As a developer, I want the relative date formatter to be a pure function testable without any external dependencies, so that I can verify all edge cases easily.
12. As a developer, I want the compact flow to be verifiable end-to-end in tests, so that I know extract → summarize → trim works as a pipeline.

## Implementation Decisions

### 决策 1：删除 Bug 修复（B1）

**前端改动**：`HistoryOverlay.tsx` 的 `handleDeleteMsg` 中，catch 分支不再静默吞错。改为：

1. 将 `removedMsg` 恢复到 Zustand store 的消息列表（按时间排序）
2. 显示 toast 错误提示："删除失败，请检查网络"
3. 重置 `confirmingDelete` 状态

同时在 `chatStore.ts` 新增 `removeMessage` action，将直接的 `setState` 调用封装为 store 方法，便于测试和复用。

**后端不需要改动**。`DELETE /api/conversations/{id}/messages/{mid}` 已经正确执行硬删除。问题纯粹在前端。

### 决策 2：Compact 触发时机与流程（B2）

**自动触发**：在 `handle_daily_greeting` 中，记忆提取（`extract_and_store`）完成后，立即调用 `summarize_and_trim(keep_count=12)`。

**手动触发**：新增 `POST /api/conversations/{id}/compact` 端点，用户可通过前端按钮或 curl 触发。

**keep_count 从 20 改为 12**：约保留 4-6 轮来回（每轮 = 用户消息 + AI 回复 ≈ 2 条），在上下文新鲜度和信息保留之间取得平衡。`summarize_and_trim` 方法签名不变，只是调用方改参数。

**compact 流程**：
1. 取最旧的 `total - 12` 条消息
2. 调用 LLM 生成摘要（合并已有 `conversation.summary`）
3. 更新 `conversation.summary`
4. 删除已摘要的消息
5. 返回被删除的消息数量和新摘要

### 决策 3：相对日期格式规则（B3）

在 `PromptManager` 中新增 `@staticmethod format_relative_date(dt: datetime) -> str`：

| 时间距离 | 显示 |
|---------|------|
| 今天 | `今天` |
| 昨天 | `昨天` |
| 2–7 天 | `3天前` |
| 8–30 天 | `1周前` ~ `4周前` |
| 1–5 月 | `1个月前` ~ `5个月前` |
| ≥6 月 | `2026年3月` |

- 每次注入系统提示词时实时计算（不存 DB），保证 "昨天" 不会永远显示昨天
- 超过 6 个月切绝对日期，避免 "365 天前" 这种无意义的大数字

### 决策 4：注入位置（B3）

**记忆**：在 `Agent.run()` 中构建 `retrieved_memories` 列表时，每条记忆前加时间标签：

```
## Memories About The User
- (3天前) 和用户聊了 Rust 的所有权概念...
- (1周前) 用户说要开始健身计划...
```

**摘要**：在注入 `conversation_summary` 时加截止日期：

```
## Previous Conversation Summary
(截至3天前) 用户最近在学 Rust，对所有权概念有困惑...
```

格式化为发生在 `Agent.run()` 中，紧接在调用 `build_system_prompt` 之前。不修改 `Memory` 模型或数据库——标签在注入时动态计算，保持数据层干净。

### 决策 5：不引入 toast 库

项目目前没有 toast 依赖。用 `alert()` 或自定义轻量 toast（一个绝对定位的 div，3 秒后消失）。选择后者——已有设置面板的 `Saving...` 提示作为样式参考。

## Testing Decisions

### 测试哲学

只测试外部行为，不测试实现细节。使用 fake 替代真实依赖。

### B1 — 删除错误恢复

- **Seam**：模拟 `fetch` 返回网络错误
- **测试用例**：
  - 删除成功 → 消息从 UI 移除
  - 删除失败 → 消息恢复到 UI，错误提示显示
  - 删除失败 → `confirmingDelete` 重置

> 前端组件测试暂不纳入本次 scope（项目无知前端测试设施）。手动冒烟验证。

### B2 — Compact

- **Seam 1**：`ConversationManager.summarize_and_trim(keep_count=12)` + fake LLM
  - 12 条消息 → 不裁剪
  - 30 条消息 → 最旧的 18 条被删除，summary 更新
- **Seam 2**：`POST /api/conversations/{id}/compact` + TestClient + 内存 SQLite
  - 返回 deleted_count 和 summary
  - 不存在的 conversation → 404
- **Seam 3**：在 `chat.py` 中验证 extract_and_store → compact 的调用顺序（集成测试）

### B3 — 相对日期

- **Seam**：`PromptManager.format_relative_date(dt)` pure function
  - 今天 / 昨天 / 3 天前 / 2 周前 / 3 个月前 / 绝对日期
  - 边界：正好 7 天（1 周前）、正好 30 天（1 个月前）、正好 180 天（2026 年 2 月）
- **注入验证**：`build_system_prompt` 输出中包含 `(X天前)` 格式的记忆前缀和 `(截至X天前)` 的摘要前缀

### 已有测试先例

项目已有 78 条 pytest 测试（内存 SQLite + `pytest-asyncio`），遵循 `docs/specs/decompose-chat-god-module.md` 的测试模式。本次测试延续这一风格。

## Out of Scope

- **前端 toast 组件库** — 不引入 react-hot-toast 等第三方依赖，用轻量内联实现
- **前端组件单元测试** — 项目无知前端测试基础设施，B1 用手动冒烟验证
- **compact 的前端 UI 按钮** — 先做 API 端点，前端按钮属于 UI 增强，可在后续迭代加
- **消息软删除** — 保持现有硬删除机制。软删除（`deleted_at` 列）需要改 Message 模型和所有查询逻辑，改动范围超出 Workflow B
- **记忆时间标记的前端展示** — MemoryViewer 中的记忆列表暂不加相对日期，仅改系统提示词注入
- **记忆提取时的日期感知** — MemoryExtractor 本身不感知日期，只在注入时加标签

## Further Notes

- B1（删除修复）是最简单的——纯前端改，3 行代码
- B2（compact）的关键改动在 `chat.py` 的 `handle_daily_greeting` 和新增的 compact 端点
- B3（时间标记）的关键改动在 `PromptManager`（format_relative_date）和 `Agent.run()`（format 记忆/摘要再传入）
- B3 的 format_relative_date 是纯函数，测试最早写最保险
- compact 后 conversation.summary 保留，新消息加上去后摘要越来越丰富
- 此 spec 对应 `docs/specs/iteration-plan-20260731.md` Workflow B，依赖 Workflow A（已完成）
