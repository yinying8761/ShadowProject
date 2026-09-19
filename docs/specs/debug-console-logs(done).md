# Debug Console & Logs — 调试控制台与日志（Workflow J）

> 日期：2026-09-15 | 状态：待实现 | 来源：`docs/specs/iteration-plan3.md` Workflow J

## Problem Statement

1. **日志无落盘**：后端 print 只进控制台窗口，应用一关就丢；打包后的桌面应用里前端日志无处可看（没有 dev.bat 黑窗口）。
2. **调试依赖黑窗口 + F12**：开发要盯 dev.bat 黑窗口；打包后连 F12 真 DevTools 都打不开。
3. **watcher tick 淹没日志**：主动陪伴每 15s 打印一行（~240 行/小时），全是 `idle=X<thresh=Y` 噪音，真正异常被淹没。
4. **前后端日志分裂**：前端 console.error 只进浏览器 console，后端 print 只进黑窗口，同一个问题要两头翻。
5. **调试形态期望（用户已确认，游戏控制台形态）**：像 CS2 `~` 控制台那样——既能实时看日志/报告，也能敲**应用白名单**指令；不是 PowerShell（拒绝任意 OS 命令）。

## Solution

- 后端新建 `log_hub` 日志总线：环形缓冲（最近 500 行）+ 落盘 `data/logs/companion.log`（滚动 5MB×2，磁盘上限约 15MB）+ 订阅者分发。`sys.stdout` Tee + 根 logger handler 接入（**ADR-0002**，不迁移任何现有 print）。
- 新建 `/ws/logs` WebSocket 通道：连接即推最近 500 行历史，实时转发新行；双向接收前端 renderer 错误上报与**面板命令**。
- 新建命令执行器（**应用白名单**）：命令经既有 WS 上行，执行结果写回 hub——作为日志行回到同一视图、同时落盘。
- 前端新增右滑悬浮调试面板（「查看 + 命令行」形态）：实时 tail、历史回放、`[renderer]` 过滤开关、一键复制、命令输入行；Ctrl+Shift+D 呼出（Esc 关闭）。
- Electron 主进程：F12 → `webContents.openDevTools()`（dev 与打包后都可用）。
- watcher 降噪：tick 只在「签名变化（level/threshold/daily）」或「即将触发（ok=True）」时打印；`PROACTIVE_TICK_DEBUG=1` 恢复全量。

## User Stories

1. 作为开发者，我想按 F12 在打包后的应用里打开真 DevTools，so that 不依赖 dev.bat 黑窗口也能调试前端 JS/网络/DOM。
2. 作为开发者，我想按 Ctrl+Shift+D 呼出调试面板，so that 前后端日志在同一个视图里实时滚动。
3. 作为开发者，我想面板打开时先看到最近 500 行历史，so that 不用等日志刷出来就能追溯刚发生的事。
4. 作为开发者，我想在面板里看到后端所有 print 与框架日志（uvicorn 等），so that 不用翻黑窗口。
5. 作为开发者，我想日志条目带时间戳和来源（后端/前端/命令），so that 一眼分辨日志从哪来。
6. 作为开发者，我想在面板里敲白名单指令（如 `status`、`clear`、`mcp reconnect`、`config reload`），so that 不重启应用就能做常见运维。
7. 作为开发者，我敲了不在白名单里的命令时，面板回「未知命令」并列出可用命令，so that 边界明确而不是静默失败。
8. 作为开发者，前端 console.error / console.warn / 未捕获异常 / 未处理 Promise rejection 能进这个面板，so that 前端报错和后端日志同屏对照。
9. 作为开发者，面板打开前发生的前端错误也不丢——连接时补传最近记录，so that 追问题不缺开头。
10. 作为开发者，日志落盘有硬上限（滚动 5MB×2），so that 不会无限吃磁盘。
11. 作为开发者，watcher 例行 tick 不出现在日志里，so that 不被 240 行/小时淹没；我可以设 `PROACTIVE_TICK_DEBUG=1` 恢复全量。
12. 作为开发者，命令执行结果以日志行形式回到同一个视图，so that 「看报告」和「敲指令」在同一处（游戏控制台形态）。
13. 作为开发者，面板支持 `[renderer]` 过滤开关，so that 只想看后端日志时不被前端上报刷屏。
14. 作为开发者，我能一键复制面板当前内容，so that 贴给排查工具/他人方便。
15. 作为开发者，Esc 或再按 Ctrl+Shift+D 关闭面板，so that 不挡聊天。
16. 作为开发者，命令只认白名单、绝不执行任意 OS 命令，so that 面板失控也不会危害系统。
17. 作为开发者，MCP 掉线时我能在面板查状态/重连，so that 不用重启整个应用。
18. 作为开发者，改了生效配置后能在面板触发重载，so that 配置变更不用重启。
19. 作为普通用户，面板是默认关闭的独立 overlay，so that 日常聊天体验完全不受影响。

## Implementation Decisions

### 日志总线（log_hub，ADR-0002）

- `LogHub`：环形缓冲（500 行，满丢最老）+ 订阅者注册/反注册 + 线程锁。
- 条目为结构化 `{source, level, message, ts}`：`source ∈ backend|renderer|cmd`；后端 print 默认 `source=backend, level=info`；renderer 上报带真实 level（warn/error）；命令输出 `source=cmd`。
- 安装（main.py lifespan 启动时）：`sys.stdout` Tee（写穿原 stdout + 进 hub）+ 根 logger 挂 hub handler + 文件 handler（`data/logs/companion.log`，5MB×2）。**幂等**（重复启动不重复装）。不迁移任何现有 print（ADR-0002）。
- 订阅者→WS 推送跨线程：hub 用线程安全方式（`loop.call_soon_threadsafe` 或带锁队列）把新行递给每个 WS 订阅任务；WS 侧用 asyncio 队列。
- hub 实例设到 `app.state.log_hub`（lifespan 内），命令执行器经构造注入拿引用。

### /ws/logs 通道

- 新 api 模块登 WS `/ws/logs`（本机应用，无需鉴权）。
- 出站协议：`{"type":"logs_history","lines":[...]}`（连接即发最近 500 行）→ 之后 `{"type":"logs_line","line":{...}}` 逐条实时推送。
- 入站协议：`{"type":"renderer_log","level":"warn|error","message":"..."}`、`{"type":"command","command":"clear"}`。
- renderer 上行在 WS 层框架为 `source=renderer`；命令执行结果由执行器写入 hub（`source=cmd`）。

### 命令执行器（白名单，新建纯模块）

- 构造注入依赖：`hub` + 可选 `mcp_manager` / `config_store` 回调（测试传 fakes）。
- 最小白名单（方案 B，先定这些）：
  - `clear` — 清空 hub 环形缓冲（所有人可见），随后写一条 `[cmd] log buffer cleared`
  - `status` — hub 统计（ring 当前行数/容量、订阅者数、日志文件路径）+ 当前模型/进程信息
  - `mcp reconnect` — 触发 MCP 重连（经 mcp_manager），结果写回
  - `config reload` — 用 ConfigStore 重新加载生效配置 → 刷新 runtime_config
  - `help` / 未知命令 — 列出白名单；`[cmd] unknown: <cmd>`
- **安全边界（写死）**：只认白名单；不 eval、不 exec、不调子进程、不碰文件系统。
- 命令执行器在 WS 命令消息到达时由 /ws/logs 处理器调用。

### Watcher 降噪（源头策略）

- 抽纯判定（如 `should_log_tick(prev_signature, cur_signature, ok) -> bool`）：签名 = `(level, next_threshold, daily_count)`；`ok=True`（即将触发）恒打；签名变化才打；否则静默。
- `PROACTIVE_TICK_DEBUG=1` 时恒打（恢复全量）。
- tick 内容沿用现状（含 idle 值），只是**打印时机**变了。

### 前端

- `appStore` 新增 `showDebugConsole` + setter；`useKeyboardShortcuts` 加 Ctrl+Shift+D 分支，Esc 关闭并入现有关闭链最上层。
- 新建 `DebugConsole.tsx`：右滑悬浮 overlay；实时 tail（自动滚动）+ 打开时接收 history + 命令输入行 + `[renderer]` 过滤开关 + 一键复制。
- 日志 WS：**独立连接**（不混入聊天 WS）；面板挂起时连接、关闭时断开；连接后立即收 history + 实时行。
- renderer 错误上报：App 根装 `console.error`/`console.warn`/`window.onerror`/`unhandledrejection` 采集，先写前端本地环形缓冲（最近 100 条），面板 WS 连接后补传（含重连补传）。**不转发 console.log**。
- Electron：`mainWindow.webContents` 的 before-input-event 处理 F12 → `openDevTools()`。

### 接线位置

- lifespan：装 hub（Tee + 文件 + logger）→ 设 `app.state.log_hub`（及命令需要的 `app.state.mcp_manager`）。
- 路由：新 api 模块挂 `/ws/logs`，`main.py` include。

## Testing Decisions

原则：只测外部行为，不测内部写法；复用现有注入缝（FakeClock / fakes / TestClient / capsys）；前端手工验收。

| Seam | 注入 | 测什么 | 参照 |
|---|---|---|---|
| **S1 LogHub 纯模块** | 构造注入 ring_size + 订阅回调（测试不装 Tee） | Tee 捕获进 ring、环形 500 上限（满丢最老）、订阅者收到推送、条目结构化（source/level） | `test_circuit_breaker.py`（纯类 + FakeClock）、`test_retry.py` |
| **S2 /ws/logs** | **只挂 logs 路由** + 注入干净 hub（**不 import main.app**，避免 lifespan 装 Tee 干扰 pytest stdout 捕获） | 连接即推 history；实时行到达；`renderer_log` 上行 → hub 条目 `source=renderer`；未知命令 → `[cmd] unknown` + 列表；`status` → 输出行回写 | `test_tool_runtime.py` / `test_conversation_title.py` 的 TestClient 模式 + `websocket_connect` |
| **S3 watcher 降噪** | 纯判定直测 + `ProactiveWatcher` 一轮（fakes + capsys） | 签名不变不打、变化/即将触发才打、`PROACTIVE_TICK_DEBUG=1` 恢复全量 | ProactiveWatcher 构造注入；`test_greeting_concurrency.py` |
| **S4 命令执行器** | 构造注入假 hub / 假 mcp_manager / 假 config_store | 白名单命令执行返回文本；未知命令提示；`clear` 清空；`status` 输出统计；**不执行白名单外命令** | `test_llm_config.py`（ConfigStore）、`test_mcp_manager.py`（AsyncMock） |

- 测试文件：`backend/tests/test_log_hub.py`（新建，S1+S2）、`backend/tests/test_command_executor.py`（新建，S4）、`backend/tests/test_proactive_watcher.py`（新建，S3——当前**不存在** watcher 测试文件，plan 中「并入现有 watcher 测试文件」的前提不成立）。
- 前端 DebugConsole / 快捷键 / Electron F12 / 错误上报：手工验收（项目无前端测试基建）。
- 回归：全量 `python -m pytest tests/ -v`。

## Out of Scope

- **任意 OS 命令执行**（面板只认白名单，安全边界）
- 白名单命令的扩展（用一段时间后按需加；`help` 先给列表）
- `console.log` 全量转发（普通前端日志用 F12 DevTools 看）
- 历史日志查询 API / 复杂过滤 / 日志检索（看更老日志直接开文件）
- 日志聚合统计（去重、速率、图表面板）
- 错误文案英文版（应用默认中文）
- 多面板/多标签页联合同步（各 WS 各自收 history）
- 熔断状态、用量等「更多开发视图」（本版只做日志 + 命令）

## Further Notes

- 架构分层：源头策略（watcher 降噪）→ 汇聚点（LogHub：收/存/广播）→ 通道（/ws/logs：进出，不改日志）→ 面板（查看 + 白名单命令）。
- 命令输出写回 hub，保证「面板视图 == 落盘 == 其他客户端看到的」一致。
- ADR-0002 已定 Tee 方案；若将来 print 迁到 logging，hub 同时消费两者，无缝。
- `PROACTIVE_TICK_DEBUG=1 python main.py` 恢复全量 tick 日志。
- 面板命令的「游戏控制台」形态是用户确认的期望（CS2 `~`），区别于 PowerShell：只执行应用白名单。