# 01 — 后端 in-flight guard（主修复）

**What to build:** `handle_daily_greeting` 同一 WebSocket 连接上只允许一个并发任务。当第二条 `daily_greeting` 消息到达时，后端直接返回 `daily_greeting_skip`，不执行任何上下文收集、记忆提取、compact 或 LLM 调用。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] WebSocket handler 中维护 `asyncio.Task` 字典（key=conversation_id），收到 `daily_greeting` 时检查是否有未完成 task
- [ ] 有 in-flight task 时 → 发送 `{"type": "daily_greeting_skip", "reason": "in_flight"}`，不创建新 task
- [ ] 无 in-flight task 时 → 创建 task 并存入字典，`add_done_callback` 自动清理
- [ ] Guard 保护 `handle_daily_greeting()` 完整流程（上下文收集 → 记忆提取 → compact → 问候生成）
- [ ] `GreetingOrchestrator.run()` 现有 `last_daily_greeting_date` 检查保持不变
- [ ] 测试：`asyncio.gather` 并发两条 `daily_greeting` → 只有一条 `done` 带 `daily_greeting: true`，至少一条 `daily_greeting_skip`（reason=in_flight）
- [ ] 测试：顺序发送两条 → 第二条返回 `already_greeted` skip
- [ ] 测试：chat 消息不受 guard 影响
