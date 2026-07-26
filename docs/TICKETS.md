# TICKETS — 拆分 MemoryService

**父 Spec:** `docs/specs/candidate-4-memory-service-split.md`
**日期:** 2026-07-22
**状态:** ready-for-agent

---

## 依赖关系

```
#1  →  #2
#1  →  #3
#2 + #3  →  #4
```

MemoryStore（#1）是底层的存储基础。#2 和 #3 都只依赖 #1，可并行实施。#4 在所有模块就位后收尾删除旧代码。

---

## #1 — 提取 MemoryStore

**What to build:** 新建 MemoryStore 模块，将 FTS5 虚拟表设置、embedding 二进制打包/解包、CRUD 操作（add）、FTS5 同步更新、`find_similar`（去重查询）、`prune`（过期剪枝）移入独立的存储适配器。MemoryService 内部实例化 MemoryStore 并委托存储相关调用。所有现有功能通过向后兼容单例正常工作。

**Blocked by:** None — 可立即开始

**Status:** ready-for-agent

- [ ] 新建 `backend/services/memory_store.py`，包含 `MemoryStore` 类
- [ ] 移动 `_ensure_fts5` → `MemoryStore.ensure_fts5()`（保持为静态方法）
- [ ] 移动 `_pack_embedding` / `_unpack_embedding` → `MemoryStore`（保持为静态方法）
- [ ] 移动 `add_memory` → `MemoryStore.add()`
- [ ] 移动 `_sync_fts5_update` → `MemoryStore.sync_fts5_update()`
- [ ] 移动 `_find_similar` → `MemoryStore.find_similar()`
- [ ] 移动 `prune` → `MemoryStore.prune()`
- [ ] `add()` 内部调用 `push_memory_notification`（从 `memory_service.py` 导入模块级函数）
- [ ] `memory_service.py` 中 `MemoryService` 内部实例化 `MemoryStore`，对应方法委托给 `self._store`
- [ ] 模块级 `_ensure_fts5()` 保留为代理函数，委托给 `MemoryStore.ensure_fts5()`（保证 `main.py` 启动流程不变）
- [ ] 现有测试通过，手动验证：启动应用 → 发送需要记忆的工具调用（`save_memory` / `search_memory`）→ 记忆存储和检索正常

---

## #2 — 提取 MemoryRetriever

**What to build:** 新建 MemoryRetriever 模块，将 `search()` 的完整混合检索流水线移入。MemoryRetriever 通过构造函数接收 MemoryStore 实例。流水线包括：FTS5 全文搜索 → embedding 余弦相似度计算 → Reciprocal Rank Fusion 合并 → 重要性/时效性加权 → 角色过滤。编写 MemoryRetriever 的单元测试，使用假 MemoryStore 验证排序逻辑。

**Blocked by:** #1

**Status:** ready-for-agent

- [ ] 新建 `backend/services/memory_retriever.py`，包含 `MemoryRetriever` 类
- [ ] `__init__(self, store: MemoryStore)` 构造函数注入
- [ ] 移动 `search()` → `MemoryRetriever.search()`
- [ ] 移动 `_cosine_similarity` / `_sanitize_fts5` → `MemoryRetriever`（静态方法）
- [ ] `MemoryService` 内部实例化 `MemoryRetriever(self._store)`，`search()` 委托给 `self._retriever.search()`
- [ ] 新建 `backend/tests/services/test_memory_retriever.py`
- [ ] 假 MemoryStore：返回预设 FTS5 结果和 memory 对象
- [ ] 测试：空查询 → 返回按重要性+访问时间排序的记忆
- [ ] 测试：有查询无 embedding → FTS5-only 排序
- [ ] 测试：有查询有 embedding → RRF 合并排序
- [ ] 测试：按 character_id 过滤
- [ ] 测试：FTS5 查询异常 → 降级不崩溃
- [ ] 现有测试通过，手动验证：正常聊天触发记忆检索 → 记忆相关的上下文被注入 system prompt

---

## #3 — 提取 MemoryExtractor

**What to build:** 新建 MemoryExtractor 模块，将 `extract_and_store()` 和 `_call_llm_for_extraction()` 移入。MemoryExtractor 通过构造函数接收 MemoryStore 实例。提取流程包括：加载最近消息 → 构建 transcript → 调用 LLM → JSON 解析与容错 → 去重检查 → embedding 计算 → 存储。编写 MemoryExtractor 的单元测试，使用假 MemoryStore 和假 LLM 服务验证提取和去重流程。

**Blocked by:** #1

**Status:** ready-for-agent

- [ ] 新建 `backend/services/memory_extractor.py`，包含 `MemoryExtractor` 类
- [ ] `__init__(self, store: MemoryStore)` 构造函数注入
- [ ] 移动 `extract_and_store()` → `MemoryExtractor.extract_and_store()`
- [ ] 移动 `_call_llm_for_extraction()` → `MemoryExtractor`（私有方法）
- [ ] `MemoryService` 内部实例化 `MemoryExtractor(self._store)`，`extract_and_store()` 委托给 `self._extractor.extract_and_store()`
- [ ] 新建 `backend/tests/services/test_memory_extractor.py`
- [ ] 假 MemoryStore：记录 `add()` 和 `find_similar()` 调用
- [ ] 假 LLM 服务：返回预设 JSON 提取结果
- [ ] 测试：消息不足 10 条 → 返回空列表，不调用 LLM
- [ ] 测试：LLM 返回有效 JSON → 解析并去重存储
- [ ] 测试：LLM 返回带 markdown 包裹的 JSON → 正确提取
- [ ] 测试：LLM 返回无效内容 → 容错不崩溃
- [ ] 测试：找到相似记忆 → 合并 importance（取更大值）并更新 content
- [ ] 测试：无相似记忆 → 创建新记忆，计算 embedding
- [ ] 现有测试通过，手动验证：连续发送多条消息 → 后台提取触发 → 新记忆被存储

---

## #4 — MemoryService 收尾

**What to build:** 在 MemoryStore、MemoryRetriever、MemoryExtractor 全部就位后，删除 `MemoryService` 中已委托的方法体（`search`、`add_memory`、`prune`、`extract_and_store`、`_call_llm_for_extraction`、`_find_similar`、`_sync_fts5_update`、`_pack_embedding`、`_unpack_embedding`、`_cosine_similarity`、`_sanitize_fts5`）。`MemoryService` 缩减为纯委托层，模块级常量和通知队列函数保持不变。全量测试回归，确保向后兼容单例仍正常工作。

**Blocked by:** #2, #3

**Status:** ready-for-agent

- [ ] 从 `MemoryService` 中删除所有已委托的方法体（保留委托调用）
- [ ] 删除所有已移动的静态方法（`_pack_embedding`、`_unpack_embedding`、`_cosine_similarity`、`_sanitize_fts5`）
- [ ] 删除 `_find_similar`、`_sync_fts5_update`、`_call_llm_for_extraction` 方法
- [ ] `MemoryService` 仅保留：构造函数（实例化三个子模块）、`search()`、`add_memory()`、`prune()`、`extract_and_store()`（均为单行委托）
- [ ] 模块级保留：`RRF_K`、`EXTRACTION_MIN_MESSAGES`、`EXTRACTION_MESSAGE_COUNT`、`push_memory_notification`、`pop_memory_notifications`、`_ensure_fts5`、`memory_service` 单例
- [ ] `pytest tests/ -v` 全量通过
- [ ] 手动冒烟测试：启动应用 → 发送消息 → 记忆检索正常 → 工具调用正常 → 长时间对话触发后台记忆提取

