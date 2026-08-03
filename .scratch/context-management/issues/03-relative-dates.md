# 03 — 记忆和摘要的时间标记

**What to build:** `PromptManager.format_relative_date()` 纯函数将日期转为中文相对日期（今天/昨天/3天前/1周前/3个月前/2026年3月）。Agent 注入记忆和摘要到系统提示词时自动加时间前缀，AI 知道每条信息的时效。每次注入实时计算，不存数据库。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] `PromptManager.format_relative_date(dt) -> str` 静态方法实现相对日期格式化
- [ ] 格式规则：今天/昨天/2-7天→X天前/8-30天→X周前/1-5月→X个月前/≥6月→YYYY年M月
- [ ] `Agent.run()` 中：`retrieved_memories` 列表每条前面加 `(X天前)` 时间标签
- [ ] `Agent.run()` 中：`conversation_summary` 加 `(截至X天前)` 时间标签
- [ ] 测试：format_relative_date 覆盖所有时间段 + 边界值（正好7天、30天、180天）
- [ ] 测试：build_system_prompt 输出包含 `(X天前)` 格式的记忆前缀和 `(截至X天前)` 的摘要前缀
