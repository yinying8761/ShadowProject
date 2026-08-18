# 03 — 前端「用量统计」设置标签页

**What to build:** 设置面板新增「用量统计」标签页（`UsagePanel`），展示当前会话的累计 token
消耗（prompt/completion/总数）+ 逐轮明细（含预估 vs 实际）。刷新时机：打开标签页时、切换
会话时、收到 `done`（一轮结束）时（观察 `chatStore.streaming` true→false）。纯 REST，不新增
WS 事件。

**Blocked by:** #02（需要 `GET /api/token-usage`）

**Status:** ready-for-agent

**不做：** 按工具调用的 token 分布图表（后续迭代）。前端无测试框架，本 ticket 用手动冒烟验证。

## Checklist

- [ ] `frontend/src/types/index.ts` 新增类型：

```typescript
export interface TokenUsageEntry {
  id: string;
  conversation_id: string;
  round_num: number;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_prompt_tokens: number;
  created_at: string | null;
}

export interface TokenUsageSummary {
  rounds: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface TokenUsageResponse {
  total: number;
  usage: TokenUsageEntry[];
  summary: TokenUsageSummary;
}
```

- [ ] `frontend/src/services/api.ts` 新增：

```typescript
fetchTokenUsage: (conversationId: string) =>
  request<TokenUsageResponse>(
    `/token-usage?conversation_id=${encodeURIComponent(conversationId)}`
  ),
```

- [ ] `SettingsNav.tsx`：`NavKey` 加 `'usage'`，`NAV_ITEMS` 加 `{ key: 'usage', label: 'Usage' }`
- [ ] `SettingsPanel.tsx`：`NavKey` 加 `'usage'`，`renderContent()` 加 `case 'usage': return <UsagePanel />`，导入 `UsagePanel`
- [ ] 新建 `frontend/src/components/settings/UsagePanel.tsx`，要点：
  - 从 `useChatStore((s) => s.currentConversationId)` 取当前会话
  - `useEffect` 依赖 `[currentConversationId, streaming]`：`currentConversationId` 变化时、以及
    `streaming` 从 true→false（一轮结束）时调 `api.fetchTokenUsage(currentConversationId)`
  - 无 `currentConversationId` 或 `summary.rounds === 0` 时渲染空态文案（不报错）
  - 展示：顶部 `summary`（prompt/completion/总数），下方逐轮列表（轮次、模型、prompt/completion/total、
    预估 `estimated_prompt_tokens` vs 实际 `prompt_tokens`）
- [ ] i18n：`translations.ts` 为「Usage」相关文案补中/英（若无词条则加）
- [ ] 手动冒烟：真实对话后打开设置 → 用量统计标签页 → 显示累计与逐轮；再发一条消息 → 收到 `done`
      后数字自动更新；切换会话 → 数据切到新会话
