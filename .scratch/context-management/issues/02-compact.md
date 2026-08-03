# 02 — Compact：记忆提取后自动裁剪 + 手动端点

**What to build:** 每天记忆提取完成后自动裁剪对话历史（保留最近 12 条消息），旧消息被 LLM 摘要替代。用户可通过 `POST /api/conversations/{id}/compact` 手动触发裁剪。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] `ConversationManager.summarize_and_trim()` 默认 `keep_count=12`
- [ ] `handle_daily_greeting` 中 `extract_and_store` 之后调用 `summarize_and_trim(keep_count=12)`
- [ ] 新增 `POST /api/conversations/{id}/compact` 端点，返回 `deleted_count` 和 `summary`
- [ ] 手动 compact 时传入 `llm_service` 实例生成摘要
- [ ] 测试：12 条消息时 compact 不裁剪
- [ ] 测试：30 条消息时 compact 删除 18 条 + summary 更新
- [ ] 测试：compact 端点 200（成功）和 404（不存在的会话）
