# 群聊审查修复 + 孤儿数据取证

> 日期：2026-09-22 | 关联 spec：`docs/specs/group-chat.md` | ADR：`docs/adr/0004-conversations-character-id-nullable.md`
> 审查固定点：`origin/master`（`3ae9605`）→ `HEAD`（`a28b3df` #50 / `cce9d6b` #51 / `005397b` #52）
>
> 本文两部分：
> **(A)** 两轴审查（Standards / Spec）结论落地情况——已修复项，供接手者避免重复劳动；
> **(B)** 尚未修复项的交接清单 + 一次孤儿数据取证。
> **未实施**的修复一律标明，末尾 §B3 给出可直接照抄的补丁草案。

---

## 0. 结论速览（未修复项）

| # | 问题 | 位置 | 严重度 | 状态 |
|---|---|---|---|---|
| 1 | 聊天接口不校验 `conversation_id` 是否存在 → 往已删除的会话发消息会写出**孤儿消息** | `backend/api/chat.py:33`（HTTP）、`:101`（WS） | 高（孤儿数据的直接成因，可复现） | **未修** |
| 2 | `DELETE /api/characters/{id}` 不级联 → 留下**孤儿会话**与悬空 `memories.character_id` | `backend/api/character.py:121` | 高（可复现） | **未修** |
| 3 | `PRAGMA foreign_keys` 从不开启 → 模型里所有 `ondelete=...` 声明在 DB 层无效 | `backend/database.py:14` | 中（根因） | **未修** |
| 4 | 角色名无非空校验 → 可建出 `name=""` 的角色，其消息渲染成 `[AI]` | `backend/api/character.py:12`、`:22`、`:113` | 低 | **未修** |
| 5 | 存量孤儿数据未清理（2 条孤儿会话 / 39 条消息 / 6 条孤儿消息 / 11 条悬空 memories） | `data/companion.db` | 低（不影响运行） | **未清理** |
| 6 | `_FALLBACK_AI_SPEAKER = "AI"` 的语义（spec 未定义该情形） | `backend/core/transcript.py:25` | 低 | 仅补文档，未改文案 |

> **2026-09-23 更新**：上表 1–5 已实施，6 保持文档化。决策与后果见
> `docs/adr/0005-service-layer-owns-referential-integrity.md`，实施记录见本文 **§C**。

---

# A. 已修复项（本轮已改，未提交）

来源：对上述三个提交做的 Standards + Spec 两轴审查。全部改动均已通过全量测试。

## A1. 群的 AI 说话人规则无路可达 → 一律显示 `[AI]`（Spec 轴最严重项）

spec L24/L76 要求「群聊的 AI 消息 → 该条消息的发言角色名」，但 `speaker_names` 没有任何消费者传入，渲染器的群分支是死代码。

**修法**：`backend/api/conversation.py` 新增 `_speaker_names_for()`，按**消息自己的 `speaker_id`** 查角色名（而不是按群成员资格——成员资格可编辑），并传给共享渲染器。

```python
async def _speaker_names_for(session: AsyncSession, messages) -> dict[str, str]:
    speaker_ids = {m.speaker_id for m in messages if m.speaker_id}
    if not speaker_ids:
        return {}
    rows = (await session.execute(
        select(CharacterProfile.id, CharacterProfile.name)
        .where(CharacterProfile.id.in_(speaker_ids))
    )).all()
    return {character_id: name for character_id, name in rows}
```

**验证**（同一探针，改前 → 改后）：

```
改前  assistant speaker='AI'            transcript='2026/9/20 14:26 [AI]: yo'
改后  assistant speaker='B'             transcript='2026/9/20 14:26 [B]: yo'
```

**连带修的**：`session.get(CharacterProfile, conv.character_id)` 在群对话（`character_id IS NULL`）下每次请求都触发
`SAWarning: fully NULL primary key identity cannot load any object. This condition may raise an error in a future release.`
现改为仅在 `conv.character_id` 非空时查询。新增测试 `test_group_history_never_looks_up_a_null_primary_key`
把「无该警告」锁成行为（已验证该测试对旧代码是**会红**的）。

新增测试：`test_group_message_speaks_as_its_member`、`test_removed_member_keeps_their_name`
（成员被「编辑群」移出后，他此前说的话仍显示自己的角色名）。

## A2. 1:1 上下文裁剪窗口被前缀改变（Spec 轴）

spec L136 要求 1:1 的变化是「纯增量（内容前缀）而不改变任何既有语义」，但
`backend/core/conversation_manager.py` 把渲染后的整行计入 token 预算 → 16k 窗口内能装下的消息变少。

**修法**：裁剪按**原始 `msg.content`** 估算，前缀只加在发出的内容上。新增测试
`test_trim_boundary_is_unchanged_by_the_prefix`（3×40 字符、`max_tokens=30`，带前缀仍须装下 3 条）。

## A3. 说话人被解析两遍（Standards 轴）

`api/conversation.py` 先对每条消息 `resolve_speaker()` 一遍拿 `speaker`，再调 `render_transcript()` 又解析一遍。

**修法**：`backend/core/transcript.py` 新增 `render_lines()`，一次返回 `RenderedLine(speaker, text)`；
`render_transcript()` 变成它的纯文本投影（LLM 上下文仍用它）。history API 只渲染一次。

## A4. 第三份 UserProfile 回退链（Standards 轴）

`api/conversation.py` 里复制了一份「按角色 → 回退全局」的查询链，且丢掉了原有的
`character_id is not None` 守卫，导致群对话把同一条 `IS NULL` 查询跑两遍。

**修法**：`backend/api/user_profile.py` 的私有 `_get_profile` 提升为公开的 `resolve_user_profile()`，
history API 直接复用。

## A5. 测试绕过权威迁移序列 / 断言测试专用钩子（Standards 轴）

- `test_history_transcript.py`、`test_group_model.py` 直接 import 私有 `_apply_additive_migrations`，
  绕过 ADR-0004 声明的唯一权威序列。现统一走 `database.run_migration_sequence`；
  `test_group_model` 里那份重复覆盖的迁移测试已删除（其断言在 `test_group_migration.py` 已用权威序列覆盖）。
- `database.ensure_group_schema(conn, *, before_rebuild=None)` / `run_migration_sequence(..., before_rebuild=)`
  是**只有测试在用**的钩子（生产备份在 `init_db` 里、且刻意放在迁移事务之外）。已删除该参数，
  `scripts/check_group_migration.py` 与测试同步更新。

## A6. `api/group.py` 的 N+1 与重复声明的成员顺序（Standards 轴）

`_group_payload()` 每个群 3 条查询（成员、角色名、对话），且用 `order_by(GroupMember.position)`
重复表达了模型关系上已声明的顺序。

**修法**：`selectinload(Group.members)` 让发言顺序只在 `models/group.py:21-23` 声明一处；
角色名与对话各一条批量查询（`list_groups` 从 3×N 降为固定 4 条 SQL）。
新增测试 `test_list_keeps_each_groups_own_members_and_conversations` 防串群回归。

## A7. 其余（Standards 轴）

- `CONTEXT.md`：api 清单补 `group.py`；§3.7 标题改为「Phase 1 + entity API shipped; turn orchestration pending」；
  §3.6/§7 补上 ADR-0004 的非增量重建路径与 `run_migration_sequence`；§6 点名 `core/transcript.py` 及两个函数；
  §3.7 补 speaker 解析规则。
- `backend/database.py`：函数内的 stdlib import（`shutil`、`datetime`）提到模块顶部（§6 的函数内延迟导入是为打破循环依赖，stdlib 不需要）。
- `backend/tests/conftest.py`：提供 `OLD_CONVERSATIONS_DDL` / `OLD_MESSAGES_DDL` 常量与共享
  `engine` / `session_factory` / `client` fixtures；两个新测试文件删掉本地拷贝与内联重写的 DDL。
- 前端：新增 `frontend/src/services/messageMapper.ts`（`toStoreMessage` / `withTranscript`），
  `useChat.ts` 两处重复映射 + `HistoryOverlay.tsx` 的 transcript 补齐共用同一处。

## A8. 本轮验证结果

| 项目 | 结果 |
|---|---|
| 群聊相关 6 个测试文件 | 47 passed（改动前 43：+5 新测试 −1 删除的重复测试） |
| 全量 `python -m pytest tests/ -q` | **398 passed / 29 errors**，29 个全是沙箱 `tmp_path` 的 `PermissionError`，0 逻辑失败 |
| 前端 `npx tsc -b` | exit 0（`tsconfig.tsbuildinfo` 已还原，不留构建产物 diff） |
| 迁移验收（真实 `data/companion.db` 副本） | PASS：会话 4 / 消息 77 / 记忆 994 **全部不变**，`character_id` notnull 1→0 |
| 迁移后 `PRAGMA foreign_key_check` | **18 → 18 不变**（下述违规为既有数据，非迁移引入） |

> 注：`data/companion.db` **尚未迁移**（`messages` 无 `speaker_id`、`conversations` 无 `group_id`），
> 下次启动后端会执行。`scripts/check_group_migration.py` 在本机沙箱下会因
> `tempfile.mkdtemp()` 写到受限临时区而 `PermissionError`——环境限制，非脚本缺陷。

---

# B. 未修复项

## B1. `_FALLBACK_AI_SPEAKER` 的完整触发条件（六种情形实测）

`backend/core/transcript.py:25` 的 `"AI"` 只在**对话记录行**里出现，且只在 assistant 消息
「说不出说话人」时。把六种情形都跑过真实接口：

| 情形 | 结果 |
|---|---|
| A 1:1，角色存在 | `[Rou]` ✅ |
| **C 1:1，角色名是空串 `""`** | **`[AI]`** |
| **B 1:1，角色行已被删除** | **`[AI]`** |
| D 群聊，`speaker_id` 是当前成员 | `[Rou]` ✅ |
| **E 群聊，`speaker_id` 是已被移出群的成员** | **`[AI]`** → 本轮已修（见 A1） |
| **F 群聊，`speaker_id` 为 NULL** | **`[AI]`**（spec 要求群消息必带 `speaker_id`；真为 NULL 说明写库路径有 bug，`[AI]` 正好是显眼信号，保留） |

**注意**：这跟前端那 5 处 `activeCharacter?.name || 'AI'` 是**两码事**——
后者（`MessageList.tsx:19`、`DialogueBox.tsx:54`、`ConversationSidebar.tsx:114,118`、
`HistoryOverlay.tsx:120,125`、`appStore.ts:72`）在 `activeCharacter === null` 时触发，
即**后端未连上 / 角色列表未加载 / 未选角色**（`frontend/src/hooks/useCharacters.ts:12-33`
的 catch 只 `console.error`，不设置 activeCharacter）。它跟 `core/transcript.py` 无关。

### B1.1 剩余未修：角色名无非空校验（情形 C 的来源）

角色名是**必填字段**，但没有非空校验。实测：

```
POST /api/characters {}              -> 422  Field required      （name 确实必填）
POST /api/characters {"name": ""}    -> 200  建出 name="" 的角色  ← 情形 C 可达
POST /api/characters {"name": "   "} -> 200  建出 name="   "     （非空 → 显示 [   ]，不是 AI）
PUT  /api/characters/{id} {"name":""}-> 会写进去（model_dump(exclude_none=True) 不过滤空串）
```

位置：`backend/api/character.py:12-13`（`CharacterCreate.name`）、`:22-23`（`CharacterUpdate.name`）、
`:113-114`（`for field, value in data.model_dump(exclude_none=True).items(): setattr(...)`）。

**建议**：`CharacterCreate`/`CharacterUpdate` 用 `field_validator` 做 `strip()` 后非空校验
（或 `constr(min_length=1)`），非法输入 → 400 带明确 detail，与现有 REST 错误语义一致。

### B1.2 关于 `"AI"` 文案本身

spec 未定义「assistant 消息无法命名说话人」时该显示什么。当前实现保留 `"AI"` 作为**最后兜底**
（角色被删时总得渲染点东西，不该让整个历史接口 500），已在 `transcript.py` 的注释里写明这是兜底
而非规则。若希望改成别的语义（例如显示 `未知角色`、或复用前端 i18n 的 `AI Companion`），
改 `backend/core/transcript.py:25` 一处即可，另有 `test_transcript_context.py::test_without_names_uses_defaults`
锁定该值需同步更新。

---

## B2. 孤儿数据取证（本次重点）

### B2.1 清单（只读查 `data/companion.db`，1,007,616 B）

| 对象 | 数量 | 明细 |
|---|---|---|
| 孤儿会话（`character_id` 指向已不存在角色） | **2** | `01417093-…f90f1`（2026-05-17 08:30:03 → 2026-05-23 11:15:42，37 条消息）、`0c346563-…76b11`（2026-05-23 11:16:35 → 11:17:42，2 条消息）。两者都指向已删角色 `2e4f6c4e-b748-4723-be30-b93e2d14f60f`，`title` 都是默认 `New Conversation`，`summary` 为空 |
| 孤儿消息（`conversation_id` 指向已不存在会话） | **6** | 全属已删会话 `9a243fb4-…5aced6`，2026-08-12 08:09:21 → 08:13:16，3 对 user/assistant |
| 悬空 `memories.character_id` | **11** | 11 个已删角色各 1 条（另有 2 条 `character_id IS NULL` 属正常） |
| 现存角色 | 2 | `75a25b55-…`（2026-05-15 创建，939 条 memories）、`fc5571a8-…`（2026-07-11 创建，43 条 memories） |
| 现存会话 | 4 | 2 条孤儿 + `a8e6593d-…`（13 条消息）+ `7f705570-…`（19 条消息） |
| `PRAGMA foreign_key_check` | **18** | conversations 2 / messages 6 / memories 10 |

> `llm_usage.conversation_id` **没有**声明 `ForeignKey`（`backend/models/llm_usage.py:16`），
> 所以不在 `foreign_key_check` 里。但它记录了 **63 个不同的 conversation_id**，而 `conversations` 只剩 **4 行**。

### B2.2 「删除对话没删干净」——证伪

**不是**这个原因。证据三条：

1. **代码路径本身是对的**：`Conversation.messages` 自初始提交 `e1a5c70`（2026-06-20）就带
   `cascade="all, delete-orphan"`（`backend/models/conversation.py:35-37`），
   `delete_conversation`（`backend/api/conversation.py:185-195`）用 `session.delete(conv)`，
   ORM 会连消息一起删。`@router.delete("/{conversation_id}")` 这个端点当时就已存在
   （`247608b` 只给它补了一行 docstring）。
2. **实测删除是干净的**：

   ```
   initial                        conversations=2  messages=6
   DELETE /api/conversations/cv1  conversations=1  messages=3   ← 消息确实跟着删了
   ```

3. **历史统计旁证**：`llm_usage` 里 63 个 conversation_id vs `conversations` 4 行
   → 历史上删过 **60 多个会话，只有 1 个会话**漏下了 6 条消息。

### B2.3 成因 A：聊天接口不校验 `conversation_id`（**可复现**）

- `POST /api/chat/send`（`backend/api/chat.py:33-37`）：`conversation_id` 给了就直接交给 `agent.run()`，不校验存在性。
- WS 路径（`backend/api/chat.py:76`、`:101-109`）：查会话**只为推导 `character_id`**，查不到也不拒绝。
- `backend/core/agent.py:142-146`：`conv` 为 `None` 也能继续（只是没有 summary），随后正常落库消息。
  即整条链路对「会话不存在」是**宽容**的，没有任何一层拦住。

**复现（实测）**：

```
建 1 个角色 →
POST /api/chat/send {"message":"hello ghost","conversation_id":"ghost-conv","character_id":"ch1"}
  -> 500（LLM 报账户余额不足，与本题无关）
  -> messages 表多出 1 条 conversation_id="ghost-conv"
  -> conversations 表 0 行                     ← 现场造出 1 条孤儿消息
```

**与真实数据吻合**：那 6 条**全是**孤儿，说明会话在写入**之前**就已被删；三对来回跨 4 分钟
（08:09:21 / 08:11:54 / 08:13:13），典型的「界面还挂着已删除的会话 id，又继续聊了几句」
（多窗口 / 多实例，或状态残留）。

### B2.4 成因 B：删除角色不级联（**可复现**，2 条孤儿会话的来源）

- `backend/api/character.py:121-129`：`session.delete(char)` + `commit()`，不做任何关联清理。
- `backend/models/character.py` **没有 `conversations` 关系** → ORM 层无级联。
- `Conversation.character_id` 上写的 `ondelete="CASCADE"`（`backend/models/conversation.py:16`）
  依赖 DB 外键强制，而它从不开启 → 完全失效。

**复现（实测）**：

```
initial                               characters=1 conversations=2 messages=6
DELETE /api/conversations/cv1         characters=1 conversations=1 messages=3
DELETE /api/characters/ch1            characters=0 conversations=1 messages=3
   ↑ 角色没了，会话还在、character_id 悬空、消息全在
```

**与真实数据吻合**：2 条孤儿会话正是被删角色 `2e4f6c4e-…` 的，随它们留下的还有 39 条消息；
`memories.character_id` 的 11 条悬空同理（`memories.character_id` 声明了 `REFERENCES character_profiles(id)`，
`ON DELETE` 未指定，同样不生效）。

### B2.5 共同根因

`backend/database.py:14-18` 的 engine 从未设置 `PRAGMA foreign_keys=ON`，全仓也没有 connect event
去设置它（`grep -r foreign_keys backend` 只命中迁移里的**读取**与守卫）。ADR-0004 也明确记载
"this app never enables FK enforcement"。

因此模型里所有 `ondelete="CASCADE"` / `"SET NULL"`——包括本轮新加的
`messages.speaker_id ON DELETE SET NULL`、`conversations.group_id ON DELETE CASCADE`——在 DB 层都是
**装饰性声明**：DB 没有防线，任何「父行没了、子行还在」的路径都会留下垃圾。

---

## B3. 建议修法（未实施，按收益排序）

### 修法 a（根因，收益最高）：聊天接口校验 `conversation_id`

`backend/api/chat.py` 的 `send_chat`（`:33-37`）在拿到 `conv_id` 后加一次存在性校验，
不存在则返回 404（或按 `character_id` 新建，取决于产品语义）；WS 路径（`:101`）
`if conv is None` 时向客户端发错误事件并结束本轮，不要继续落库。

```python
# backend/api/chat.py, send_chat
    conv_id = req.conversation_id
    if conv_id:
        if not await session.get(Conversation, conv_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        conv = await conv_manager.get_or_create_conversation(session, req.character_id)
        conv_id = conv.id
```

风险：前端若持有过期 `conversationId`（例如后端被重启并换了数据目录），会从「静默写孤儿」
变成「聊天报错」——需要前端在这条 404 上自动新建会话。**这正是我们想要的可见性**，
但要顺手确认 `useChat` 的发送失败分支能处理。

### 修法 b：删除角色时显式清理关联数据

在 `backend/api/character.py:121-129` 的 `delete_character` 里，先删该角色的会话与消息，
再删角色（不依赖 `ondelete`）：

```python
    conv_ids = (await session.execute(
        select(Conversation.id).where(Conversation.character_id == character_id)
    )).scalars().all()
    if conv_ids:
        await session.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))
        await session.execute(delete(Conversation).where(Conversation.id.in_(conv_ids)))
    await session.delete(char)
    await session.commit()
```

需同时决定 `memories` 的语义：`memories.character_id` 悬空会让这些记忆**永远检索不到但占库**
（当前 11 条）。建议一并 `delete(Memory).where(Memory.character_id == character_id)`，
或显式 `SET NULL` —— 这是个产品决定，不是纯技术决定。

### 修法 c：FK 强制的二选一（值得一个 ADR）

两条路，别维持现状：

- **启用**：engine 或 connect event 里 `PRAGMA foreign_keys=ON`，让所有声明生效。
  **前置工作**：必须先清理现有 18 条违规（B2.1），否则**新写入会被直接拒绝**；
  且 SQLite 的 `PRAGMA foreign_keys` 在事务内是 no-op，要在连接建立时设置（注意 ADR-0004
  的重建迁移依赖 `foreign_keys=OFF`，见 `backend/database.py:107-113` 的守卫——
  启用后该守卫会让群重建迁移在**老库**上直接抛错，必须一起重新设计）。
- **不启用**：承认它们只是文档，把模型里无效的 `ondelete=...` 删掉，改由服务层显式清理
  （即修法 a + b 的路线）。这样模型不再骗人。

---

## B4. 存量清理方案（未执行）

执行前**先备份** `data/companion.db`（迁移本身也会在启动时生成
`companion.db.bak-group-<ts>`，但清理应在迁移之后、单独备份）。清理范围：

```sql
-- 1) 2 条孤儿会话 + 它们名下的 39 条消息
DELETE FROM messages WHERE conversation_id IN (
  SELECT v.id FROM conversations v WHERE v.character_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM character_profiles p WHERE p.id = v.character_id));
DELETE FROM conversations WHERE character_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM character_profiles p WHERE p.id = character_id);

-- 2) 6 条孤儿消息（属已删会话 9a243fb4-…）
DELETE FROM messages WHERE NOT EXISTS (
  SELECT 1 FROM conversations v WHERE v.id = messages.conversation_id);

-- 3) 11 条悬空 memories.character_id（也可改成 SET NULL 保留内容）
DELETE FROM memories WHERE character_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM character_profiles p WHERE p.id = memories.character_id);
```

预期结果：`PRAGMA foreign_key_check` 返回 **0 行**。

清理前后请核对：`conversations` 4 → 2，`messages` 77 → 32，`memories` 994 → 983。
**注意**：`memories` 的删除会连带 `memory_fts` 的索引行（FTS5 外部内容表），
如果走 SQL 手工删，需要同步维护 FTS，或改用应用层删除走 `memory_store`。

---

## B5. 复现脚本要点（本次取证用，未落库）

以下均在内存 SQLite / 只读连接上跑，未修改仓库文件：

1. **AI 兜底六情形**：`create_all` → 插 4 个角色 + 3 条 1:1 会话 + 1 个群（2 成员）+ 3 条群会话
   → 用 `text("DELETE FROM character_profiles WHERE id='ghost'")` 模拟角色被删 → 逐个
   `GET /api/conversations/{id}/messages` 打印 `speaker` / `transcript`。
2. **删除行为**：1 角色 + 2 会话 + 6 消息 → `DELETE /api/conversations/cv1` → 计数 →
   `DELETE /api/characters/ch1` → 计数并查残留行。
3. **孤儿消息复现**：建 1 角色 → `POST /api/chat/send` 带不存在的 `conversation_id`
   → 查 `messages` / `conversations` 计数。
4. **空名校验**：`POST /api/characters` 分别带 `{}` / `{"name": ""}` / `{"name": "   "}`，
   `PUT` 带 `{"name": ""}`。
5. **真实库取证**（只读）：`sqlite3.connect("file:data/companion.db?mode=ro", uri=True)`，
   查孤儿会话/消息、`PRAGMA foreign_key_check`、`llm_usage` 的 distinct conversation_id。
   Windows 控制台注意设 `sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")`
   （消息内容含 emoji，GBK 会 `UnicodeEncodeError`）。

---

# C. 本批修复（已实施）

> 2026-09-23。§B 的未修项按 §B3 的建议落地；三处决策：chat 接口 404 拒绝（前端自动新建会话）、
> 删除角色连 memories 一起删、**不开启 FK 强制**而是把无效声明删掉并让服务层显式承担 —— 见 ADR-0005。

| 项 | 实施 | 落点 |
|---|---|---|
| B2.3 / B3a 聊天接口不校验 `conversation_id` | HTTP → 404；WS 连接时**与每轮各查一次** → `error{code:"conversation_not_found"}` + close 4404；前端自动新建会话并重发 | `api/chat.py`、`services/conversationRecovery.ts`、`hooks/useWebSocket.ts`、`hooks/useChat.ts` |
| B2.4 / B3b 删除角色不级联 | `_delete_character_dependents`：会话+消息、该角色的 memories、`speaker_id`→NULL、群成员资格、专属画像；变成 0 成员的群保留 | `api/character.py` |
| B3c FK 二选一 | **不开启**：模型 / `ADDITIVE_MIGRATIONS` / 重建 DDL 里无效的 `ondelete=` 全部删除，引用完整性改由服务层显式完成 | ADR-0005、`models/*.py`、`database.py` |
| 删会话留下悬空 `memories.source_conversation_id` | 显式置 NULL（记忆按角色保留） | `api/conversation.py` |
| B1.1 角色名无非空校验 | strip 后非空 → 400（创建与更新共用 `_clean_character_name`） | `api/character.py` |
| B4 存量清理 | `scripts/cleanup_orphan_data.py`：备份 → 单事务 → `foreign_key_check` 自证（`--dry-run` 可预演） | `scripts/` |

**真实库结果**（`data/companion.db`；先 `init_db` 迁移，备份 `companion.db.bak-group-20260923-093546` 与 `companion.db.bak-orphans-*`）：

| 指标 | 迁移前 | 清理前 | 清理后 |
|---|---|---|---|
| conversations | 4 | 4 | **2** |
| messages | 77 | 77 | **32** |
| memories | 994 | 994 | **984** |
| `PRAGMA foreign_key_check` | 18 | 18 | **0** |
| `conversations.character_id` NOT NULL | 1 | 0（重建迁移） | 0 |
| `memory_fts` 残留 rowid | 1 | 1 | **0** |

> `memories` 984 而非 B4 预估的 983：悬空 `memories.character_id` 实测 **10** 条（B2.1 记 11）。
> 两条真实会话（19 + 13 条消息）与两个存活角色的 939 / 43 条记忆全数保留。
> `llm_usage` 仍留 62 个已删会话 id —— 按 ADR-0005 属**有意保留**的记账日志，脚本刻意不动。

**验证**：新增 15 个测试（`test_character_api.py` 9、`test_chat_conversation_guard.py` 4、`test_conversation_delete.py` 2），
全量 `python -m pytest tests/ -q` → **442 passed**；前端 `npx tsc -b` exit 0；
`scripts/check_group_migration.py` 在已迁移的真实库上 PASS（`rebuild ran: False`，幂等）。

**有意未做**：B1.2 改 `"AI"` 兜底文案（spec 未定义该情形）；角色名长度上限（`Group` 名有 100 上限，
角色名没有，DB 也不强制）—— 都留给后续决定。
