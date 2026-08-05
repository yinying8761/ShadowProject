# 待修复 & 待优化

> 最后更新: 2026-08-04

---

## 1. 每日问候触发两次

**现象**: 启动后收到两条不同的 daily greeting 消息。

**根因**: 
- 后端 `chat.py:350-352` 中 `asyncio.create_task(handle_daily_greeting())` 没有 in-flight guard
- `GreetingOrchestrator` 的 `last_daily_greeting_date` 检查在第一次 LLM 调用完成前不生效（日期还没写入），第二次请求到来时检查通过
- 前端可能也发送了两次 `daily_greeting` 消息，待排查

**影响**: 用户收到两条问候；记忆提取跑两次；compact 跑两次（第二次 deleted=0 无害）；weather API 被调用两次

**修复方向**:
- 后端加 in-flight guard（`_daily_greeting_in_progress` flag），阻止并发 greeting 任务
- 前端排查为何发送两次

---

## 2. 每日问候被记忆提取阻塞

**现象**: 用户打开应用后要等很久才看到问候，因为记忆提取（LLM 摘要 + 裁剪）在问候之前串行执行。

**根因**: `handle_daily_greeting()` 中先调 `extract_and_store()` 和 `summarize_and_trim()`，然后才调 `GreetingOrchestrator.run()` 生成问候。整个流程串行。

**影响**: 用户体验差——打开应用期望即时问候，却被阻塞数秒到数十秒。

**修复方向**:
- 将记忆提取和 compact 改为后台任务（`asyncio.create_task`），问候生成先执行
- 或至少让问候和记忆整理并行——先发起问候的 LLM 调用，同时启动记忆提取

---

## 3. 记忆提取触发两次

**现象**: 每日记忆提取（`extract_and_store`）被执行两次。

**根因**: 同 #1 —— `handle_daily_greeting` 被调用两次，里面的 `extract_and_store` 自然也重复执行。

**影响**: LLM 调用浪费；可能产生重复记忆。

**修复方向**: 同 #1，修复 greeting 的 in-flight guard 即可一起解决。
