## Problem Statement

当前记忆系统存在三个问题：

1. **角色记忆不隔离**：Memory 表没有 `character_id`，所有角色共享同一份记忆。选灰暗时能在设置→查看记忆中看到小樱以前总结的记忆，灰暗的 AI 也能读到这些记忆。
2. **记忆来源不可追溯**：AI 通过 `save_memory` 工具存的记忆和用户明确陈述的事实没有区分。AI 曾经编造的内容被当成事实存储，之后在每日问候和聊天中被检索出来，形成自我循环的幻觉链。
3. **缺少自动提取机制**：`MemoryExtractor` 写好了但从未接入线上。仅靠 `save_memory` 工具不够——AI 可能漏掉重要信息，用户也不会每条都手动说"记住"。

## Solution

1. Memory 表加 `character_id` 列，所有记忆入口（工具、API、检索）按角色隔离。
2. Memory 表加 `source` 列，标记来源（`user_stated` / `ai_summarized`），问候场景排除 AI 总结的内容。
3. 接入 `MemoryExtractor`，在每日问候前自动提取一次上次问候以来的对话内容。一天最多一次，自然受"今日已问候"检查保护。

## User Stories

1. 作为用户，当我切换到灰暗角色时，查看记忆中不应看到小樱的记忆。
2. 作为用户，当灰暗发起每日问候时，不应引用小樱对话中保存的记忆。
3. 作为用户，当我在小樱的对话中说"记住我喜欢吃辣"，小樱调用 save_memory 后，这条记忆应绑定到小樱角色而非灰暗。
4. 作为用户，每日问候应该优先使用用户明确说过的记忆，而非 AI 自己总结的日记体内容。
5. 作为用户，AI 之前编造的幻觉内容不应在后续问候中被当作真实记忆引用。
6. 作为用户，每天第一次打开窗口时，AI 应自动回顾上次问候以来的对话并提取值得记住的内容，不用我每次都说"记住这个"。
7. 作为用户，自动提取每天最多只发生一次，不会在对话中途频繁打断或消耗额外 token。

## Implementation Decisions

### Schema 变更

- `Memory` 模型增加 `character_id: str | None`（nullable for backward compat），外键指向 `character_profiles.id`
- `Memory` 模型增加 `source: str`，枚举值：`"user_stated"`（用户明确陈述或要求记住）和 `"ai_summarized"`（AI 自动判断并保存），默认 `"ai_summarized"`
- 现有数据行为：`character_id` 为 NULL（向后兼容），`source` 为 `"ai_summarized"`（因为所有现有记忆都是 AI 调用 save_memory 存的）

### 接口变更

- `save_memory` 工具：接收 `character_id` 参数（由 Agent 注入，LLM 不可见），`source` 默认 `"ai_summarized"`
- `search_memory` 工具：接收 `character_id` 参数（由 Agent 注入），过滤当前角色记忆
- `GET /api/memories`：增加可选查询参数 `character_id`，前端传入当前角色 ID 进行过滤
- `MemoryRetriever.search()`：`character_id` 从可选改为必传，空查询路径也强制过滤
- 每日问候记忆检索：排除 `source='ai_summarized'` 的记忆，或至少优先 `user_stated`

### Agent 上下文注入

- Agent 在派发工具调用前，对 `save_memory` 和 `search_memory` 注入 `character_id`（Agent 持有当前 character_id）
- 注入方式：在 `_execute_tools_with_approval` 中，对需要角色上下文的工具调用补充 `character_id` 参数

### 记忆自动提取策略

- **触发时机**：每日问候前，在 `handle_daily_greeting` 中、问候生成前
- **提取范围**：上次问候（`character.last_daily_greeting_date`）以来的所有消息，而非固定数量
- **自然节流**：受"今日已问候"检查保护，一天最多触发一次。如果用户今天已经问候过（`last_daily_greeting_date == today`），问候和提取都跳过
- **与其他机制的配合**：`save_memory` 工具照常工作（对话中即时保存），自动提取是兜底——补上那些 AI 没主动调用工具但值得记住的对话内容
- **提取产物标记**：自动提取的记忆 `source='ai_summarized'`，与 `save_memory` 工具产出的标记一致

对比旧方案（`MemoryExtractor` 原来的批处理设计）：

| | 旧设计（死代码） | 新方案 |
|---|---|---|
| 触发 | 消息数 ≥ 10 条 | 每日问候前 |
| 频率 | 每 10 条一次 | 一天最多一次 |
| 可验证性 | 差（频繁产生小块） | 好（一天一块，量适中） |
| 延迟 | 低 | 最多一天 |

### 向后兼容

- `character_id` 为 nullable，现有 NULL 值的记忆在所有角色下可见（过渡期行为）
- 新保存的记忆从保存时刻起绑定角色
- 前端 `GET /api/memories` 不传 `character_id` 时返回所有记忆（保持旧行为兼容）

## Testing Decisions

### 测试接缝

- `MemoryStore.add()`：接收 character_id 和 source 参数，写入 DB
- `MemoryRetriever.search()`：character_id 强制过滤，空查询路径同样过滤
- `save_memory` 工具函数：传入 character_id，验证记忆绑定到正确角色
- `GET /api/memories`：传入 character_id 参数，验证只返回对应角色记忆
- `MemoryExtractor.extract_and_store()`：传入 conversation_id 和分界日期，验证只提取指定范围内的消息
- 每日问候流程：验证问候前触发了记忆提取，且一天只触发一次

### 测试策略

- 使用内存 SQLite + FTS5 进行集成测试（复用现有 test_memory_retriever / test_memory_extractor 模式）
- 只测试外部行为（存入角色A的记忆不被角色B检索到），不测试实现细节
- monkeypatch `async_session` 指向测试用内存数据库
- 问候流程测试：mock `last_daily_greeting_date` 验证提取触发逻辑

### 现有测试参考

- `backend/tests/services/test_memory_retriever.py` — FTS5 + 内存 SQLite 模式
- `backend/tests/services/test_memory_extractor.py` — monkeypatch async_session 模式

## Out of Scope

- 前端"查看记忆"UI 的角色切换（仅后端 API 支持 character_id 过滤）
- 已存幻觉记忆的清理（用户可在查看记忆界面手动删除，不做自动清理）
- 对话中途的增量提取（仅保留问候前触发一种自动路径）

## Further Notes

- `MemoryExtractor.extract_and_store()` 当前为死代码（仅在测试中调用），本次将其接入每日问候流程
- `save_memory` 工具照常工作，是对话中即时保存的主渠道；自动提取是兜底
- 两种保存路径并存：工具即时保存（高频、单条）+ 问候前批量提取（低频、一天一次）
- 提取边界用 `character.last_daily_greeting_date` 而非消息数量，更自然——提取"上次问候以来用户分享的全部内容"，而非"最近 N 条消息"
