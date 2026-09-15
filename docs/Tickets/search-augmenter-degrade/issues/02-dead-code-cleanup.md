# 02 — 删除死代码：老 research() + 5 分钟结果缓存

**What to build:** 删除老 `tools/search_tools.research()`（`_do_search` + 一次独立
LLM 总结调用）和 `core/router.py` 的 5 分钟结果缓存（`cache_get`/`cache_set`）。
SearchAgent 成为整个应用唯一的搜索实现（主 Agent 的 `research` 工具走它）；仓库内
不再残留任何对已删符号的引用，全量测试保持绿色。

**Blocked by:** #01（augmenter 必须先停止引用 `research()` 与缓存，本票才能删得干净）

**Status:** ready-for-agent

**参考:** `docs/specs/search-augmenter-degrade.md`、`docs/adr/0003-search-decision-rights.md`

**关键语义：**

- **保留**：`_do_search`/`_search_duckduckgo_sync`/`_search_searxng`/`_search_bing_web`/
  `_search_bing`/`fetch_url`（SearchAgent 内部工具仍用）。
- **保留**：`need_search` 与 HARD/SOFT 词表。
- **删除**：老 `research()` 函数（连同只被它用的模块级 `_llm = LLMService()` 实例）；
  `router.py` 的 `_cache`/`CACHE_TTL`/`cache_get`/`cache_set`。
- 行为不变：主 Agent 调 `research` 工具 → SearchAgent 子智能体路径，结果与改动前一致。

## Checklist

- [ ] `tools/search_tools.py`：删除老 `research()`（保留 `_do_search` 等；确认 `_llm` 无其他引用后一并删）
- [ ] `core/router.py`：删除 `_cache`/`CACHE_TTL`/`cache_get`/`cache_set`（保留 `need_search`/词表）
- [ ] grep 确认 `backend/` 内无 `research`/`cache_get`/`cache_set` 死引用残留
- [ ] 现有 SearchAgent 测试通过：`python -m pytest tests/core/test_search_agent.py -v`
- [ ] 全量测试通过：`python -m pytest tests/ -v`
- [ ] 手动冒烟：聊天触发主 LLM 调 `research` → 搜索结果/总结与改动前一致（SearchAgent 路径）