# 02 — 前端 localStorage guard（防御层）

**What to build:** `useDailyGreeting` 发送 `daily_greeting` 消息前检查 `localStorage` 中是否已有今天的 `daily_greeting_date`。已标记则完全不启动轮询，消除 React StrictMode 双 mount 导致的冗余 WebSocket 消息。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] mount effect 的 `setInterval` 回调第一行：检查 `localStorage.getItem('daily_greeting_date') === today` → 成立则 `clearInterval` 并 return
- [ ] visibility handler 入口：同样检查 `localStorage`，当天已标记则跳过
- [ ] 手动验证：打开应用 → 只收到一条问候
- [ ] 手动验证：最小化再恢复窗口 → 不重复发送
- [ ] 手动验证：第二天打开 → 正常收到新问候
