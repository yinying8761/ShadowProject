# 01 — 搜索路由降级为提示注入（message_augmenter）

**What to build:** `need_search()` 关键词命中（HARD + SOFT 触发词）时，augmenter
不再替用户执行搜索、也不再读写结果缓存，只在消息后追加一句轻量提示（如「用户消息可能
涉及时效或外部信息，可考虑调用 research 工具核实」），由主 LLM 自主决定是否调用
`research` 工具。未命中消息原样通过；POI 阶段不受影响。

**Blocked by:** None — 可直接开始。

**Status:** ready-for-agent

**参考:** `docs/specs/search-augmenter-degrade.md`、`docs/adr/0003-search-decision-rights.md`

**关键语义：**

- 提示的注入**不要**套用现有"搜索参考资料"包装（`_inject_context` 的 `[帮助AI回答的搜索参考资料...]`
  是给真实结果用的，套在提示上会误导 LLM 以为已有资料）——提示应是轻量的建议性话语。
- `need_search` 保留（HARD + SOFT 都命中提示路径），词表维持现状。
- augment 签名与调用点（`chat.py`）不变；仅 Stage 1 行为变更。
- 不再 `from tools.search_tools import research`、不再 `from core.router import cache_get, cache_set`。

## Checklist

- [ ] `services/message_augmenter.py` Stage 1：`need_search()` 命中 → 追加提示语；
      移除 `cache_get`/`cache_set` 依赖与 `research()` 调用
- [ ] 新建 `backend/tests/services/test_message_augmenter.py`（当前该模块无任何测试）：
  - [ ] HARD 触发词命中 → 返回内容含提示，且验证搜索零调用（patch `_do_search`/老 `research` 断言未执行）
  - [ ] SOFT 触发词命中 → 同样出提示
  - [ ] 未命中 → 输出与输入一致（无 POI 词时）
  - [ ] 含美食词 → POI 注入仍工作（位置服务不可用时优雅降级，不崩溃）
- [ ] 手动冒烟：聊天发「今天天气怎么样」→ augmenter 只加提示不搜索；主 LLM 自行决定
      是否调 `research`（Deliverable：决策权在主 LLM）
- [ ] 全量测试通过：`python -m pytest tests/ -v`