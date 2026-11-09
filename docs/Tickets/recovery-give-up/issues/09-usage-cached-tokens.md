# 09 — 可观测：llm_usage 记录缓存命中 tokens

**What to build:** 把 API 已在响应里返回、但当前被丢弃的用量持久化：`llm_usage` 增加缓存命中字段，成本可按「新输入 / 缓存命中输入 / 输出」三档拆分。两种形状：Chat Completions 的 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`，Responses API 的 `input_tokens_details.cached_tokens`。不改计费逻辑，只做记录。

**Blocked by:** None — 可立即开始（建议排在 #74 之后，避免与基线重建并行）。

**Status:** ready-for-agent

**参考:** docs/specs/recovery-give-up.md

- [ ] `llm_usage` 增加缓存字段（增量迁移；旧数据可读）
- [ ] 采集侧支持两种响应形状，取不到时降级为 0/NULL，不影响主流程
- [ ] 统计能区分 新输入 / 缓存命中 / 输出
- [ ] 测试：两种响应体解析、字段缺失降级、迁移后旧行可读
