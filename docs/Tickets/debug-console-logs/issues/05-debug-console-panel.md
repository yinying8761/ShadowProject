# 05 — 前端调试面板（查看 + 命令行）+ renderer 错误上报

**What to build:** Ctrl+Shift+D 呼出右滑悬浮调试面板（游戏控制台形态）：实时 tail 日志 +
打开时回放最近 500 行历史 + `[renderer]` 过滤开关 + 一键复制 + 底部命令输入行（Enter 执行）。
App 根同时采集前端错误（console.error/warn、window.onerror、unhandledrejection）存入本地
环形缓冲（最近 100 条），面板日志通道连接时补传——面板打开前的前端报错也不丢。

**Blocked by:** #03（日志通道协议）、#04（命令协议）

**Status:** ready-for-agent

**参考:** `docs/specs/debug-console-logs.md`

**关键语义：**

- 独立日志 WS（**不混入聊天 WS**）：面板挂起时连接、关闭时断开；连接后先收 `logs_history`
  再收 `logs_line` 实时行；上行 `renderer_log` / `command`。
- `appStore` 新增 `showDebugConsole`；`useKeyboardShortcuts` 加 Ctrl+Shift+D（Esc 关闭并入
  现有关闭链最上层）。
- 日志行样式：时间戳 + 来源；`[renderer]`（或 `source=renderer`）过滤开关；自动滚动；复制按钮。
- **不转发 console.log**（普通前端日志用 F12 DevTools）。
- 命令输入行只在面板内生效，不会影响聊天输入。

## Checklist

- [ ] `appStore`：`showDebugConsole` + setter；`useKeyboardShortcuts`：Ctrl+Shift+D 呼出/收起、Esc 关闭
- [ ] 新建 `DebugConsole.tsx`：右滑悬浮 overlay；tail（自动滚动）+ history 回放 + `[renderer]` 过滤 + 复制 + 命令输入行
- [ ] 日志 WS hook：开连关断、收 `logs_history`/`logs_line`、发 `renderer_log`/`command`
- [ ] App 根错误采集（error/warn/onerror/unhandledrejection）→ 本地环形 100 条 → 连接时补传
- [ ] 手工验收：
  - [ ] Ctrl+Shift+D 呼出 → 实时日志滚动 + 历史回放
  - [ ] 敲 `status` → `[cmd] status ...` 出现在同一视图
  - [ ] 故意抛前端错误 → 面板里出现 `[renderer]` 行（含面板打开前发生的，靠补传）
  - [ ] `[renderer]` 过滤开关、复制按钮生效；Esc/Ctrl+Shift+D 关闭
  - [ ] 聊天 WS 功能不受影响（日志走独立连接）