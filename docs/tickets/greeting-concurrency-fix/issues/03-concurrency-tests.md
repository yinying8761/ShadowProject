# 03 — 并发测试

**What to build:** 验证并发重构后的四项行为：问候先于提取返回、`before` 正确过滤、后台失败不影响问候、`before=None` 向后兼容。

**Blocked by:** 02 — 测的是并发重构后的行为。

**Status:** ready-for-agent

- [ ] 测试：mock `extract_and_store` 延迟 1 秒 → 验证 `daily_greeting` done 事件在提取完成前发出（问候先行）
- [ ] 测试：快照之后插入一条消息 → `extract_and_store(before=snapshot)` 结果中不包含该消息
- [ ] 测试：后台 task 抛异常 → 问候正常返回，不报错，不影响用户
- [ ] 测试：不传 `before` 时 `extract_and_store` 行为不变（现有 6 个测试保持通过）
