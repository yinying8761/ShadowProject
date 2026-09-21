# 02 — 群数据模型 + conversations 重建迁移 + ADR

**What to build:** 引入「群」的数据基础：新增 `Group` / `GroupMember` 表；`Conversation` 增加
`group_id`（可空，有值即群对话）与 `last_extract_at`（可空，"打开时补账"锚点）；`Conversation.character_id`
由 NOT NULL 改为**可空**（群对话没有单一角色）——这一步需要**一次性重建式迁移**，并落一个 ADR。

**Blocked by:** #01（同一批表，迁移顺序排在时间戳列之后）

**Status:** ready-for-agent

**参考:** `docs/specs/group-chat.md`（Phase 2 · 群实体与数据模型）

**关键语义：**

- 新表由 `create_all` 建；列变更走迁移。`character_id` 改可空**偏离项目"只加列"惯例** →
  显式重建迁移（建新表 → 拷数据 → 换名）+ 备份 + **ADR 记录该约束变更**。
- 迁移必须**保留既有数据**（会话、消息、外键关系），且**幂等**（重复执行安全）。
- 1:1 语义完全不变：`group_id` 为空的会话行为与现在一致。
- 群成员带**发言顺序**（顺序即群轮发言次序）。

## Checklist

- [ ] `Group`（名称、创建时间）与 `GroupMember`（群、角色、发言顺序）模型
- [ ] `Conversation.group_id` + `Conversation.last_extract_at`（增量迁移）
- [ ] `conversations.character_id` 改可空的重建式迁移（数据保留 + 幂等 + 失败可回滚/先备份）
- [ ] ADR：记录该列约束变更与「群对话无单一角色」的模型决定
- [ ] 测试：迁移测试（按老 schema 建库 → 跑迁移 → 数据完整 + `character_id` 可空）
- [ ] 手工/脚本验收：用 `data/companion.db` 的副本跑一次迁移，会话与消息条数不变
- [ ] 1:1 全量回归：`python -m pytest tests/ -v`