# 02 — 并发重构 `handle_daily_greeting`

**What to build:** 记忆提取 + compact 移入后台 asyncio task，问候生成不再等待。快照防护生效——问候消息不会被提取进记忆。用户等待时间从"提取 + 问候"缩短为"问候"。

**Blocked by:** 01 — 依赖 `extract_and_store` 的 `before` 参数。

**Status:** ready-for-agent

- [ ] 在问候生成前取 `snapshot = datetime.now(timezone.utc)`
- [ ] `extract_and_store` 和 `summarize_and_trim` 放入 `asyncio.create_task` 后台运行，不 await
- [ ] 后台 task 传入 `before=snapshot`，确保问候消息不被扫到
- [ ] 后台 task 自开独立 `async_session`，供 extraction 和 compact 串行使用
- [ ] 主流程不等待后台 task，直接运行 `GreetingOrchestrator.run()`
- [ ] 问候上下文改用最近几条对话消息（不再依赖 `ai_summarized` 记忆，因为提取还没完成）
- [ ] `user_stated` 记忆（用户画像）在上下文收集中保留
- [ ] in-flight guard（`_daily_greeting_tasks`）逻辑不变
- [ ] 后台 task 异常不影响问候返回（已有 `try/except` 包裹）
- [ ] 后台 task 完成后自动清理（`add_done_callback`）
