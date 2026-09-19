# 06 — Electron F12 → 真 DevTools

**What to build:** 打包后的桌面应用（及 dev 模式）按 F12 也能打开真 Chrome DevTools——脱离
dev.bat 黑窗口即可调试前端 JS / DOM / 网络。

**Blocked by:** None — 可直接开始。

**Status:** ready-for-agent

**参考:** `docs/specs/debug-console-logs.md`

**关键语义：**

- 在 Electron 主进程对主窗口 `webContents` 用 `before-input-event` 拦 F12 → `openDevTools()`。
- 与 Ctrl+Shift+D 面板分工：F12 = 浏览器真 DevTools；面板 = 前后端日志汇流 + 白名单命令。
- 只处理 F12，不劫持其他按键。

## Checklist

- [ ] Electron 主进程：主窗口 `before-input-event` 处理 F12 → `webContents.openDevTools()`
- [ ] 手工验收：dev 模式（:16173）与打包模式分别按 F12 → DevTools 弹出；其他按键行为不变