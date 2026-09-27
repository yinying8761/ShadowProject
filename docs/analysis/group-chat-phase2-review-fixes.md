# 群聊 Phase 2 审查修复清单（ticket 04 / 05 / 06 + 报告 §B 落地）

> 日期：2026-09-22 | 关联 spec：`docs/specs/group-chat.md` | ADR：`0004`–`0008`
>
> 审查范围：`118f56b`（上一轮 §A 修复提交）**之后**的 5 个提交 → `HEAD`（`e725fc7`），59 文件 +4830/−542
> —— 即 `118f56b..HEAD`，**不含** `118f56b` 自身的改动（它只作为行为核对基线，见 §3.2）：
>
> ```
> f1e4bad fix: 群聊审查修复 §B — 拒绝孤儿消息 / 删除级联 / 服务层承担引用完整性
> ec3100f feat: 群轮编排 + WS 接线 (ticket #53)
> 908d787 feat: 群聊前端界面 (ticket #05)
> 9f3eb58 feat: 群记忆与 compact —— 打开群对话时补账 (ticket #55)
> e725fc7 chore: 清掉记忆提取器里遗留的无用 import（审查发现）
> ```
>
> 两轴（Standards / Spec）并行审查，本文记录**尚未修复**的项。§3 是**对审查结论本身的复核**——
> 3.1/3.2 指出原报告的两条前提与事实不符，3.4 记录本文自身被订正的 7 处；动手前请先读它，别按错前提改代码。
> 待修项一律不带 `(done)` 后缀（CONTEXT.md §6 约定）。
>
> **第二轮（复核后修补）**：对本文这一批改动又做了一轮两轴审查，发现的问题已就地修掉，
> 逐条见 **§7**——其中包括两轴都漏掉的一个用户可见回归（N2）与一处低危竞态（N3）。

---

## 0. 结论速览

| # | 轴 | 问题 | 位置 | 严重度 | 状态 |
|---|---|---|---|---|---|
| S1 | Standards | transcript 直通别名（Middle Man，**不是**第二份渲染器） | `memory_extractor.py:148-154` | 低 | ✅ 已删 |
| S2 | Standards | transcript 组装逻辑逐字重复两份 | `conversation_manager.py:298-303`、`group_memory.py:187-197` | 中 | ✅ 已收口 |
| S3 | Standards | 记忆通知重复推送（逐条 + 汇总） | `memory_extractor.py:293-297` | 中 | ✅ 已改 |
| S4 | Standards | 「纯逻辑」契约与驱动的实际用法不一致 | `group_turn.py:1-18`、`group_chat.py:100` | 低 | ✅ 文档已对齐 |
| S5 | Standards | `GROUP_TTS_ENABLED = false` 永假开关 | `useTTS.ts:14,26,50` | 低（**spec 明文要求保留，见 §1.5**） | ⏸ 按 spec 不动 |
| S6 | Standards | 默认会话标题前端后端各有一份 | `ConversationList.tsx:5` | 低 | ✅ 已改 |
| S7 | Standards | `_store_and_push` 命名与 "push" 的另一含义冲突 | `group_chat.py:168` | 低 | ✅ 已改名 |
| S8 | Standards | 多余空行残留 | `memory_extractor.py:76-79` | 极低 | ✅ 已删 |
| P1 | Spec | 合并窗 + 漏 `occurred_on` 时记忆时间退回批次最早一天 | `group_memory.py:44-50,91`、`memory_extractor.py:156-167` | 中 | ✅ 修法 1（日志 + 测试） |
| P2 | Spec | 角色名校验顺手改名落库（超出报告要求） | `character.py:37-46,77,133` | 低 | ✅ 已写进 CONTEXT |
| P3 | Spec | `done`（群轮结束）要等整个插话队列跑完 | `services/group_chat.py:135-136`、`group_turn.py:121-139` | 低 | ✅ 新增 `turn_end` |
| N1 | 复核补充 | `daily_greeting_skip` 不分 `reason` 一律记「今天已问候」 | `useWebSocket.ts:131-134` | 低（当前无实际影响） | ✅ 已按 reason 分支 |
| N2 | 第二轮 | 改名后本地 `is_default_title` 陈旧 → 侧栏仍显示 'New Conversation' | `ConversationList.tsx:90-93` | 中（用户可见回归） | ✅ 第二轮已修（§7.2） |
| N3 | 第二轮 | `turn_end` 的 `pending=false` 之后仍可能马上开一轮 | `group_turn.py` / `group_chat.py:_on_turn_end` | 低 | ✅ 只补文档（§7.3） |

> 除 S5（spec 要求保留开关，故意不动）与 P1 的修法 2/3（涉及取舍，留待单独 ticket）外，
> 12 条全部落地；逐条改动与验证见 **§6**，第二轮复核后的修补见 **§7**。

验证基线：全量 `python -m pytest tests/ -q` = **469 passed / 38 errors**，38 个错误逐条都是
沙箱 `tmp_path` 的 `PermissionError`（`-p no:cacheprovider`），**0 逻辑失败**。
（修复后同沙箱复跑 = **475 passed / 39 errors**：多出的 1 个 error 是本轮新增的 WS 用例落在
同一个 `tmp_path` 依赖文件里，见 §5。）

---

## 1. Standards 轴

### 1.1 S1 — transcript 直通别名（判断项；done）

`backend/services/memory_extractor.py:148-154`：

```python
    @staticmethod
    def render_diary_transcript(messages) -> str:
        """1:1 用的行格式（"谁说的"由角色标签给出，不需要名字）。

        实现在 `core.transcript.render_role_labels` —— 对话摘要也用同一份。
        """
        return render_role_labels(messages)
```

纯直通别名（Middle Man），全仓只有 `:321` 一个调用点。**它并不是"第二份渲染器"**：CONTEXT.md §6
「**One transcript renderer**：…never a second copy of the formatting rules」禁的是**复制格式规则**，
这里是转调，规则仍然只有一份 —— 所以按"判断项"处理，别按"硬性违规"写提交信息
（若认为这个具名入口是"1:1 日记口径"的有意 seam，保留也说得通，但注释要写明它是 seam 而非转发）。

**修法**：删掉该方法与 `:321` 的调用，直接调 `core.transcript.render_role_labels(messages)`
（`:22` 已经 import 了它）。

### 1.2 S2 — transcript 组装逻辑重复两份（硬性；done）

`backend/core/conversation_manager.py:298-303`：

```python
            speaker_names = await self.resolve_speaker_names(session, old_messages)
            transcript = "\n".join(
                line
                for line in render_transcript(old_messages, speaker_names=speaker_names)
                if line
            )
```

`backend/services/group_memory.py:187-197`：同一形状逐字重复（还多开了一次 session，只为拿名字）：

```python
            if messages:
                async with self._sessions() as session:
                    speaker_names = await self._conversations.resolve_speaker_names(
                        session, messages
                    )
                # 群记录走共享渲染器：保留"谁说了什么"…
                transcript = "\n".join(
                    line
                    for line in render_transcript(messages, speaker_names=speaker_names)
                    if line
                )
```

**修法**：在 `backend/core/transcript.py` 加一个共用函数，两处都调它：

```python
def render_transcript_text(messages, *, speaker_names=None, ...) -> str:
    """`render_transcript` 渲染成**一段文本**，丢掉工具管线留下的空行。

    群摘要（ConversationManager.summarize_and_trim）与群补账（group_memory）
    共用 ——「渲染 → 丢空行 → join」只此一份。纯函数。
    """
    return "\n".join(line for line in render_transcript(...) if line)
```

则 `conversation_manager.py:299-303` → `transcript = render_transcript_text(old_messages, speaker_names=speaker_names)`；
`group_memory.py:192-197` → `transcript = render_transcript_text(messages, speaker_names=speaker_names)`。
（已按此改完；实现用显式关键字形参而非 `**kwargs`，保持类型可读。）

> **订正（见 §3.4-7）**：原文说 `group_memory.py:188-191` 那次"只为取名字而开的 session"可以去掉 ——
> 这句不成立。`resolve_speaker_names` 要查库，而渲染器必须保持纯函数（`core/transcript.py` 的约定：
> 不碰 DB / 时钟），所以那次 session 是必要的，本轮**保留**（代码里已注明理由）。

### 1.3 S3 — 记忆通知重复推送（硬性；**注意：行为早于本次改动存在**；done）

`backend/services/memory_extractor.py:293-297`：

```python
        if stored and notify:
            from services.memory_service import push_memory_notification

            push_memory_notification(conversation_id, len(stored))
```

`MemoryStore.add` 在 `notify=True`（默认）时已经**逐条**推过（`memory_store.py:170-171` 去重路径、
`:189-190` 新增路径），而 `store()` 的默认参数也是 `notify=True`（`:246`），并在 `:289` 透传。
于是 1:1 路径 `extract_and_store` 会先逐条推 N 次、再汇总推 1 次，同一个会话被计两遍。
`notify` 一个参数同时表示"逐条推"和"由调用方汇总"，两种含义混在一起，且无测试覆盖。

**修法（二选一，建议前者）**：

- **让 `MemoryStore.add` 不再推送**，推送只由调用方做一次：`store()` 末尾那一处即可覆盖
  （群聊已是 `notify=False` + `group_memory.py:228-231` 汇总一次），1:1 也自然变成一次。
  注意这会把 1:1 从「N+1 次通知」变成「1 次通知」——前端 `addMemoryNotification(count)` 是累加的，
  行为更合理，但属于可见变化，需在提交信息里写明。
- 或者把参数拆成两个（`notify_each` / `notify_batch`），语义显式化。

**别让 `MemoryStore.add` 的推送悄悄消失**：它还有第二个调用方 —— `memory_service.add_memory`
（`memory_service.py:93`）← `tools/memory_tools.py:54`（`save_memory` 工具）。这条路靠
`agent.py:409-410` 在工具成功后显式发的 `{"type": "memory_updated", "count": 1}` 兜住，所以按上面
第一条改**不会**掉通知；顺带还消掉了它的重复：现在工具当次已显式发一次，而 `add()` 推进队列的那一条
会被**下一轮**开头的 `agent.py:154-156` 再消费一次。

### 1.4 S4 — 「纯逻辑」契约与驱动的实际用法不一致（硬性，但前提需更正，见 §3.1；done）

`backend/core/group_turn.py:1-18` 的模块 docstring 与 ADR-0006 都声明编排器「不碰 DB / 网络 / 时钟」
（ADR-0006 的原话是 "the LLM, the DB, the clock and the websocket are injected or absent"）；
而**把 `submit()` 的返回值定义为"调用方该不该自己 await run()"的信号的，只有那段 docstring**（ADR 里没有）：

```python
    def submit(self, text: str) -> bool:
        """…返回 True 表示已经有在途群轮（它会自己接力成新轮）；False 表示当前空闲 ——
        调用方需要 await `run()` 把队列跑掉。…"""
```

而驱动 `backend/services/group_chat.py:100` 只用 `self._orchestrator.submit(text)`、**忽略返回值**，
改用自己那套 `self._turn_task` 记账（`:101-102`）。不是 bug —— 安全的真正来源是 `run()` 里
「`take_pending()` 取空 → `_in_flight=False`」这一段**没有任何 await**（`group_turn.py:131-137`），
所以驱动那句"`_turn_task is None or done()` 就起任务"不可能与在途轮重叠；而 `group_turn.py:126-128`
的不可重入守卫真被触发时是**抛错**，不是悄悄串行。真正对不上的只是**文档**：定义"返回值 = 调用方
该不该自己 `await run()`"的只有 `group_turn.py:106-114` 的 docstring，**ADR-0006 并没有定义这个
返回值**（它只说 `submit(text)` 是同步的），而且 ADR-0006 第 30-34 行本来就写着
「**The driver owns serialization** … starts at most one turn task per conversation」——
"真实契约"已经记在 ADR 里，落伍的是那段 docstring。

**修法**：只改 `group_turn.py` 的 docstring 就够（"驱动自管任务句柄；`submit()` 只负责入队，
返回值仅表示入队时是否已有在途轮"），ADR-0006 不必动。若反过来要让驱动改用 `submit()` 的返回值
决策，务必保住上面那条"取空与置空闲之间无 await"的性质（否则会真开出第二个任务、撞上守卫抛错）。
**不要**按审查原话去删 `has_pending()`（见 §3.1）。

### 1.5 S5 — 永假 TTS 开关（判断项，**spec 覆盖基线，建议不改**；⏸ 本轮不动）

`frontend/src/hooks/useTTS.ts:14`：`const GROUP_TTS_ENABLED = false;`，配 `:26`、`:50` 两处守卫。

基线会把它读作 Speculative Generality，但 **spec L119 明文要求保留**：
「**TTS 在群聊禁用**（见 Out of Scope），但保留开关/挂钩以便将来回归」。
按"文档化的仓库标准覆盖基线"的规则，**这条应当抑制**。留档仅供知情，不建议动手。

### 1.6 S6 — 默认会话标题有两份（判断项；done）

`frontend/src/components/chat/ConversationList.tsx:5`：`const DEFAULT_TITLE = 'New Conversation';`
（另在 `:74`、`:80` 用于显示与进入编辑态判断），而后端有四处（分属三个文件）拥有该默认值
（`models/conversation.py:26`、`api/conversation.py:22`、`conversation_manager.py:141` 与 `:182`）。

这是从旧侧栏带过来的重复（不是本次新引入的规则），但新组件正是收口的时机。

**修法**：`GET /api/conversations` 已经把 title 下发了；前端不必再知道默认值——把"是否是默认标题"
的判断改成后端下发一个布尔（或让前端用空 title 表示未命名），或在 `services/api.ts` 里集中一处。

### 1.7 S7 — `_store_and_push` 命名（判断项；done）

`backend/services/group_chat.py:168`：该方法做的是「落库 + 发一条 WS 帧」：

```python
    async def _store_and_push(self, utterance: GroupUtterance) -> None:
        """确实说了的：落库（`speaker_id` = 该角色）再推送。"""
```

但本 diff 里 "push" 已被 `push_memory_notification`（记忆通知推送）占住。
**修法**：改名 `_store_and_send`。

### 1.8 S8 — 多余空行（判断项；done）

`backend/services/memory_extractor.py:76-79`，`_SYSTEM_BY_SCOPE`（`:71-74`）与 `_RULES_BY_SCOPE`（`:80-83`）
之间留了四个空行。上次 `e725fc7` 清理遗留 import 时漏掉了这几行。

---

## 2. Spec 轴

### 2.1 P1 — 合并窗 + 漏 `occurred_on` 时记忆时间退回批次最早一天（严重度：中；修法 1 done）

spec 的依据分属两条 bullet（别当成一句引）：

- L109 结尾：「…**同一批事实存储 N 份**（每个成员各一份，`character_id` = 成员），记忆时间用**消息真实日期**。」
- L110：「**补账**：锚点 = `Conversation.last_extract_at`；隔数日再打开时一次性处理该窗口内的全部内容。」

ticket 06 同义：「记忆 `created_at` 用**消息真实日期**」。

现状：

- `backend/services/group_memory.py:44-50` 定义了一个批次只有**一个** `created_at`：
  ```python
      created_at: datetime     # 这批记忆的 created_at（= 这批最早那天的 00:00 UTC）
  ```
- `:91` 构造时取批内**最早**一天：`created_at=datetime.combine(first, time.min, tzinfo=timezone.utc)`；
  超过 `MAX_BATCHES = 7`（`:38`）时，最老的那些天会被**合并成一批**（`:75-78`）。
- `backend/services/memory_extractor.py:156-167` 的 `_item_created_at()` 在 LLM 未给 `occurred_on`
  或格式不可解析时，返回的正是这个 fallback：
  ```python
          if not item.occurred_on:
              return fallback
          try:
              day = _date.fromisoformat(str(item.occurred_on).strip()[:10])
          except ValueError:
              return fallback
  ```

**实测探针**（anchor `2026-09-01`、until `2026-09-23`）：`day_count = 23 > 7` → `merge_count = 17`
→ 首个窗口为 `since_date=2026-09-01, before=2026-09-18, created_at=2026-09-01`。
于是**该窗口里 09-17 的消息，只要模型漏了 `occurred_on`，就被存成 09-01**。

**缺的测试是"合并窗 + 漏字段"这个组合，不是"跨日"**：`test_group_memory.py:248`
（`test_each_days_memories_are_dated_to_that_day`）本来就覆盖**跨日分窗**、`:63-76`
（`test_long_absence_merges_the_oldest_days_into_one_batch`）甚至专门锁定了**合并批**的
`created_at` / `before`；`:231`、`:419` 才是单日用例。真正没有断言的是**端到端**那一步：
合并窗里的消息在模型漏 `occurred_on` 时会被存成批次最早一天。群聊提示词确实要求
`occurred_on`（`memory_extractor.py:62`「从那几行的时间戳里取」），`CONTEXT.md:252-255`
也把"合并窗靠 `occurred_on` 定日期"写成了设计，`ExtractedMemory.occurred_on` 的 docstring
（`memory_extractor.py:93`）更把"拿不到就退回窗口那天"记为**设计内的 fallback** ——
所以这是"已知回退路径缺护栏"，不是普遍性违约，**严重度按中看**。

**修法（按代价从小到大）**：

1. **让缺口可见**：多日批次里出现无 `occurred_on` 的条目时打一条日志（现在的
   `[GroupMemory] catch-up …` 汇总行看不出这件事），并补一个用假 LLM 的多日批次测试，
   明确锁定"拿不到真实日期时退回批次最早一天"这个已知行为——至少别让它静默。
   补测请直接构造 `day_count > MAX_BATCHES` 的窗口（`batch_windows(anchor, until, max_batches=3)`
   这种形状，参照 `test_group_memory.py:63-76`），别再按"单日批次"写用例。
2. **消掉合并**：把 `MAX_BATCHES` 的语义从"最多 7 批"改成"最多 7 天，超出的老内容不进提取但也不 compact"
   ——但这会与 `:159-161`「只往前数几天的话，更老的消息会被后面的 compact 直接删掉」的设计初衷冲突，
   需要重新想清楚，别草率改。
3. **按天拆批**：允许批次数随天数增长（代价是 LLM 调用次数线性上升），日期自然精确。

> 我倾向先做 1（低风险、把 silent 变 visible），2/3 涉及取舍，建议单独一个 ticket 决策。

### 2.2 P2 — 角色名校验顺手改名落库（超出报告要求；done：保留 strip 并写进 CONTEXT）

报告 §B1.1 只要求「非法输入 → 400」（实测原为：`POST {"name": ""}` → 200 建出空名角色）。
实现（`backend/api/character.py`）做到了 400，但顺带把名字 `strip()` 后**落库**：

```python
37: def _clean_character_name(name: str) -> str:
...
77:     name = _clean_character_name(data.name)          # create
133:         fields["name"] = _clean_character_name(fields["name"])   # update
```

即 `POST {"name": " A "}` 现在存成 `"A"`（原来是 `" A "`）。需求没要求改写，属实现自由发挥；
好在方向合理（避免 `[   ]` 这种渲染），但**属于可见行为变化**，需确认是否有意为之。
**修法**：要么保留并在提交信息/`CONTEXT.md` 里写明"角色名会去首尾空白"，要么只校验不改写。

### 2.3 P3 — 群轮结束信号要等整个插话队列跑完（done：新增 `turn_end`）

`backend/services/group_chat.py:135-136` 的 `_run_turns` 只在 `orchestrator.run()` 全部返回后才发 `done`：

```python
            if not cancelled:
                await self._safe_send({"type": "done", "group": True})
```

而 `run()` 会一直循环到 `take_pending()` 为空（`group_turn.py:131-135`），也就是把**排队的所有插话轮**
跑完才发这一个 `done`；前端 `frontend/src/hooks/useWebSocket.ts:80-87` 正是群聊这条分支清 `isStreaming`
的地方（`error` 分支 `:172-178` 也会清，但正常路径只此一处）：

```ts
            case 'done':
              clearRetryState();
              if (!data.message_id && data.group) {
                setStreaming(false);
                bumpConversationList();
```

后端语义是对的（旧轮确实立即收束成新轮，`group_turn.py:146-151`、`:161-165`），但**信号粒度**是"批"而不是"轮"：
整个排队批次期间那个"正在回应"的布尔态一直不清。注意**发言本身是逐条可见的**——每条
`group_message` 在编排器判定的当下就推送（`group_chat.py:180-188`）并落进消息列表
（`useWebSocket.ts:140-156`）；停住的只是 `isStreaming` 这一个状态（输入框占位文案
`InputBar.tsx:53-56` 就是为这个场景写的），加上群聊不流式（`token` 被刻意吞掉，
`group_chat.py:163-165`：那可能是 `<silent>`），观感上像"角色们集体沉默"。

**影响有限**：`InputBar.tsx:20` 的 `canSend = canChat && (!isStreaming || !!activeGroup)` 允许群聊在
streaming 中继续发消息，不阻塞操作。spec 也没有规定 WS 事件形状（L101/L36-57 讲的是编排语义）。

**修法**：把 `done` 提到每轮边界发一次，或新增一个 `turn_end` 事件让前端按轮收状态。
现成的 `on_turn_finished`（`group_chat.py:61,133-134`）目前绑的是"生成会话标题"那件事
（`chat.py:468` 的 `_ensure_title_bg`），要复用它得先把"标题一次性"与"每轮收状态"两件事分开，
别顺手让标题生成跟着每轮触发。

> **本轮采用后者**（见 §6）：新增 `turn_end{group, pending}`，批次结束仍由 `done` 兜底；
> 上面那个批级回调顺手改名 `on_batch_finished`，把"批"和"轮"在名字上就分开。

---

## 3. 对审查结论的复核（动手前必读）

### 3.1 更正：Standards 原报告称"驱动读 `has_pending()`"——不成立

`backend/services/group_chat.py` 全文**没有** `has_pending()` 调用，只有 `:100` 的
`self._orchestrator.submit(text)`。`has_pending()`（`group_turn.py:102-104`）唯一的调用方是测试
`backend/tests/test_group_turn.py:299`（`assert orch.has_pending() is True`）。
所以**不要**按"驱动绕过公开接口"去改，站得住的内核只有 §1.4 写的"忽略 `submit()` 返回值"。

### 3.2 更正：重复推送（S3）不是本次引入的回归

在固定点 `118f56b` 上核过：`memory_store.py:170`、`:189` 当时就已经逐条推，
`memory_extractor.py:240` 当时就已经汇总推。本次只是把内联循环抽成了 `store()`，**行为未变**。
仍值得修（S3），但不要当成"本次改动引入的 bug"来写提交信息。

### 3.3 补充（两轴都没提）：N1 `daily_greeting_skip` 不分 `reason`（done）

`backend/api/chat.py:220` 新增了群聊的拒绝理由：

```python
            await websocket.send_json(
                {"type": "daily_greeting_skip", "reason": "group_conversation"}
            )
```

而前端 `frontend/src/hooks/useWebSocket.ts:131-134` 对所有理由一视同仁地写"今天已问候"：

```ts
            case 'daily_greeting_skip':
              // Server confirms greeting already done — mark complete
              localStorage.setItem('daily_greeting_date', new Date().toISOString().slice(0, 10));
```

触发链是真实的：`useDailyGreeting.ts:17-58` 在**每次 `currentConversationId` 变化时**都发
`daily_greeting`（进群换了对话 id → 也会发），后端回 `group_conversation`，前端就写下当天日期。

**当前无实际影响**：我查过 `daily_greeting_date` 这个 key 在前端**只有写、没有任何读**
（读取点为 0，全仓仅 `useWebSocket.ts:93`、`:133` 两处 `setItem`），所以是惰性状态。但若将来它重新
被当作"今天已问候"的守卫，**进一次群聊就会把当天 1:1 的问候吃掉**。
（`docs/specs/daily-greeting-double-fix(done).md:59` 当初就要求 `useDailyGreeting` 在发送前
`getItem('daily_greeting_date')` 跳过——那个读取点现在已不在代码里，key 因此成了死状态；
N1 说的"将来重新被当作守卫"正是这条路。）

**修法**：`case 'daily_greeting_skip'` 按 `reason` 分支（只有 `already_greeted` 才记日期），
或在那一行补注释说明这个 key 目前无人读。同时可考虑 `useDailyGreeting` 在群对话下不发请求。

### 3.4 本文的事实订正（复核后已就地改正）

§1/§2 的代码事实都对着 `HEAD` 与固定点 `118f56b` 逐条核过（行号、片段、调用方、行为链）。
以下 7 处是**本文自身**先前写错、现已改正的，记录在此免得后来者按旧版理解：

1. **P1 的测试论据**：原写「`test_group_memory.py:231/248/419` 只覆盖单日批次」不成立——
   `:248` 是跨日用例、`:62-69` 已锁定合并批（原文写 `:63-76`，为近似值）；缺的是"合并窗 + 漏 `occurred_on`"的
   **端到端**断言。P1 严重度随之从「高」下调为「中」。
2. **P1 的 spec 引文**：原把 L109 结尾的「记忆时间用消息真实日期」与 L110 的「补账」拼成
   一句并挂在 L109 名下，已拆回两条 bullet。
3. **§1.4 的 ADR 表述**：ADR-0006 **没有**定义 `submit()` 的返回值信号（只有
   `group_turn.py` 的 docstring 定义了），且它本来就写明"驱动拥有串行权"；要改的只有那段 docstring。
4. **S1 的定性**：直通别名不是"第二份渲染器"（CONTEXT.md §6 禁的是复制格式规则），
   已从「硬性」改为「判断项」、严重度中→低。
5. **§2.3 的两句影响描述**：`isStreaming` 并非"只在这个事件上清"（`error` 分支也清）；
   「用户看不到任何进展」也不准确——`group_message` 是逐条实时可见的。
   另补了"复用 `on_turn_finished`"的注意事项（它现绑着标题生成）。
6. **§1.6 的计数**：后端默认标题是 4 处（分属 3 个文件），不是 3 处。
7. **§1.2 的"多余 session"建议**：原说 `group_memory.py:188-191` 那次只为取名字而开的 session
   可以去掉——不成立。名字要查库（`resolve_speaker_names`），而渲染器必须是纯函数
   （`core/transcript.py` 明文"不碰 DB / 时钟"），所以那次 session 必须留着；本轮保留并在代码里注明。

补充三条正文里没有、但改动前值得知道的：
- **S3 的修法要连工具路径一起看**（已补进 §1.3）：`MemoryStore.add` 的第二个调用方是
  `save_memory` 工具，它靠 `agent.py:409-410` 显式发事件，所以去掉 `add()` 的推送不会掉通知。
- **§0 的范围**已明确为 `118f56b..HEAD`（**不含** `118f56b` 自身的改动；§A 那批只作为 §3.2 的核对基线）。
- **S4 的"驱动记账很安全"靠的是无 await 段**：`run()` 里「`take_pending()` 取空 → `_in_flight`
  置回 False」之间没有 await，所以驱动的 `_turn_task` 判空不会与在途轮重叠；本轮新增的
  `on_turn_end` 回调就挂在这段之前（先回调、后取队列），取队列仍在回调之后，故此性质不变。

---

## 4. 已核对正确，不要重复改（本轮审查确认）

- **串行、永不并行**：`group_turn.py:126-128` 的 `RuntimeError` 守卫 + `backend/tests/test_group_turn.py`。
- **顺序 = 成员 `position`**：`group_chat.py:227` 用关系声明的顺序（`selectinload(Group.members)`）。
- **跳过（`<silent>`）不落库不推送**：`group_turn.py:41-55` 的 `is_silent`（夹带内容的按人话处理），
  `group_chat.py:155-157` 的 `persist_reply=False` + 编排器判定后才 `_store_and_send`（S7 后新名字）。
- **链预算 6 到顶整群静默**：`group_turn.py:152-153`、`:26`。
- **插话不打断在途流、队列合并为一轮、预算重置**：`group_turn.py:132-134`、`:146-151`、`:161-165`。
- **群聊不启动主动陪伴、不响应每日问候**：`chat.py:194-206`（`proactive_session = None`）、
  `chat.py:218-222`；测试 `test_group_chat_ws.py:267-271`。
- **每角色上下文 = 人设 + 自身记忆 + 全量工具**：`agent.py:49`（`system_suffix` 形参）、
  `:268-270`（追加到 system prompt 尾部）、`:177`（`memory_query` 取本轮用户消息）、
  `:294-305`（`persist_reply=False` 时 `_finish` 把文本交回调用方而不落库），审批按角色归属。
- **群摘要走时间戳渲染、1:1 保持原标签格式**：`conversation_manager.py:296-305` 按**会话种类**分支
  （注释说明：不能按"有没有解析出名字"判断，否则角色被删时会退回 1:1 格式丢掉说话人）。
- **报告 §B 全部落地**：§B1.1 → 400（`character.py:37-46`）；§B3a → HTTP 404
  （`chat.py:43-58`）+ WS `conversation_not_found`/4404（`chat.py:119-125`）+ 每轮复检（`chat.py:520-530`）
  + 前端 `conversationRecovery.ts`；§B3b → `_delete_character_dependents`（`character.py:141` 起，
  含 conversations / messages / memories / speaker_id 置空 / 群成员 / 专属画像）；
  §B3c → 保持 FK 关闭 + **`backend/models/`、`ADDITIVE_MIGRATIONS`、重建 DDL 里已无任何 `ondelete=` 残留**
  + ADR-0005 记录该决定；§B4 → `scripts/cleanup_orphan_data.py`（备份 → 单事务 → `foreign_key_check`
  与 FTS 残留自证 → 刻意不动 `llm_usage`）。
- **前端**：进群/退群模式记忆与恢复（`appStore.ts:95`、`:100-111`）、成员列表（`GroupSidebar.tsx`）、
  对话列表（`ConversationList.tsx`）、「新对话」、复用单条 WS；群说话人**没有**在前端维护第二套规则
  （`messageMapper.ts:19-20` 与 `useWebSocket.ts:151-152` 都携带后端解析的 `speaker`，
  `MessageList.tsx:38` 优先用它，仅在缺失时回退到当前成员/角色表）。

---

## 5. 验证方式

- 全量回归：`cd backend && python -m pytest tests/ -q -p no:cacheprovider` → **469 passed / 38 errors**；
  38 个错误**逐条**为 `PermissionError`（tmp_path 沙箱），已用 `Select-String "^E "` 逐行确认无非
  `PermissionError` 的错误。本机沙箱的已知限制，非代码缺陷。
  （复核时原样复现：`469 passed / 38 errors in ~20s`，`Group-Object` 归类 38/38 = `PermissionError`
  于 `…\Temp\dsh-*\pytest-of-yin_ying`。）
- 相关子集（改动后自测用）：`tests/test_group_turn.py tests/services/test_group_memory.py
  tests/test_group_chat_ws.py tests/core/test_agent_group_turn.py tests/test_compact.py
  tests/test_chat_conversation_guard.py tests/test_character_api.py tests/test_conversation_delete.py`。
- 前端类型检查：`cd frontend && npx tsc -b`（会改写被跟踪的 `frontend/tsconfig.tsbuildinfo`，
  跑完记得 `git checkout -- tsconfig.tsbuildinfo`，别把构建产物带进提交）。
- P1 的复现：直接对 `batch_windows(datetime(2026,9,1,tzinfo=utc), datetime(2026,9,23,tzinfo=utc))`
  打印窗口——共 7 窗，首个窗口为 `since_date=2026-09-01, before=2026-09-18, created_at=2026-09-01`
  （复核时实跑一致）。

---

## 6. 落地记录（12 条 = 11 改 + 1 故意不动）

改动都在**不变行为语义**的前提下把"重复/歧义/静默"收掉；每条都配了锁定行为的测试。

| # | 改了什么 | 位置 | 锁它的测试 |
|---|---|---|---|
| S1 | 删掉 `render_diary_transcript` 直通别名，改调 `render_role_labels` | `memory_extractor.py`（删 7 行 + 调用点） | 既有 `test_memory_extractor.py`（1:1 提取路径） |
| S2 | 新增 `render_transcript_text`（渲染 → 丢空行 → join），两处调用点收口 | `core/transcript.py`、`conversation_manager.py:299`、`group_memory.py:226` | **第二轮补**：`test_transcript.py::TestRenderTranscriptText`（直接单测）+ `test_compact.py` / `test_group_memory.py`（走真实调用点）。原文写"既有 `test_transcript.py`"，但那文件当时并不引用它（第二轮审查 §8 指出） |
| S3 | `MemoryStore.add` 不再推送（去掉 `notify` 形参）；推送只由调用方按批做一次 | `memory_store.py:135-189`、`memory_extractor.py:227-285` | 新增 `TestMemoryNotifications`（2 例：批推一次 / store 不推） |
| S4 | `group_turn.py` 模块 docstring + `submit()` docstring 对齐真实契约（驱动自管任务句柄） | `core/group_turn.py:1-30,122-133` | 既有 `test_submit_while_idle_says_nobody_is_driving` 等 |
| S6 | 会话列表/群详情下发 `is_default_title`；前端删掉自带的 `'New Conversation'` 比对 | `api/conversation.py`、`api/group.py`、`ConversationList.tsx`、`types/index.ts` | 新增 2 例（`test_conversation_title.py` 列表、`test_group_api.py` 群详情/创建） |
| S7 | `_store_and_push` → `_store_and_send` | `services/group_chat.py` | —（私有方法，靠群聊 WS 测试覆盖） |
| S8 | 删掉 `_SYSTEM_BY_SCOPE` 后的 4 个多余空行 | `memory_extractor.py` | —（纯格式） |
| P1 | 合并窗出现无 `occurred_on` 的条目时打一行警告（不再静默）；行为本身不动。**第二轮**：警告条数改由 `CatchUpResult.undated` 带出来，测试不再解析日志文本 | `group_memory.py`（`_spans_more_than_a_day` / `_warn_missing_occurred_on` / `CatchUpResult.undated`） | 新增 `test_a_merged_window_without_occurred_on_is_dated_to_its_first_day`（真提取器 + 假 LLM，断言 09-01 回退 + `result.undated == 1`） |
| P2 | 角色名去首尾空白（含 400 拒绝）写进 `CONTEXT.md §6` | `CONTEXT.md` | 既有 `test_character_api.py` |
| P3 | 新增 **`turn_end{group, pending}`**：编排器每跑完一个用户轮回调一次，驱动按轮发事件；前端按 `pending` 收/保持"正在回应"。批次结束仍是 `done`（两者都清，幂等）。顺带把驱动侧 `on_turn_finished`（批级）改名 `on_batch_finished`，与"轮"区分。**第二轮**：回调去掉没人用的 `GroupTurnResult` 载荷（`TurnEndSink = Callable[[], Awaitable[None]]`），并把"`pending=false` 之后仍可能马上开一轮"写进 docstring | `core/group_turn.py`（`TurnEndSink` + `on_turn_end`）、`services/group_chat.py:144-158`、`api/chat.py:468`、`useWebSocket.ts`、`types/index.ts`、`CONTEXT.md §5.1`、`ADR-0006` | 新增 `test_each_turn_is_reported_when_it_ends`（编排器：`pending` 先 True 后 False）+ `test_the_turn_end_event_reports_an_empty_queue`（WS 线形状，**本沙箱跑不起来，见下方验证**） |
| N1 | `daily_greeting_skip` 只在 `reason === 'already_greeted'` 时记「今天已问候」 | `useWebSocket.ts:131-137` | —（前端无测试基建；判据来自 `greeting_orchestrator.py:51`，其它 reason 见 `chat.py`） |
| S5 | **不动**：spec L119 明文要求保留群聊 TTS 开关 | `useTTS.ts` | — |

**验证**（第一轮 = 实现者，第二轮 = 复核者；第二轮改动见 §7）：

- 后端全量：`cd backend && python -m pytest tests/ -q -p no:cacheprovider`
  → 第一轮 **475 passed / 39 errors**，第二轮（§7 之后）**478 passed / 39 errors**。
  39 个错误逐条都是沙箱 `tmp_path` 的 `PermissionError`（已用 `Select-String "^E "` 过滤确认
  没有非 `PermissionError` 的错误），**0 逻辑失败**。
- **P3 的 WS 用例本沙箱跑不起来，且这一条未能独立复核**：`test_group_chat_ws.py` 的 8 个用例
  全部在 fixture setup 阶段 error（0.47s）——`ws_env` 定义在 `backend/tests/conftest.py:57`
  并用 `tmp_path`（它刻意用文件库而非内存库，见该 fixture 的 docstring：TestClient 的 portal
  线程与测试各自持有事件循环，内存库会被重建）。复核者把 `TEMP` 指到工作区重跑仍然 8 errors，
  即**被挡的是 pytest 的 tmp-root 工厂本身**，不是这个 fixture 的写法。
  因此"8 个用例全绿"仍是实现者的单次自述（其做法是把 `ws_env` 临时换成工作区文件库），
  未经第二方独立确认。编排器侧的孪生用例 `test_each_turn_is_reported_when_it_ends` 是可跑的。
- 受影响子集（不过 `tmp_path`）：第一轮的 9 个文件口径 = **120 passed**；第二轮复核者改跑其中
  7 个直接受影响的文件（`test_transcript.py`、`test_group_turn.py`、`services/test_group_memory.py`、
  `services/test_memory_extractor.py`、`test_conversation_title.py`、`test_group_api.py`、
  `test_compact.py`）= **114 passed / 0 failed**。
- 前端类型检查：`cd frontend && npx tsc -b` → **exit 0**（两轮都跑了；跑完已
  `git checkout -- frontend/tsconfig.tsbuildinfo`，别把构建产物带进提交）。

**留给后续 ticket 的**（刻意没做）：

1. P1 的修法 2/3（消掉合并批 / 按天拆批）——涉及 `MAX_BATCHES` 语义与 compact 的取舍。
2. `useDailyGreeting` 在群对话下不再发 `daily_greeting`（N1 的顺带优化）：现在只是白跑一次往返，
   已无副作用。
3. `turn_end` 目前只用于收 `isStreaming`；"谁正在输入"的 UI 留 UI ticket（ADR-0006 已这么定）。

---

## 7. 第二轮：复核后的修补（12 项）

对 §6 这一批**未提交**改动又做了一轮两轴审查（Standards + Spec），下面每条都是那轮发现的问题。
前 10 条是审查报告的发现，N2/N3 是复核者自己加的（两轴都没提）。

### 7.1 文档与标准对齐

| # | 问题 | 修法 |
|---|---|---|
| 1 | **ADR-0006 与代码矛盾且未更新**：其 Consequences 明文写"A 'typing' indicator … **the backend needs no new event for it**"，而 §6 的 P3 恰好加了那个事件，且只更新了 `CONTEXT.md §5.1`（`docs/agents/domain.md` 要求非平凡决策落 ADR） | `docs/adr/0006:58-60` 就地补 **Amended (review P3)** 段：说明为什么一个 burst 需要**按轮**边界、`turn_end{group,pending}` 的语义（含 `pending=false` 只表示"此刻队列空"），并在 `:66` 的契约清单里补上 `turn_end` |
| 2 | **`CONTEXT.md §6` 的"默认标题只有一个来源"是假的**：字面量仍在 `models/conversation.py:26`、`api/conversation.py:22`、`core/conversation_manager.py:141`，比较式还内联了 3 处 | 真的收口：`Conversation.DEFAULT_TITLE` 成为唯一值（列默认值 + `ConversationManager.DEFAULT_TITLE` 别名），`ConversationManager.is_default_title()` 成为唯一判定；`ensure_title` 也改走它；`CONTEXT.md §6` 相应改写 |
| 3 | **多余模块级单例、两种写法**：`api/group.py` 的 `conv_manager = ConversationManager()` 只为读一个类常量，而同文件另一处用类名 | 删掉该单例，两处都改 `ConversationManager.is_default_title(...)`（静态方法，无需实例） |
| 4 | **测试戳私有状态**：`services/test_memory_extractor.py` 直接 `memory_service._memory_notifications.pop(...)` / `.get(...)`（公开的 `pop_memory_notifications` 就在旁边被用着） | 两处改用公开 API：`pop_memory_notifications("conv-aggregate") == 2`（旧的逐条+汇总会得 1+1+2 = 4）与 `pop_memory_notifications("conv-nopush") == 0` |
| 5 | **测试断言日志文本**：`test_group_memory.py` 用 `capsys` + `assert "跨多日" in out` | 警告条数改由 `CatchUpResult.undated` 带出（P1 的"缺口可见"现在是结构化可断言的），测试断言 `result.undated == 1`；日志保留给运维 |
| 6 | **`items` 无类型 / 可见性不一致**：`_warn_missing_occurred_on(window, items, ...)` 的 `items` 无标注；`spans_more_than_a_day` 公开却只有一个调用方 | `items: list[ExtractedMemory]`（导入该类型）+ `_spans_more_than_a_day` 转私有；**连带**把 `SpyExtractor` 的假条目从字符串改成 `ExtractedMemory`（它模拟的就是真提取器的返回类型） |
| 7 | **`TurnEndSink` 载荷没人用**（Speculative Generality）：唯一生产消费者 `_on_turn_end(self, result)` 从不读 `result`，`GroupTurnResult` 的 import 只为标注而存在 | `TurnEndSink = Callable[[], Awaitable[None]]`，`await self._on_turn_end()`；`group_chat.py` 去掉该 import；`test_each_turn_is_reported_when_it_ends` 相应改为只断言 `has_pending()` 序列 |
| 8 | **N1 的覆盖面比文档大**：`core/agent.py:149` 也发 `daily_greeting_skip`，但**没有 reason**，于是那条路径也被排除在"记日期"之外且未记录 | 改为 `{"type":"daily_greeting_skip","reason":"empty"}`，并把它补进 `useWebSocket.ts` 的注释与 `CONTEXT.md §5.1` 的 reason 清单 |
| 9 | **§6 的 S2 行引错测试**：把 `test_transcript.py` 说成锁 `render_transcript_text` 的测试，而该文件当时根本不引用它 | 补真测试 `test_transcript.py::TestRenderTranscriptText`（join + 丢工具管线空行 + 全工具时得空串），并在 §6 表格里改正说明 |
| 10 | **文档小错**：§6 的 S8 行写"5 个空行"而 §1.8 写"4 个"（diff 实际删 4 个）；§3.4-1 的 `:63-76` 实为 `:62-69` | 两处已按实际改正 |

### 7.2 N2 — 改名后 `is_default_title` 陈旧（用户可见回归，两轴都没发现）

`frontend/src/components/chat/ConversationList.tsx:90-93` 改名成功后只合并了 `title`：

```ts
setConversations((prev) =>
  prev.map((c) => (c.id === convId ? { ...c, title: updated.title } : c))
);
```

而 §6 新引入的 `displayTitle`（`:73-77`）**先短路**在 `conv.is_default_title` 上，于是给一条
仍是默认标题的对话改名后，侧栏继续显示 `t('New Conversation')`（再次进入编辑态也是空的，
因为 `handleStartEdit` 同样看这个 flag），直到列表因别的原由重载——而
`reloadKey = conversationReloadKey(角色id, currentConversationId, version)` 在改名时**三项都不变**。
第一轮之前的前端比的是字面量 `title === DEFAULT_TITLE`，改名后立刻显示新名字，所以这是回归。

**修法（两条一起做）**：后端 `update_conversation` 的返回体补上 `is_default_title`
（`create`/`get` 也一并补，保持同一形状），前端就地刷新该字段：

```ts
? { ...c, title: updated.title, is_default_title: updated.is_default_title }
```

**锁定它**：`test_conversation_title.py::TestListDefaultTitleFlag::test_renaming_returns_the_cleared_flag`
断言 PUT 之后 `is_default_title is False`（前端没有测试基建，所以把契约锁在后端响应上）。

### 7.3 N3 — `pending=false` 不是"这批结束了"（只补文档）

`group_turn.py` 的 `on_turn_end()` 在 `take_pending()` **之前**被 await（这个顺序是刻意的：
回调期间进来的消息会被紧随其后的 `take_pending()` 取走，不会漏掉一轮）。代价是
`group_chat._on_turn_end` 算出的 `pending=false` 只代表"**此刻**队列是空的"——用户若正好在
回调的 WS 发送期间插话，前端会先清掉 `isStreaming`，紧接着却又真开一轮，那一轮就没有
"正在回应"指示（发言本身仍逐条即时可见）。

不改成"先取队列再回调"（那会重新引入"回调期间到达的消息要等下一次 submit 才被处理"的问题），
而是把这个语义写进 `group_turn.py` 的模块 docstring、`_on_turn_end` 的 docstring、
`frontend/src/types/index.ts` 的 `pending` 注释与 `CONTEXT.md §5.1`。

### 7.4 第二轮验证

- 后端全量：**478 passed / 39 errors**（39 个全是 `PermissionError`，0 逻辑失败）。
  比第一轮 475 多的 3 个就是本轮新增的测试：`TestRenderTranscriptText` 两例 +
  `test_renaming_returns_the_cleared_flag` 一例。
- 前端 `npx tsc -b` → **exit 0**（`tsconfig.tsbuildinfo` 已还原）。
- 仍未做：P1 的修法 2/3、`useDailyGreeting` 群下不发请求、以及 §6 里 P3 的 WS 用例
  在本沙箱无法运行的确认（见 §6 验证那段的说明）。
