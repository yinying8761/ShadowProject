# Tickets — 记忆角色隔离 + 来源标记 + 每日提取

基于 spec: `.scratch/memory-isolation-spec.md`

---

## #1 — Schema 扩展

**What to build:** Memory 模型增加 `character_id` 和 `source` 两列（nullable），SQLite 执行 ALTER TABLE 迁移。现有代码无行为变更——纯 expand 步骤。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Memory 模型增加 `character_id: str | None`（外键 → character_profiles.id）
- [ ] Memory 模型增加 `source: str`，默认 `"ai_summarized"`
- [ ] 应用启动时检查并执行 ALTER TABLE（若列不存在则添加）
- [ ] 现有 43 个测试全部通过（行为不变验证）

---

## #2 — 工具 + 检索角色隔离

**What to build:** Agent 在派发 `save_memory` / `search_memory` 前注入当前 `character_id`。存储层记录角色和来源，检索层强制按角色过滤。角色 A 存的记忆，角色 B 搜不到。

**Blocked by:** #1 — Schema 扩展

**Status:** ready-for-agent

- [ ] `MemoryStore.add()` 接收 `character_id` 和 `source` 参数并写入 DB
- [ ] `MemoryRetriever.search()` 的 `character_id` 参数改为必传，空查询路径也按字符过滤
- [ ] Agent 在 `_execute_tools_with_approval` 中对 `save_memory` / `search_memory` 注入 `character_id`
- [ ] `save_memory` 工具产出标记 `source='ai_summarized'`
- [ ] `search_memory` 工具只返回当前角色的记忆
- [ ] 测试：角色 A 存的记忆不被角色 B 检索到
- [ ] 测试：空查询路径正确过滤 character_id

---

## #3 — API + 问候记忆隔离

**What to build:** 记忆查看 API 按角色过滤，每日问候的 memory search 带角色过滤且排除 AI 总结的内容。选灰暗时看不到小樱的记忆。

**Blocked by:** #2 — 工具 + 检索角色隔离

**Status:** ready-for-agent

- [ ] `GET /api/memories?character_id=xxx` 只返回指定角色的记忆
- [ ] 不传 `character_id` 时返回所有记忆（向后兼容）
- [ ] 每日问候 `handle_daily_greeting` 的 memory search 传入 `character_id`
- [ ] 每日问候的 memory search 排除 `source='ai_summarized'`（或优先 `user_stated`）
- [ ] 测试：API 按 character_id 过滤
- [ ] 测试：问候路径不使用其他角色的记忆

---

## #4 — 每日记忆自动提取

**What to build:** 每日问候前自动提取上次问候以来的对话内容，接入已有的 `MemoryExtractor`。一天最多一次，自然受"今日已问候"检查保护。

**Blocked by:** #3 — API + 问候记忆隔离

**Status:** ready-for-agent

- [ ] `handle_daily_greeting` 中，问候生成前调用 `MemoryExtractor.extract_and_store()`
- [ ] 提取范围：`last_daily_greeting_date` 以来的所有消息（而非固定消息数）
- [ ] 提取产物标记 `character_id` + `source='ai_summarized'`
- [ ] 受"今日已问候"保护：同一天内第二次触发直接跳过（问候和提取都跳过）
- [ ] 用户今天无新消息时不提取（空提取返回 []）
- [ ] 测试：提取只覆盖上次问候以来的消息范围
- [ ] 测试：一天只触发一次
