# 01 — `extract_and_store` 加 `before` 参数（预重构）

**What to build:** `extract_and_store` 新增可选参数 `before: datetime | None`。传入后，消息查询从 `created_at >= since_date` 变为 `created_at >= since_date AND created_at < before`，排除快照之后的消息（如问候语）。不传时行为与原来完全一致。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] `MemoryExtractor.extract_and_store` 签名新增 `before: datetime | None = None`
- [ ] 有 `since_date` 时：在 `where` 子句中追加 `Message.created_at < before` 条件
- [ ] 无 `since_date` 时（走 `EXTRACTION_MESSAGE_COUNT` 分支）：同样支持 `before` 过滤
- [ ] `MemoryService.extract_and_store` 透传 `before` 参数
- [ ] `before=None` 时不改变任何查询行为（向后兼容）
- [ ] 测试：传入 `before=快照`，快照之后的消息不出现在结果中
- [ ] 测试：不传 `before` 时行为不变（现有 6 个测试仍通过）
