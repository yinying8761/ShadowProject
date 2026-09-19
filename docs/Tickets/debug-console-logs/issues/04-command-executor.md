# 04 — 命令执行器（白名单）+ 通道接线

**What to build:** 面板里敲的应用命令经 `/ws/logs` 上行 → 白名单执行器执行（`clear` / `status` /
`mcp reconnect` / `config reload` / `help`，未知命令回「unknown」并列出白名单）→ 结果以
`[cmd]` 日志行写回日志总线，回到同一视图并落盘。**只认白名单**——绝不执行任意 OS 命令
（安全边界写死）。

**Blocked by:** #03（命令消息走 `/ws/logs` 通道）

**Status:** ready-for-agent

**参考:** `docs/specs/debug-console-logs.md`

**关键语义：**

- 命令执行器构造注入依赖：hub + 可选 `mcp_manager`/`config_store` 回调（测试传 fakes）。
- 白名单命令：
  - `clear` — 清空 hub 环形缓冲，随后写 `[cmd] log buffer cleared`
  - `status` — hub 统计（ring 行数/容量、订阅者数、日志文件路径）+ 当前模型/进程信息
  - `mcp reconnect` — 触发 MCP 重连（经 mcp_manager），结果写回
  - `config reload` — 用 ConfigStore 重新加载生效配置 → 刷新 runtime_config
  - `help`/未知命令 — 列出白名单；`[cmd] unknown: <cmd>`
- **安全边界**：不 `eval`、不 `exec`、不调子进程、不碰文件系统；只查表分发。
- 入站协议新增：`{"type":"command","command":"clear"}`；`main.py` lifespan 把 `mcp_manager`
  暴露到 `app.state`（当前是 lifespan 局部变量）。

## Checklist

- [ ] 新建命令执行器模块：白名单注册 + 各命令实现（clear/status/mcp reconnect/config reload/help）
- [ ] 未知命令 → `[cmd] unknown: <cmd>` + 可用命令列表
- [ ] `/ws/logs` 处理器接 `command` 消息 → 执行器 → 结果写 hub（`source=cmd`）
- [ ] lifespan 暴露 `app.state.mcp_manager`（供 `mcp reconnect` 用）
- [ ] 新建 `backend/tests/test_command_executor.py`（S4，注入假 hub/fakes）：
  - [ ] 白名单命令执行返回文本；`clear` 清空 ring；`status` 输出统计
  - [ ] 未知命令提示 + 列表
  - [ ] 构造一个「白名单外命令名」→ 断言绝不执行、只回 unknown
- [ ] S2 补一条：WS 发 `command` 消息 → `[cmd]` 输出行回到通道
- [ ] 手动冒烟：WS 里敲 `status` → `[cmd] status ...` 出现在日志流