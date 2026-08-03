# 01 — 修复删除消息的幽灵上下文 Bug

**What to build:** 用户在历史面板删除消息，网络正常则消息真正从数据库删除，AI 后续对话读不到。网络失败时消息恢复到 UI 列表，用户看到错误提示，知道删除未成功。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] `HistoryOverlay.tsx` handleDeleteMsg 的 catch 分支：恢复 removedMsg 到 Zustand store（按 created_at 排序），重置 confirmingDelete
- [ ] 删除失败时显示错误 toast："删除失败，请检查网络"
- [ ] `chatStore.ts` 新增 `removeMessage` action，封装从消息列表移除的逻辑
- [ ] `HistoryOverlay.tsx` 改用 `removeMessage` action 而不是直接 `setState`
- [ ] 手动冒烟验证：断网 → 删消息 → 消息恢复 + toast 出现
