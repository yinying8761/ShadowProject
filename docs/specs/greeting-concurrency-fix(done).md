# Daily Greeting 不被记忆提取阻塞

> 日期：2026-08-10 | 状态：待实现 | 关联：`pending-fixes.md` #2

## Problem Statement

每次打开应用时，用户要等很久才能看到每日问候。因为 `handle_daily_greeting()` 先把记忆提取（`extract_and_store`，LLM 调用 5~30s）和对话裁剪（`summarize_and_trim`，1~3s）串行跑完，最后才调用 `GreetingOrchestrator` 生成问候。整个过程耗时 10~40 秒，用户体验差。

**根因**：整个问候流程是串行的 —— 记忆整理在问候生成之前，用户被迫等待与问候无关的后台工作完成。

**时序（当前）**：

```
08:00:00  用户打开应用
08:00:01  handle_daily_greeting() 开始
08:00:02  查天气、定位
08:00:03  extract_and_store() → LLM 提取记忆（等 15s）
08:00:18  compact 裁剪旧消息
08:00:22  GreetingOrchestrator → LLM 生成问候（等 8s）
08:00:30  用户看到问候  ← 等了 29 秒
```

## Solution

**`asyncio.create_task()` 并发**：记忆提取 + compact 放入后台协程，问候生成立即开始。两者 I/O（LLM API 调用）自然交错，用户等待时间从"提取 + 问候"缩短为"问候"。

**防泄漏**：并发启动前取时间戳快照，传入 `extract_and_store` 作为消息查询的上界，确保问候生成的消息不会被提取进记忆。

**时序（改后）**：

```
08:00:00  用户打开应用
08:00:01  handle_daily_greeting() 开始
08:00:02  查天气、定位
08:00:03  snapshot = now()  ← 快照
          ├─ 后台 task: extract → compact（15s）    ├─ 主流程: 问候生成（8s）
08:00:11  用户看到问候  ← 只等了 10 秒
08:00:18  后台任务完成
```

## User Stories

1. 作为用户，打开应用后 5~10 秒内就能看到每日问候，不再被记忆提取阻塞数十秒。
2. 作为用户，问候内容不受影响——即使记忆提取在后台运行，问候仍然包含天气、位置等上下文。
3. 作为用户，记忆提取在后台正常完成，不会因为我看到了问候就丢失或重复。
4. 作为开发者，问候生成的消息不会被 `extract_and_store` 当作"对话内容"提取进记忆。
5. 作为开发者，`extract_and_store` 新增 `before` 参数是可选的——不传时行为与原来完全一致，不影响其他调用方。
6. 作为开发者，后台 task 内部仍然串行（先提取 → 再 compact），保持原有的逻辑依赖关系。
7. 作为开发者，后台 task 使用自己独立的数据库 session，不影响主流程的事务。

## Implementation Decisions

### 并发方式：asyncio 协程

- 使用 `asyncio.create_task()` 而非 `threading`。两个任务主要耗时都是 LLM API 调用（I/O），协程在 `await` 处自然切换，无需锁。
- 后台 task：`extract_and_store` → `summarize_and_trim`（内部串行）
- 主流程：收集上下文 → `GreetingOrchestrator.run()`（立即开始，不 await 后台 task）

### 防泄漏：时间戳快照

- `extract_and_store` 新增可选参数 `before: datetime | None`
- 并发启动前在 `handle_daily_greeting` 中取 `snapshot = datetime.now(timezone.utc)`
- 传入 `before=snapshot`，SQL 查询变为 `created_at >= since_date AND created_at < before`
- 问候消息入库时间 > snapshot，因此不会被扫到
- `before` 为 None 时行为不变（向后兼容）

### 后台 task 的 session

- 后台 task 自开一个 `async_session()`，供 `extract_and_store` 和 `summarize_and_trim` 共享
- 两个操作在同一事务内串行执行
- 与主流程的 session 完全隔离

### 问候上下文改用最近消息

- 问候 prompt 中不再引用 `ai_summarized` 记忆（因为提取还没完成）
- 改用最近几条对话消息作为上下文，提供"现场感"
- `user_stated` 记忆（用户画像）仍然保留

### 不改的部分

- `GreetingOrchestrator` 的 `last_daily_greeting_date` 检查逻辑不变
- in-flight guard（`_daily_greeting_tasks` 字典）不变——它防止并发 greeting，本次改动不影响它
- `extract_and_store` 的 JSON 解析、dedup、embedding 逻辑不变
- `summarize_and_trim` 的逻辑不变

## Testing Decisions

### 测试原则

- 只测外部行为：问候在记忆提取完成前就返回、提取不被问候消息污染。
- 用 mock LLM 控制时序，验证并发正确性。

### Seam

| Seam | 位置 | 用途 |
|------|------|------|
| S1 — 函数调用级 mock | `memory_service.extract_and_store` | 注入慢速 mock，验证问候先返回 |
| S2 — `before` 参数过滤 | `extract_and_store` SQL 查询 | 验证快照之后的消息不出现 |

### 测试用例

1. **问候先于记忆提取返回**：mock `extract_and_store` 延迟 1 秒 → 验证 `daily_greeting` done 事件在提取完成前发出。
2. **`before` 参数正确过滤**：在快照之后插入一条消息 → `extract_and_store(before=snapshot)` 不包含该消息。
3. **后台 task 失败不影响问候**：后台抛异常 → 问候正常返回，不报错。
4. **`before=None` 向后兼容**：不传 `before` → 行为与原来一致。

## Out of Scope

- 修改 greeting prompt 的具体内容（本次只改消息来源，不改 prompt 结构）
- 修复 bug #1 / #3（已通过 in-flight guard 修复）
- 修复 bug #4 / #5
- 前端改动

## Further Notes

- 修复后可关闭 `pending-fixes.md` 中的 #2。
- `before` 参数还可在将来用于其他需要"截至某时刻"的提取场景。
- 考虑将来如果 extraction 特别慢（>60s），应加超时处理，避免 task 泄漏。本次不做。
