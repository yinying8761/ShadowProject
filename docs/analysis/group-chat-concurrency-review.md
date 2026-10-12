# 群聊并发化审查（ticket 07–11 / ADR-0009）

> 日期：2026-10-10 | 关联 spec：`docs/specs/group-chat.md` | ADR：`0006`（部分被取代）、`0009`
>
> 审查范围：`origin/master`（`04d5409`，已推送的基线）→ `HEAD`（`bf0575c`），6 个未推送提交，
> 36 文件 +2105/−428。即 `git diff origin/master...HEAD`：
>
> ```
> bf0575c feat: 群聊前端并发渲染 —— 多「正在输入」+ 按序号插入 (ticket #11)
> 19bcb38 feat: 群聊驱动并发化 —— 事件协议 / 消息序号 / 审批串行 (ticket #10)
> 770327e feat: 群轮并发调度器重写 —— 全员起跑 + 事件唤醒的有界并发 (ticket #09)
> d8c9914 feat: 群轮停用「继续说」—— 每个成员每用户轮只说一条 (ticket #08)
> 47cc237 feat: 群消息单调序号 + 所有读路径按序号排序 (ticket #56)
> 2f24af2 docs: 群聊并发化 ADR-0009 + spec 修订 + tickets 07-11
> ```
>
> 两轴（Standards / Spec）**并行独立**审查，结论分列 §1 / §2，互不合并、不跨轴排序。
> §3 是**对审查结论本身的复核**：我逐条核对了引用位置，并补了可重跑的验证证据；
> 动手前请先读它——其中几条的严重度与归因需要修正。
> 待修项一律不带 `(done)` 后缀（CONTEXT.md §6 约定）。
>
> **§5 是修复落地后的第二轮**：`dac662c` 逐条处理了 §4 的建议，那里记录复核结果、
> **首次真实运行**的验证，以及一条两轴都漏掉、事后才发现的规格矛盾（群自动标题）。

---

## 0. 结论速览

| # | 轴 | 问题 | 位置 | 严重度 | 复核结论 |
|---|---|---|---|---|---|
| S1 | Standards | 同一份文档自相矛盾：一处写「ticket #10 未完成」，另一处写「Pending: none」 | `CONTEXT.md:233` vs `:278` | 硬（文档） | 成立，两行都是本次新增 |
| S2 | Standards | spec 状态行仍写「群轮并发化为待实现」 | `docs/specs/group-chat.md:3` | 硬（文档） | 成立 |
| S3 | Standards | 排序规则出现**第二份手写副本**，绕开 `Message.chronological_order()` | `backend/models/conversation.py:43` | 硬（违反明文约定） | 成立；且丢了 `coalesce` 兜底 |
| S4 | Standards | 被改文件里留下与新契约冲突的旧文字 | `group_chat.py:159-168`、`CONTEXT.md:6/:90` | 中（文档，部分系既有） | 部分成立：ADR 索引两处**本次之前就已过期** |
| P1 | Spec | 唤醒规则：spec / ADR 正文说「唤醒每个成员」，代码**排除说话者本人** | `spec:29,95`、`ADR-0009:19` vs `group_turn.py:177` | 中（文档≠代码） | 成立，但**该改的是文档**，不是代码 |
| P2 | Spec | ticket 要求的「可注入任务工厂」未实现 | `group_turn.py:328` | 低（有替代理由） | 成立，且代码里有书面理由 |
| P3 | Spec | 修订后的 spec 状态行自相矛盾 | `spec:3`、`CONTEXT.md:233/:278` | 硬（文档） | 与 S1/S2 同源 |
| P4 | Spec | 未被任何 ticket 要求的产出（前端自检脚本 + 两条 npm script） | `frontend/scripts/check_message_order.mjs`、`package.json:10-11` | 低（增量极好） | 成立，但已在 CONTEXT.md §6 自陈 |
| — | 气味 | `check_message_order.mjs:64-70` 复刻 `replaceMessageId` 的「改名 + 重排」组合 | 前端 | 判断项 | 成立（只复刻组合，共享原语已 import） |
| — | 气味 | `create_task` 硬编码，无注入缝 | `group_turn.py:328` | 判断项 | 同 P2 |
| — | 气味 | `GROUP_MESSAGE_BUDGET` 与 §7 术语「链预算」命名漂移 | `group_turn.py:52` / `CONTEXT.md:494` | 判断项 | 成立，术语表已把 M=9 定义为新含义 |

**取证（§3.3）**：`564 passed / 0 failed`；`check:message-order` 9/9 PASS。
两轴都**没有**发现功能性 bug——并发调度、序号分配、审批串行的实现与 ADR-0009 的承诺逐条对得上。

**后续状态**：S1–S4 / P1–P4 已由 `dac662c` 全部订正并复核（见 §5.1）；首次真实运行的验证见
§5.3；**残留风险只剩前端 `replaceMessageId` 零覆盖**（§5.5），另有一条两轴都漏掉的规格矛盾见 §5.4。

---

## 1. Standards 轴

> “这段代码符合本项目记录在案的编码规范吗？”

**违反成文规范**

1. **`CONTEXT.md:233` 与 `CONTEXT.md:278` 互相矛盾，且两行都是本次新增。** `:233` 写
   “the driver is still one batch at a time (**ticket #10 pending**)”，`:278` 写
   “**Pending:** none — tickets #56 / #08 / #09 / #10 / #11 delivered ADR-0009”；
   而 `:262` 已经把 `services/approval_gate.py`（ticket #10）记为已交付。
   CONTEXT.md 自述是“当前架构的权威摘要”，所以 #10 已交付，`:233` 是错的。
2. **`docs/specs/group-chat.md:3`** 仍写“**群轮并发化为待实现**”，但 07–11 全部在本批交付。
   CONTEXT.md §6 的 `(done)` 后缀约定“marks a workflow whose implementation is already
   completed”——既没加后缀，也没改状态（判断项：该 workflow 横跨 Phase 1+2，后缀不好加，
   但状态必须改）。
3. **`backend/models/conversation.py:43`** —— `order_by="Message.seq, Message.created_at"`
   是排序规则的**第二份手写副本**，绕开了 `Message.chronological_order()`。CONTEXT.md:443-448
   明文规定该函数“is the only ordering key for message reads — never
   `order_by(Message.created_at)` again … a second ordering rule anywhere is exactly what
   makes two views of one conversation disagree”。该字面量还丢掉了 `coalesce(seq, 0)`
   的旧数据兜底。**边界上算硬违规**（diff 自己的注释都承认它是“模型层的第三个读路径”）。
   顺带：`coalesce(seq,0)` 与裸 `seq` 在 SQLite 下等价（NULL 排最前），但换库后
   （PostgreSQL 的 ASC 默认 NULLS LAST）语义就分叉了。
4. **被改文件里的过期文字**：`CONTEXT.md:6` “(ADR-0001…0004 so far)” 与 `:90`
   “adr/ # architecture decision records (0001–0004)” 在新增 ADR-0009 时仍是旧值；
   `backend/services/group_chat.py:159-173` 的 `_on_turn_end` docstring 仍写“前端据此决定
   ‘正在回应’要不要收掉 … 批次结束仍由上面的 done 兜底”，与 `CONTEXT.md:360` 和
   `frontend/src/hooks/useWebSocket.ts:179-180`（“自 ticket #10 起只是**信息性**事件”）冲突。

**基线气味（一律判断项）**

- **Duplicated Code** —— `frontend/scripts/check_message_order.mjs:64-70`（`interjectionLands`）
  重新实现了 `frontend/src/stores/chatStore.ts:147-160`（`replaceMessageId`）拥有的
  “改名 + 按 seq 重排”规则，而不是调用那个 action；于是 store 漂移了这个自检**照样 PASS**。
- **Speculative Generality / 缺缝** —— ticket 09 要求“并发用可注入的任务工厂”；
  `backend/core/group_turn.py:328` 直接硬编码 `asyncio.create_task(...)`，测试无法替换它，
  与 CONTEXT §6 的“Seams / dependency injection（最高杠杆约定）”相悖。其余依赖都注入得很干净
  （`ApprovalGate(send_json)`、`speak`、`max_messages`）。
- 命名漂移：`GROUP_CHAIN_BUDGET`→`GROUP_MESSAGE_BUDGET`，而 §7 术语表仍叫「链预算 / Chain budget」。

**已核对为干净**：单一对话记录渲染器约定、`dispatch -> str` 信封、写入一律经 `add_message`、
懒加载单例 import、ticket 的 `Blocked by:` 字段、`(done)`/勾选框惯例、
“并发测试必须用文件库”这条新约定（新增 WS 测试都用 `ws_env` / `file_client`，没有测试去戳私有成员）。
**刻意未报**：`is_continuation` / `GROUP_CONTINUATION` 死代码——ADR-0009 明确背书“两步退役”，
第 1 步已落地（第 2 步暂无 ticket，与 S1 略有关系）。

---

## 2. Spec 轴

> “这份代码忠实实现了发起它的 ticket / spec 吗？”

**先验证过的正面结论**：ADR-0006:3-6 确实记录了被 ADR-0009 部分取代；ADR-0009 末段
“Delivered wire contract” 的每一条断言都对得上代码——`group_thinking{character_id,speaker,thinking}`
（`group_chat.py:218-225`）、`group_message` 带 `seq`（`:251`）、`message_ack` 带 `seq`（`:268`）、
历史接口带 `seq`（`api/conversation.py:141`）、每 socket 一把 `ApprovalGate` 且 120s
（`api/chat.py:157`、`services/approval_gate.py:20`）、`turn_end` 仅信息性
（`useWebSocket.ts:174-179`）。

**(c) 实现看着对、其实与 spec 不一致——唤醒规则。**
spec `docs/specs/group-chat.md:29`“之后**每条新消息都会唤醒**还在场且没用满额度的成员”，
`:95`“**每有一条新消息落库**，唤醒每个「空闲且未用满 K」的成员再思考一轮”；
ADR-0009:18-19“wakes every member that is idle and has not hit its cap”。
**代码排除说话者本人**：`backend/core/group_turn.py:177` 把 `woken_at[character_id]=gen` 打在
作者自己身上，于是 `ready()`（`:150-155`）不会再唤醒它；`backend/tests/test_group_turn.py:164`
把这条锁死了。只有 `CONTEXT.md:233` 记录了这处偏离，所以 **ADR 正文与它自己的标题
（“new messages wake **the rest**”）矛盾**，而 spec 修订没采纳它。
后果：spec:159 的“N×K 次思考”上界在一人独说时够不着，spec:97“想多说就等被唤醒”
只能靠**别人**发消息成立。→ 要么改 ADR/spec 文字，要么改代码。

**(a) 部分未实现。** ticket `09-concurrent-scheduler.md:24`（= spec:132）“并发用可注入的
任务工厂，测试可等待”——未实现：`group_turn.py:328` 硬编码 `asyncio.create_task`，
没有注入缝（测试之所以确定，只是因为 `run()` 会把在途全部等完）。

**(c) 修订文字过期。** `docs/specs/group-chat.md:3` 状态仍写“群轮并发化为待实现”，
而 `770327e`/`19bcb38`/`bf0575c` 就在本批交付了它。`CONTEXT.md:233` 写“(ticket #10 pending)”
而同文件 `:278` 写“Pending: none”——同一个文件，同一批改动。

**(b) 范围外产出。** spec:136 把前端验收定为“手工验收（项目无前端测试基建）”，
本批却加了 `frontend/scripts/check_message_order.mjs` 以及 `frontend/package.json:10-11` 的
`typecheck`、`check:message-order` 两条 script——没有任何 ticket 要求过。
另外 ticket `10:26`（“不再『每会话一个 turn task』”）**字面上没达成**：驱动仍然每会话只有一个
`_turn_task`（`group_chat.py:93,122-123`）；但 ADR-0009:94-95 重新立法为“批次串行、
批次内并发”，所以是那张 checklist 过期，不是代码错。

**环境限制（已排除）**：两个文件库并发测试
（`backend/tests/test_message_sequence.py::TestConcurrentWrites`）在受限沙箱下因 pytest
`tmp_path` 被拒而 ERROR（PermissionError），**不是代码失败**；详见 §3.3。

---

## 3. 对审查结论的复核

### 3.1 逐条核对结果

- **S1 / S3 / P3**：原文引用逐字核对无误（`CONTEXT.md:233`、`:278`、`conversation.py:43`、
  `spec:3`、`spec:95`、`ADR-0009:17-19`）。**成立。**
- **P1（唤醒排除说话者）**：代码行为核对无误——`_TurnState.accept()` 里
  `self.gen += 1; self.woken_at[character_id] = self.gen`（`group_turn.py:177`），
  `spend_round()` 也写 `woken_at = gen`（`:161`），`ready()` 判 `woken_at < gen`。
  **但严重度要下调、归因要改**：ADR-0009 的**决策标题**就是
  “All members start together; new messages wake **the rest**”，spec 用户故事 14 也写
  “我想让角色**在别人说话后**被唤醒再说一轮（而不是自己一口气连说）”——
  即“排除自己”正是意图。**过时的是 ADR 正文那一句和 spec 的 `:29`/`:95` 措辞**，
  不是代码；`group_turn.py:11-13` 的模块 docstring 甚至已经写明了理由。
  所以这不该按“实现错了”修，而应按“把 ADR 正文与 spec 措辞补齐”修。
- **S4**：`group_chat.py:159-168` 确为**本次新引入**的过期 docstring（`_thinking`/`turn_end`
  语义刚被本批改掉），要修。但 `CONTEXT.md:6` / `:90` 的 “ADR-0001…0004” 在
  `origin/master` 上就已经是旧值（`0005`–`0008` 早已存在），**不是本次引入**——
  可以顺手修，但别记在本次账上。
- **P2 / 气味「缺缝」**：`group_turn.py:328` 硬编码 `create_task` 核对无误。代码里有书面理由
  （`:5` 与模块 docstring：“并发用的是调用方事件循环上的 asyncio 原语 —— 原语不是 IO，
  `run()` 会把所有在途生成等完，所以测试直接 await 它就能确定地断言并发语义”）。
  这是一处**被记录下来的、对 ticket 文字的刻意偏离**，与 ticket “可注入任务工厂”不一致。
- **P4 / 气味「Duplicated Code」**：`check_message_order.mjs` 确实未被任何 ticket 要求；
  但它只复刻了“改名 + 重排”的组合，`insertBySequence` 这个共享原语是 import 的；
  且 **CONTEXT.md §6 已被本批更新为指向这条自检脚本**，属于自陈的增项而非偷偷夹带。
- **气味「命名漂移」**：`CONTEXT.md:494` 已把「链预算」重新定义为 M=9，术语并未真的失配，
  只是英文名从 `CHAIN_BUDGET` 变成了 `MESSAGE_BUDGET`——判断项，可不修。

### 3.2 两轴都没覆盖的一处

两轴都**没有**报 `MessageList.tsx` 的 `speakerMark` 抽取（说话人标识从 `speakerOf` 里提出来，
供消息与“正在输入”共用）——预期如此，这是消除重复的正确动作，我复核后确认它没有引入
`activeGroup` 为 null 时的行为变化（`speakerMark` 内部已用 `activeGroup?.members`）。

### 3.3 取证（可重跑）

| 验证 | 命令 | 结果 |
|---|---|---|
| 后端全量 | `cd backend && python -m pytest tests/ -q -p no:cacheprovider` | **564 passed / 0 failed**（28.18s） |
| 前端顺序自检 | `cd frontend && node scripts/check_message_order.mjs` | **9/9 ok，PASS**（Node v24.15.0） |
| 迁移验收 | `python scripts/check_group_migration.py` | **PASS**：`added messages.seq` → `backfilled 42 rows in 3 conversation(s)`；conversations/messages/memories 计数不变、`character_id_notnull=0`、`unnumbered=0` |

**注意**：受限沙箱下同一条命令会报 **46 errors**，全部是
`PermissionError: [WinError 5] … Temp\dsh-XXXX\pytest-of-…`——`tmp_path` 夹具写不进去，
与代码无关；受影响的是文件库并发测试（`test_message_sequence.py::TestConcurrentWrites`）、
`test_log_hub.py`、`test_mcp_manager.py`、`test_llm_config_api.py`。
放权后 564 项全绿，**新增的两个并发测试确实通过**，即 CONTEXT.md §6 新定的
“并发测试必须用文件库”这条约定是被真正执行的（内存库走 StaticPool 单连接，测不出并发）。

`frontend/package.json:10` 的 `"typecheck": "tsc -p tsconfig.json"` 我也核对了：
`frontend/tsconfig.json` 是普通配置（`include: ["src"]`），不是 solution-style 的引用清单，
所以这条 script 不是空转。

**运维提醒（活库尚未迁移）**：直接查 `data/companion.db`（只读）确认当前 `messages` 表
**还没有 `seq` 列**、42 条消息未编号 —— 也就是后端自本批提交以来还没重启过。
下次启动会走 `init_db`：探测到需回填 → 先在事务**之外**快照一份
`companion.db.bak-group-<时间戳>`（`_backup_db_before_migrations`）→ 加列 → 回填 42 行。
这是预期行为（CONTEXT.md §4 已写明“会写数据的迁移先快照”），但会**多出一个约 1 MB 的备份文件**，
介意的话记得清理。

---

## 4. 建议的处理顺序（供决定，不代表已修）

1. **S1 / S2 / P3（文档自相矛盾，5 分钟）** —— `CONTEXT.md:233` 去掉“ticket #10 pending”，
   `spec:3` 状态改为并发化已交付。
2. **P1（文档补齐，10 分钟）** —— ADR-0009:19 与 `spec:29`/`:95` 补上“**不唤醒说话者自己**
   （被自己的消息唤醒＝「继续说」改名）”，与 `group_turn.py:11-13` 的既有说明对齐。
3. **S3（一处真实的规范违反）** —— `conversation.py:43` 改回引用唯一的排序键
   （如 `order_by=...` 走一个与 `chronological_order()` 同源的可调用/表达式），别保留第二份副本。
4. **S4** —— `group_chat.py:159-168` 的 `_on_turn_end` docstring 更新为“信息性”。
5. **P2 / 气味** —— 是否给 `create_task` 加注入缝：判断项，也可以用“ADR 背书”结案。
6. **P4** —— 是否保留前端自检脚本：判断项；若要保留，建议补进某个 ticket 的 checklist
   以免下次又被当成 scope creep。

以上均**未修改任何代码或文档**（本报告是新增文件）。

---

## 5. 修复落地与运行后验证（2026-10-12）

### 5.1 `dac662c` 订正复核

`dac662c fix: 落实群聊并发化审查的订正项 (S1-S4 / P1-P4)` 把 §4 的 6 条建议全部处理了。
我逐条复核，**全部成立**（不是"声称修了"）：

| 项 | 落点 | 复核 |
|---|---|---|
| S1 | `CONTEXT.md:232` 标题改为 `concurrent (tickets #09/#10, ADR-0009)` | ✅ 与 `:277` 的 `Pending: none` 不再矛盾；全仓 grep 已无 `ticket #10 pending` |
| S2/P3 | `spec:3` 改为「群轮并发化已交付（tickets 07–11）」 | ✅ |
| S4 | `group_chat._on_turn_end` docstring 改为「信息性事件」 | ✅ 与 `CONTEXT.md:360`／`useWebSocket.ts` 一致 |
| S3 | `conversation.py:46` 改传 `Message.chronological_order()` | ✅ **见 5.2 的实测** |
| P1 | ADR-0009 决策第 1 条 + spec 两处 + CONTEXT 术语表 + ticket 09 备注 | ✅ 四处都补了「说话者自己除外」 |
| P2 | ADR-0009 记录「不注入任务工厂」的理由 | ✅ 由遗漏变成决定（**位置**见 5.4） |
| P4 | ticket 11 补记自检脚本、声明非 ticket 要求 | ✅ |

### 5.2 S3 的实测（不是"存着但没生效"）

只读探针直接问关系属性与 SQL：

```
stored order_by: ['coalesce(messages.seq, :coalesce_1) ASC', 'messages.created_at ASC']
relationship read order: ['legacy', 'seq1', 'seq2']     # 故意打乱插入顺序 + 混入无序号老行
emitted SQL: ... ORDER BY coalesce(messages.seq, :coalesce_1) ASC, messages.created_at ASC
```

关系的读序与 `chronological_order()` **一字不差**；新增的守卫测试
（`test_message_sequence.py::TestReadOrder::test_the_relationship_reads_in_the_same_order`）
是**有效**的——若 `order_by` 失效，SQLite 会按 rowid 返回 `seq2, seq1, legacy`，测试必红。
另外全后端 grep：**0 处**仍在直接按 `Message.created_at` 排序，13 处读路径全部走唯一排序键。

### 5.3 首次真实运行（后端全量 + 活库）

| 验证 | 结果 |
|---|---|
| 后端全量（含新增守卫测试） | **565 passed / 0 failed**（与 `dac662c` 提交信息所称一致） |
| 活库迁移（首次启动） | `seq` 列已加、**0 条未编号**；消息 42 → 57 |
| 序号不变量审计 | 会话内**重复序号 0 处**、未编号 0；按 seq 排后 `created_at` 逆序的会话 **0** |
| 逐行前缀剥离（本批修复） | 真实拦下一次：`[ConvManager] stripped a transcript prefix the model echoed (conversation=ffc83a63)` |

**一个重要的口径订正**：审计时我先按 `min(seq) == 1` 判，把两个会话标成"异常"——
**这个不变量是错的**。`seq` 在写入时分配、之后**永不重编号**，而 compact
（`summarize_and_trim`）会删掉最旧的消息，所以存活区间**合法地**从大于 1 开始。
日志把两处缺口都证实了：`[DAILY] compact 7f705570…: deleted 1 messages` → 该会话 seq 从 2 起；
`[GroupMemory] catch-up conv=ffc83a63 … compacted=4` → 群会话 seq 从 5 起。
真实不变量是「无重复 / 无未编号 / 与写入顺序不矛盾」，三条全过。
**别再按 `min(seq)==1` 审计**（`scripts/check_group_migration.py` 用的是 `unnumbered == 0`，是对的）。

### 5.4 两轴都漏掉的一条：群对话的自动标题（**规格与设计意图矛盾**）

**这是两轴审查和 §3 复核都没发现的**（发现于运行后的取证），记在这里以免丢失。
归因已于 2026-10-12 经用户澄清：**群标题按设计就该由用户手动改，AI 不自动命名**
（理由："让 AI 改群标题是个问题——交给谁改？还是一起改？"），未决部分留待以后有更好办法再改。

事实：

- spec **两处**承诺了群自动标题：`docs/specs/group-chat.md:59`（用户故事 18「我想群聊对话
  也有自动标题」）与 `:28`（「和 1:1 一样有对话列表、**自动命名**、新对话按钮」）。
- 实现**没有**，而且是**静默**没有：31 条消息、3 个批次之后标题仍是 `New Conversation`；
  `ensure_title` 的前置条件直接对着活库验过**是满足的**（`first_user = seq 10`、
  `first_assistant = seq 5` 都存在）；跨 9/16–10/12 的整份日志里**没有一条**标题失败记录
  （`[ConvManager] title generation failed:` 与 `[WS] background title generation failed:` 都是 print → stdout，会被 LogHub tee）。
- 同库的 1:1 会话**有**正常标题，说明机制本身是好的，坏的只是群这条路径。

**真正的风险不是"标题没生成"，而是这行代码把设计意图藏了起来**：

```python
# api/chat.py:451 —— 全项目 on_batch_finished 只有这一个用途
on_batch_finished=lambda: asyncio.create_task(_ensure_title_bg(conversation_id)),
```

`on_batch_finished` 这个钩子**只为给群起标题而存在**（grep 全仓：仅 `group_chat.py` 的定义/调用
+ 这一处传参）。而 `asyncio` 对 task 只持**弱引用**，1:1 路径特意把 task 存进 `_title_tasks`
（`api/chat.py:595-597`），群这条路径把它丢弃了。也就是说：**"不改群标题"这个正确行为，
目前是靠"那行 task 悬空所以永不触发"这个意外在支撑。**

⇒ **下一个看到 `create_task` 没存引用的人（包括本报告作者，当时差点就这么写）会"顺手补上引用"，
于是直接实现出用户不想要的行为。** 这比"标题没生成"危险得多。
（注：这个失败模式在日志里**看不见**——`Task was destroyed but it is pending!` 走 stderr，
而 LogHub 只 tee stdout + root logger。）

顺带一条支持该设计直觉的实证：就算让它跑，取材也是错的。`ensure_title` 取"第一条用户消息 +
第一条助手回复"，而群对话因 compact 砍掉头部后，实测这两条是**顺序颠倒**的
（`first_assistant` 在 seq 5、`first_user` 在 seq 10），提示词里"用户／助手"的配对与实际时序不符。

**建议（方向：按设计意图收口，不去"修复"它）**：

1. 改 spec 两处：把"群聊也有自动标题"改成"群标题由用户手动改（AI 不自动命名）"，
   并记下未决问题（谁改／一起改）。
2. 给这个决定一个落点：并进 `docs/adr/0007-group-chat-as-a-session-kind.md`
   （群不是 1:1 的一个具体推论），或单开一个短 ADR。
3. 处理掉那个钩子：删掉 `on_batch_finished`，或至少留一行注释写明"**故意**不生效"，
   让"不改群标题"成为**显式**行为而非悬空 task 的运气。

### 5.5 仍然开着的

- **前端 `replaceMessageId` 仍零覆盖**（§1 的 Duplicated Code 气味）：提交信息自陈"前端本轮未改动"。
  即"思考中插话 + 成员回复几乎同时到"的归位逻辑，**真正运行的那份代码依旧没被验证过**
  （被验证的是一份平行副本）。这是本报告里**残留风险最高**的一项。
  修法：把"改名 + 按 seq 归位"抽成 `utils/messageOrder.ts` 的纯函数，store 与自检脚本都调它。
- **P2 的决定落在 ADR-0009 的 `Consequences` 末段**，而它本质属于 Decision／Considered Options
  （"rejected: injectable task factory"）。`docs/agents/domain.md` 未强制模板，该 ADR 本来就有一条
  "Delivered wire contract" 在 Consequences 里，故**可接受**，只是不够对味。
- **diff 卫生**：ticket 09 / 11 被改过但末行仍无换行；`docs/specs/group-chat.md` 仍无 `(done)` 后缀
  （CONTEXT.md:87 约定"tickets 写完即标"）。
