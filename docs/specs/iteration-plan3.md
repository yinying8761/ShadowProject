# 迭代计划 3 — 可见性三件套（命名 / 错误 / 日志）

> 日期：2026-09-09 | 状态：待实现

## 总览

三个 Workflow，按优先级顺序执行（相互独立，无硬依赖）：

```
H（对话命名）  →  I（错误可见）  →  J（调试控制台与日志）
   1-2天             1-2天              2-4天
```

| # | Workflow | 核心目标 | 预计工作量 |
|---|----------|---------|-----------|
| H | 对话命名 | AI 自动命名 + 用户手动改名，侧边栏不再是"New Conversation" | 1-2 天 |
| I | 错误可见性 | 网络错误进聊天界面：重试进度瞬态提示 + 失败内联错误气泡 | 1-2 天 |
| J | 调试控制台与日志 | 日志总线（落盘 + 实时推送）+ 桌面内调试面板 + watcher 降噪 | 2-4 天 |

**总计约 4-8 天（业余时间）。**

---

## Problem Statement

1. **会话标题是死数据**：`Conversation.title` 默认 `"New Conversation"`，全代码库无任何更新逻辑，侧边栏无法辨认会话。
2. **错误对用户不可见**：前端 `error` 事件只 `console.error`（useWebSocket.ts）；后端 LLM 重试完全静默，用户体感"卡半天然后没有回复"，不知道发生了什么。
3. **调试依赖黑窗口 + 网页版 F12**：打包后的桌面应用里前端日志无处可看；后端 print 日志不落盘，应用一关就丢。
4. **watcher tick 淹没日志**：主动陪伴每 15s 打印一行（~240 行/小时），全是 `idle=X<thresh=Y` 噪音。

---

## User Stories

1. 作为用户，开始新对话后，侧边栏应自动出现能辨认的标题。
2. 作为用户，如果不喜欢 AI 起的标题，我能自己改名，且改完后 AI 永不覆盖。
3. 作为用户，网络抖动重试期间，我能看到"正在重试 (1/5)"，知道应用在努力而不是卡死。
4. 作为用户，重试全部失败时，聊天界面应明确告诉我失败了、为什么（持久可见，可展开细节）。
5. 作为开发者，打包后的桌面应用里我能按 F12 打开真 DevTools 看前端日志。
6. 作为开发者，快捷键呼出的调试面板能实时 tail 后端日志，不依赖 dev.bat 窗口。
7. 作为开发者，前端报错（console.error/未捕获异常）与后端日志汇入同一个视图。
8. 作为开发者，日志落盘有硬上限（滚转删除），不会无限吃磁盘。
9. 作为开发者，watcher 例行 tick 不出现在正常日志里，事件发生才有日志。

---

## Workflow H：对话命名

### 现状

`Conversation.title`（String(200)，默认 `"New Conversation"`）存在但从未被更新；侧边栏直接显示原始值。

### 核心规则：一条判断实现全部语义

> **title 不等于默认值 → 跳过命名。**

- AI 首次命名成功 → title ≠ 默认值 → 永不再覆盖
- 用户改名 → title ≠ 默认值 → 永不覆盖（用户优先级天然最高）
- AI 生成失败 → title 仍是默认值 → 下次对话自然再试
- 无需新增 `title_source` 数据库列

### AI 自动命名

- **触发**：WS 聊天流 `done` 事件后起后台任务（不阻塞消息流）
- **生成**：取该会话第一条用户消息 + 第一条 AI 回复 → `chat_sync(max_tokens≈50, temperature=0.3)`，要求输出 ≤12 字标题（直接输出标题本身，无解释）
- **重试**：小步 2 次（复用 `retry()`，base 0.5s）；仍失败保持默认，下次对话再试
- **老会话懒补**：不做启动时批量回填（避免启动期 LLM 调用爆发、网络差时连环失败）；老会话在下次对话时按同一规则补命名，永不聊的保持默认

### 用户改名

- **API**：`PUT /api/conversations/{id}`，body `{"title": "..."}`（现 API 无此端点，新增）
- **交互**：侧边栏会话条目——**删除键左侧加编辑键** → 点击进入行内编辑 → **Enter 确认 / Esc 取消 / 右键取消**（全局"右键取消"交互习惯的起点，未来各处统一）

### 显示

title 为默认值时，侧边栏显示 i18n 翻译的「新对话」（词条已有，当前直接显示原始英文值）。

### 改动清单

| 文件 | 改动 |
|------|------|
| `backend/api/conversation.py` | 新增 `PUT /{id}`（title） |
| `backend/api/chat.py` | `done` 后起后台命名任务（title==默认值时） |
| `backend/core/conversation_manager.py` | 新增 `generate_title()`：拼 prompt + chat_sync + 重试 |
| `frontend/src/components/chat/ConversationSidebar.tsx` | 编辑键 + 行内编辑 + 默认标题翻译 |
| `frontend/src/stores/`（会话列表来源） | `done` 后刷新会话列表 |

无数据库迁移（title 字段已存在）。

---

## Workflow I：错误可见性

### 现状

`useWebSocket.ts` 对 `error` 事件仅 `console.error`；`retry()` 无进度回调，LLM 层 5 次指数退避（1+2+4+8+16=31s）全程静默。

### 设计：两条通道

```
重试期间（瞬态，前端内存）：
  retry() 新增 on_retry 回调
  → Agent.run 新增 on_llm_retry 钩子，直通 WS
  → 前端显示「网络连接失败，正在重试 (1/5)」，重试结束即消失

全部失败（持久，前端内存）：
  error 事件（现有）→ 内联红色错误气泡
  人话主文案 + 可展开原始 message，手动关闭，不写数据库
```

**实现决策**：

- `on_llm_retry` **直通 send_json 而不进 Agent 事件流**：回调发生在 generator 深处的 await 链内，无法从回调处 yield；且它是纯 UI 瞬态，不值得扩事件管道。但 CONTEXT.md §5.1 的 WS 表仍登记 `llm_retry`（它是客户端可见的服务端事件）
- 错误文案映射表：`Connection error` → 网络连接失败，请检查网络或稍后重试；超时 → 响应超时；其余 → 出错了，请稍后重试。原始 message 放进可展开详情（兼职半个调试视图）
- 仅聊天链路接 `on_llm_retry`（主动陪伴/问候无人在看，不打扰）
- 错误气泡与重试提示**都只存 chatStore 内存**，切会话即清；数据库照旧只存用户/助手消息与 partial 内容（现有行为）

### 改动清单

| 文件 | 改动 |
|------|------|
| `backend/services/retry.py` | `retry()` 新增可选 `on_retry(attempt, max_retries, exc)` |
| `backend/services/llm_service.py` | `stream_chat` / `chat_sync` 透传 `on_retry` |
| `backend/core/agent.py` | `run()` 新增 `on_llm_retry` 参数，传入 stream_chat |
| `backend/api/chat.py` | 构造 on_llm_retry → `send_json({"type":"llm_retry", attempt, max_retries})` |
| `frontend/src/hooks/useWebSocket.ts` | 处理 `llm_retry` + `error`（不再只 console.error） |
| `frontend/src/stores/chatStore.ts` | `retryState`（瞬态）+ `errorBubble`（持久）状态与清除 |
| `frontend/src/components/chat/ErrorBubble.tsx` | **新建** — 内联错误气泡 |
| `CONTEXT.md` §5.1 | 登记新 WS 事件 `llm_retry` |

---

## Workflow J：调试控制台与日志

### 日志总线（`backend/services/log_hub.py` 新建）

```
print() / logging → 日志总线（环形缓冲 500 行 + 线程锁）
                      ├─→ RotatingFileHandler：data/logs/companion.log（5MB × 2 备份，磁盘硬上限 ~15MB）
                      └─→ /ws/logs 实时推送
```

- **捕获方式**：`sys.stdout` 重定向为 Tee（写穿原 stdout + 进 hub）；根 logger 挂一个 hub handler（覆盖 uvicorn 等框架日志）。不迁移任何现有 print（决策见 **ADR-0002**）
- **/ws/logs 双向**：连接即推最近 500 行历史；客户端可上行 `{source:"renderer", level, message}` 上报前端日志（打 `[renderer]` 前缀进 hub）

### 前端日志上报（最小集）

捕获 `console.error` / `console.warn` / `window.onerror` / `unhandledrejection` → `/ws/logs` 上行。**不转发 console.log**（量大人杂，要看前端普通日志用 F12）。

### 调试面板（前端）

- **Ctrl+Shift+D** 呼出/收起；右侧滑入悬浮面板（独立 overlay + Zustand 开关，复用 useKeyboardShortcuts 模式）——与设置面板受众分离（用户设置 vs 开发者工具）
- 功能：实时 tail + 打开时载最近 500 行 + `[renderer]` 过滤开关 + 一键复制
- Electron 主进程注册 **F12 → `webContents.openDevTools()`**（打包后也能开真 DevTools）

### Watcher 降噪

`proactive_watcher.py`：tick 只在**签名变化**（level / threshold / daily 变化）或即将触发（`ok=True`）时打印；环境变量 `PROACTIVE_TICK_DEBUG=1` 恢复全量。

### 改动清单

| 文件 | 改动 |
|------|------|
| `backend/services/log_hub.py` | **新建** — hub（环形缓冲 / 锁 / Tee / 文件 / 订阅者） |
| `backend/main.py` | 启动时装 hub（stdout 重定向 + 文件 handler） |
| `backend/api/`（新文件或并入现有） | **新建** — `/ws/logs` 端点 |
| `backend/services/proactive_watcher.py` | tick 降噪 + `PROACTIVE_TICK_DEBUG` 开关 |
| `frontend/src/components/dev/DebugConsole.tsx` | **新建** — 悬浮调试面板 |
| `frontend/src/hooks/useKeyboardShortcuts.ts` | Ctrl+Shift+D |
| `frontend/src/stores/appStore.ts` | 面板开关状态 |
| `frontend/`（启动接线处） | renderer 错误上报（error/warn/onerror/unhandledrejection） |
| `frontend/electron/main.cjs` | F12 → openDevTools |

---

## Testing Decisions

原则同前两期：只测外部行为，复用现有 seam（FakeLLMService / TestClient + 内存 SQLite / FakeClock）。

| 测试 | 覆盖 |
|------|------|
| `backend/tests/test_conversation_title.py`（新建） | H：PUT 改名端点；title==默认值才生成；已命名跳过；生成失败保持默认 |
| `backend/tests/test_log_hub.py`（新建） | J：Tee 进缓冲；缓冲上限；订阅推送；`[renderer]` 前缀 |
| watcher 降噪用例 | J：签名不变不打、变化/触发才打、env 开关恢复（并入现有 watcher 测试文件） |
| `test_llm_retry.py` 补一条 | I：on_retry 回调次数与参数正确 |

错误气泡/重试提示为纯前端 UI，手工验收。

---

## Out of Scope

- 调试面板**指令白名单**（用一段时间后再定要什么命令）
- 错误气泡**"点击重试"按钮**（自动重发涉及幂等与消息持久化，复杂度跳档）
- `console.log` 全量转发
- 历史日志查询 API / 复杂过滤（看更老日志直接开文件）
- 错误文案英文版（应用默认中文）

---

## 运行方式

```bash
# H：新会话聊一句 → 标题自动出现；编辑键改名后 AI 不再覆盖
# I：断网发消息 → 「正在重试 (n/5)」→ 失败出现红色错误气泡（刷新即清）
# J：Ctrl+Shift+D 呼出调试面板（实时 tail）；data/logs/companion.log 自动滚动
#    PROACTIVE_TICK_DEBUG=1 python main.py   # 恢复全量 tick 打印
#    F12 → 真 DevTools（打包后可用）
```
