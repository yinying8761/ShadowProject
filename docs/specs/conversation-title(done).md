# 对话命名 — AI 自动命名 + 手动改名 (Workflow H)

> 日期：2026-09-09 | 状态：待实现 | 来源：`docs/specs/iteration-plan3.md` Workflow H

## Problem Statement

每个会话的标题永远是 `"New Conversation"`：这个字段存在但全代码库没有任何更新逻辑。侧边栏里所有会话条目长得一模一样，用户无法辨认哪个会话聊过什么，也不能给重要的会话起名字。

## Solution

- **AI 自动命名**：一轮对话结束后，后台取该会话第一条用户消息 + 第一条 AI 回复，让 LLM 起一个 ≤12 字的标题，写回 `Conversation.title`。命名在后台进行，不打断聊天流。
- **用户手动改名**：侧边栏会话条目上加编辑键，行内编辑（Enter 确认 / Esc 取消 / 右键取消）。
- **优先级规则（一条判断实现全部语义）**：`title 不等于默认值 → 跳过命名`。AI 命名过、用户改过名，都满足 title ≠ 默认值，永不再覆盖；AI 生成失败则 title 仍是默认值，下次对话自然再试。
- 无需数据库迁移（title 字段已存在），不新增任何状态列。

## User Stories

1. 作为用户，我想在开始新对话并聊过一轮后，侧边栏自动出现能辨认的标题，so that 我不用在一排 "New Conversation" 里猜。
2. 作为用户，我希望标题不超过 12 个字，so that 侧边栏窄栏里也能完整显示。
3. 作为用户，我想点击会话条目旁的编辑键自己改名，so that 我能用自己的方式组织会话（如「找工作-周报」「周日的倾诉」）。
4. 作为用户，我改的名字要永久生效、AI 永不覆盖，so that 我的命名优先级天然最高。
5. 作为用户，改名时我希望行内编辑（不弹对话框），Enter 确认，so that 改名快而不打断浏览。
6. 作为用户，编辑中按 Esc 或右键应取消并恢复原标题，so that 误触不会留下半截文字。
7. 作为用户，当 AI 命名失败（网络/接口问题）时，标题保持「新对话」，so that 下次对话自动再试而不是留下一个坏标题。
8. 作为用户，命名应在后台进行，so that 聊天回复的流式体验不受任何影响。
9. 作为用户，标题改为默认值的会话应显示为本地化的「新对话」，so that 中文界面里不冒出英文 "New Conversation"。
10. 作为用户，AI 起好标题后侧边栏应立即刷新显示，so that 我不用切换会话才能看到。
11. 作为老用户，我的历史会话不需要一次性批量命名，so that 应用启动不被 LLM 调用风暴拖慢；老会话在下次对话时按同一规则补命名。
12. 作为从没聊过的会话，标题保持「新对话」，so that 不为死会话浪费 LLM 调用。
13. 作为开发者，命名判断与生成集中在一个可注入假 LLM 的模块方法里，so that 我能用 FakeLLMService 单测全部语义（跳过/生成/失败兜底）。
14. 作为开发者，改名是普通 REST 端点，so that 前端与测试都能用同一合约。
15. 作为开发者，本特性不引入数据库迁移或新列，so that 升级路径零成本。
16. 作为开发者，主动陪伴/每日问候产生的 done 事件不触发命名，so that 无人在场的后台轮次不偷偷消耗 LLM 调用。

## Implementation Decisions

### 核心规则：一条判断实现全部语义

- 默认标题常量沿用现有值（"New Conversation"）。
- **title ≠ 默认值 → 跳过命名**。由此推出：AI 首次命名成功后永不再覆盖；用户改名后永不覆盖（用户优先级天然最高）；生成失败 title 仍是默认值，下次对话再试。
- **不新增 `title_source` 列**，不做任何数据库迁移。

### 命名入口（最高缝）

- `ConversationManager` 新增一个命名方法（如 `ensure_title(session, conversation_id, *, llm_service)`），**内含全部语义**：查 title → 等于默认值才继续 → 取首条用户消息 + 首条 AI 回复 → 拼 prompt → `chat_sync` → 写回。调用方（WS 处理器）只负责在后台任务里调它。
- `llm_service` 构造注入，遵循项目 DI 惯例，测试可替换假实现。

### AI 生成

- 输入：该会话第一条用户消息 + 第一条 AI 回复（各自截断到合理长度，防止超长 prompt）。
- 调用：`chat_sync(max_tokens≈50, temperature=0.3)`，prompt 要求「输出不超过 12 字的会话标题，直接输出标题本身，无解释、无引号」。
- 后处理：trim、去掉包裹引号；结果为空 → 保持默认。
- 重试：复用 `retry()`，小步 2 次（base 0.5s）；彻底失败静默保持默认，不向客户端发任何事件。
- 并发：同一会话同一时刻只允许一个在途命名任务（in-flight 去重），避免连发消息触发重复 LLM 调用。

### 触发点

- WS 聊天流 `done` 事件后起 `asyncio.create_task` 后台命名，不阻塞消息流。
- `done` 带 `proactive` / `daily_greeting` 标记的**不触发**命名（无人在场）。
- **不做启动时批量回填**：老会话在下一次对话时按同一规则补命名；永不聊的保持默认。

### 改名 API

- 新增 `PUT /api/conversations/{id}`，body `{"title": "..."}`；title 为空/全空白拒绝；只允许改 title；返回更新后的会话对象。
- 会话不存在 → 404（与现有 GET/DELETE 行为一致）。

### 前端

- 侧边栏会话条目：**编辑键放在删除键左侧**，hover 显现（与删除键一致）。
- 行内编辑：点击编辑键 → 输入框替换标题 → Enter 确认 / Esc 取消 / 右键取消（全局「右键取消」交互习惯的起点）。确认后调 PUT 并立即更新本地列表。
- 显示：title 为默认值时显示 i18n 词条「新对话」（词条已有，当前代码直接显示原始英文值，改为走 `t()`）。
- 刷新：会话列表当前是侧边栏组件的本地 state；`done` 后由 chatStore 置一个「会话列表已脏」的版本计数，侧边栏订阅该计数变化时重拉列表。

## Testing Decisions

### 测试原则

只测外部行为，不测内部写法。复用现有 seam，不新增缝：

| 缝 | 注入 | 测什么 | 参照 |
|---|---|---|---|
| S1 `ConversationManager` 命名方法 | 假 `llm_service`（假 `chat_sync`）+ 内存 SQLite | title==默认值才生成 / 已命名跳过 / LLM 失败重试后保持默认 / 空结果保持默认 / proactive 场景由调用方保证不调用 | `test_llm_retry.py` 的 FakeLLMService |
| S2 HTTP | `TestClient` + 内存 SQLite | PUT 改名成功往返 / 空标题拒绝 / 404 | `test_tool_runtime.py` 的 client 模式 |

### 测试文件

- `backend/tests/test_conversation_title.py`（新建）— S1 + S2 全部用例。

### 回归

- `chat.py`（WS 处理器）与 `conversation.py`（API）是纯接线改动，靠现有 `test_llm_retry.py` / agent 相关测试保证不破。前端为手工验收。

## Out of Scope

- 启动时批量回填历史会话标题（明确不做，避免启动期 LLM 调用爆发）。
- 「重新生成标题」按钮（想改名直接手动改名即可）。
- `title_source` 等任何新数据库列。
- 标题多语言生成策略（prompt 用中文要求，应用默认中文）。
- 侧边栏以外的标题显示（如聊天面板头部）。
- 全局统一的右键菜单系统（本版只建立「右键取消」这一处交互习惯）。

## Further Notes

### 改动清单

| 文件 | 改动 |
|---|---|
| `backend/core/conversation_manager.py` | 新增 `ensure_title()`：默认值判断 + 拼 prompt + chat_sync + 重试 + 写回 |
| `backend/api/chat.py` | `done` 后（非 proactive/greeting）起后台任务调 `ensure_title`；in-flight 去重 |
| `backend/api/conversation.py` | 新增 `PUT /{id}`（title） |
| `frontend/src/components/chat/ConversationSidebar.tsx` | 编辑键 + 行内编辑 + 默认标题走 i18n |
| `frontend/src/stores/chatStore.ts` | done 后置「会话列表已脏」版本计数 |
| `frontend/src/services/api.ts` | `updateConversationTitle` |
| `frontend/src/i18n/translations.ts` | 新增编辑相关文案（如已有则复用） |
| `backend/tests/test_conversation_title.py`（新建） | S1 + S2 |

### 验证命令

```bash
cd backend && python -m pytest tests/test_conversation_title.py -v
```

### 运行方式

```bash
# H：新会话聊一句 → 标题自动出现；编辑键改名后 AI 不再覆盖
curl -X PUT http://127.0.0.1:8722/api/conversations/<id> \
  -H "Content-Type: application/json" -d '{"title":"周日的倾诉"}'
```
