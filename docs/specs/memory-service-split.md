# Spec: 拆分 MemoryService — 存储适配器 vs. 检索逻辑 vs. 提取编排

**状态:** `ready-for-agent`
**日期:** 2026-07-22
**来源:** `/improve-codebase-architecture` 候选方案 #4

---

## Problem Statement

`backend/services/memory_service.py` 目前是一个 509 行的混合模块，在一个 `MemoryService` 类中混杂了三层不同的关注点：

- **存储：** FTS5 虚拟表/触发器创建、二进制 embedding 打包/解包、CRUD 操作、FTS5 同步、过期剪枝
- **检索：** 混合搜索流水线（FTS5 BM25 → embedding 余弦相似度 → RRF 合并 → 重要性/时效性加权 → 角色过滤）
- **编排：** LLM 驱动的记忆提取、JSON 解析与容错、去重（字符级加权重叠算法）、embedding 计算与存储

同时还有模块级函数（`_ensure_fts5`、`push/pop_memory_notifications`）和模块级单例（`memory_service`），调用者必须了解这些隐式入口。

这个模块是**浅层的**——`search()` 方法背后是 135 行的复杂流水线，但模块没有干净的内部接缝。真正的接缝——切换存储后端（例如从 SQLite+FTS5 到向量数据库）、替换排序算法（例如从 RRF 到学习型排序）、隔离 LLM 提取逻辑——都不存在。每个关注点都无法独立测试。

## Solution

将 `MemoryService` 拆分为三个深层模块，每个模块拥有窄接口：

| 模块 | 文件 | 接口 | 提取行数 | 职责 |
|---|---|---|---|---|
| `MemoryStore` | `services/memory_store.py` | `add()`, `find_similar()`, `prune()`, `ensure_fts5()` | ~180 行 | FTS5 设置、CRUD、embedding 打包、FTS5 同步、过期剪枝 |
| `MemoryRetriever` | `services/memory_retriever.py` | `search(query, character_id, top_k) → list[Memory]` | ~150 行 | 混合搜索：FTS5 → embedding 余弦 → RRF → 重要性/时效性 → 角色过滤 |
| `MemoryExtractor` | `services/memory_extractor.py` | `extract_and_store(conversation_id, llm_service) → list[Memory]` | ~120 行 | LLM 提取、JSON 解析、去重、embedding 计算、存储 |

模块级函数（`push/pop_memory_notifications`、常量）保留在精简后的 `memory_service.py` 中（~40 行），该文件同时提供一个向后兼容的 `memory_service` 单例，委托给上述三个模块。

### 依赖关系

```
MemoryStore  （无依赖 — 只依赖 database 和 models）
    ↑
MemoryRetriever(store)   MemoryExtractor(store, ?)
    ↑                           ↑
memory_service（向后兼容单例，委托给上述模块）
```

Retriever 和 Extractor 均通过构造函数接收 `MemoryStore` 实例。`MemoryExtractor` 还需要 LLM 服务——但它不存储 LLM 服务引用，而是在 `extract_and_store()` 方法中接收 `llm_service` 参数（与现有接口一致）。

## User Stories

1. 作为开发者，我希望修改混合搜索的排序算法时只需编辑 `MemoryRetriever`，而不触及存储或提取逻辑，这样改动的风险更可控。
2. 作为开发者，我希望将 SQLite+FTS5 替换为向量数据库时只需替换 `MemoryStore`，检索和提取模块无需感知存储后端变化。
3. 作为开发者，我希望调整 LLM 提取的 prompt 或 JSON 解析容错逻辑时只需编辑 `MemoryExtractor`，而不在 500 行文件中翻找。
4. 作为测试者，我希望用返回已知记忆的假 `MemoryStore` 来测试 `MemoryRetriever` 的排序逻辑，而不需要设置 FTS5 触发器。
5. 作为测试者，我希望用假 LLM 服务和假 `MemoryStore` 来测试 `MemoryExtractor` 的去重和存储流程。
6. 作为测试者，我希望用内存 SQLite + FTS5 测试 `MemoryStore` 的 CRUD 和全文搜索，而不依赖生产数据库。
7. 作为现有代码的维护者，我希望 `agent.py`、`chat.py`、`memory_tools.py` 中所有 `memory_service.search()` / `add_memory()` 调用保持向后兼容——拆分后无需修改调用者。
8. 作为新加入的开发者，我希望理解记忆搜索功能时只需阅读一个约 150 行的模块，而不是在 509 行的文件中追踪三个关注点。
9. 作为部署者，我希望启动时的 FTS5 初始化（`_ensure_fts5()`）和记忆剪枝（`prune()`）调用保持兼容，不做任何改动。

## Implementation Decisions

### 决策 1：提取顺序（由底向上）

1. **MemoryStore** — 最底层，无业务逻辑依赖。包含 FTS5 设置、embedding 打包/解包、CRUD（`add`、`delete`）、FTS5 同步（`_sync_fts5_update`）、`find_similar`（FTS5 去重查询）、`prune`。
2. **MemoryRetriever** — 依赖 MemoryStore。包含 `search()` 的完整混合检索流水线。
3. **MemoryExtractor** — 依赖 MemoryStore。包含 `extract_and_store()` 和 `_call_llm_for_extraction()`。
4. **精简 memory_service.py** — 保留模块级函数和向后兼容单例。

每个阶段完成后验证现有功能不受影响，再进入下一阶段。

### 决策 2：MemoryStore 接口

```python
class MemoryStore:
    # FTS5 setup (replaces module-level _ensure_fts5)
    @staticmethod
    async def ensure_fts5()

    # Embedding helpers (static, unchanged)
    @staticmethod
    def pack_embedding(vec: list[float]) -> bytes
    @staticmethod
    def unpack_embedding(data: bytes) -> list[float]

    # CRUD
    async def add(session, content, memory_type, importance,
                  source_conversation_id, embedding) -> Memory

    # FTS5 sync (called after content update during dedup)
    @staticmethod
    async def sync_fts5_update(mem_id, old_content, new_content, memory_type)

    # FTS5-based similarity search for dedup
    async def find_similar(session, content, threshold=0.85) -> Memory | None

    # Maintenance
    async def prune() -> int
```

`add()` 内部会在成功插入后调用 `push_memory_notification`——通知逻辑保持在模块级别，Store 从模块导入。

### 决策 3：MemoryRetriever 接口

```python
class MemoryRetriever:
    def __init__(self, store: MemoryStore)

    async def search(session, query: str, top_k: int = 3,
                     character_id: str | None = None) -> list[Memory]

    # Static helpers moved from MemoryService
    @staticmethod
    def _cosine_similarity(a, b) -> float
    @staticmethod
    def _sanitize_fts5(query: str) -> str
```

`search()` 内部的 FTS5 查询通过 `self.store` 间接访问 session 和原始 SQL（FTS5 MATCH 查询是 SQL 文本，不适合封装在 Store 方法中——或者 Store 提供 `search_fts5(session, query, limit)` 方法）。这个细节在实现时决定。

### 决策 4：MemoryExtractor 接口

```python
class MemoryExtractor:
    def __init__(self, store: MemoryStore)

    async def extract_and_store(conversation_id: str, llm_service) -> list[Memory]
```

`_call_llm_for_extraction` 保持为私有方法。`extract_and_store` 内部使用 `self.store` 进行去重检查（`find_similar`）、FTS5 同步（`sync_fts5_update`）和存储（`add`）。

### 决策 5：向后兼容单例

精简后的 `memory_service.py`（~40 行）提供：

```python
# 模块级常量（不变）
RRF_K = 60
EXTRACTION_MIN_MESSAGES = 10
EXTRACTION_MESSAGE_COUNT = 20

# 通知队列（不变）
def push_memory_notification(conversation_id, count)
def pop_memory_notifications(conversation_id) -> int

# FTS5 初始化代理
async def _ensure_fts5()  # 委托给 MemoryStore.ensure_fts5()

# 向后兼容单例
class MemoryService:
    def __init__(self):
        self._store = MemoryStore()
        self._retriever = MemoryRetriever(self._store)
        self._extractor = MemoryExtractor(self._store)

    async def search(self, session, query, top_k=3, character_id=None):
        return await self._retriever.search(session, query, top_k, character_id)

    async def add_memory(self, session, content, **kwargs):
        return await self._store.add(session, content, **kwargs)

    async def prune(self):
        return await self._store.prune()

    async def extract_and_store(self, conversation_id, llm_service):
        return await self._extractor.extract_and_store(conversation_id, llm_service)

memory_service = MemoryService()
```

所有现有调用者（`agent.py`、`chat.py`、`memory_tools.py`、`main.py`）无需任何修改。

### 决策 6：不做的事

- **不修改任何调用者。** `agent.py`、`chat.py`、`memory_tools.py`、`main.py` 的 import 和调用方式完全不变。
- **不改变 `push/pop_memory_notifications` 的 API。** 它们保持为模块级函数。
- **不改变数据库 schema。** FTS5 虚拟表结构不变。
- **不引入新的外部依赖。** 仅移动现有代码。

### 决策 7：通知队列的处理

`push_memory_notification` 在两个地方被调用：`add_memory()`（MemoryStore）和 `extract_and_store()`（MemoryExtractor）。两者都从 `memory_service.py` 导入模块级函数。通知队列保持为模块级全局——它本质上是跨模块的事件总线，不适合归属任何一个模块。

## Testing Decisions

### 测试哲学

只测试外部行为。每个模块通过其公开接口测试。使用内存 SQLite + FTS5 作为存储后端（`sqlite+aiosqlite://`），与 `test_agent_tools.py` 中的集成测试模式一致。

### 测试 MemoryStore

- 使用内存 SQLite 数据库（`create_async_engine("sqlite+aiosqlite://")`）
- 测试用例：
  - `add()` → 返回 Memory 对象，content/memory_type/importance 正确
  - `add()` 含 embedding → embedding 被正确打包存储
  - `find_similar()` → 插入两条相似内容 → 找到匹配（加权重叠 ≥ 0.85）
  - `find_similar()` → 插入两条不相关内容 → 返回 None
  - `prune()` → 插入旧的低重要性记忆 → 被删除
  - `prune()` → 插入新的高重要性记忆 → 保留
  - `ensure_fts5()` → FTS5 表创建成功 → 重复调用不报错

### 测试 MemoryRetriever

- 假 MemoryStore：返回预设的记忆列表（不依赖真实 FTS5）
- 假 embedding_service：返回预设的 query embedding
- 测试用例：
  - 空查询 → 返回按重要性+访问时间排序的记忆
  - 有查询无 embedding → 退化为 FTS5-only 排序
  - 有查询有 embedding → RRF 合并排序
  - 按 character_id 过滤 → 只返回属于指定角色的记忆
  - FTS5 查询失败 → 降级到 fallback 排序（不崩溃）

### 测试 MemoryExtractor

- 假 MemoryStore：记录 `add()` 和 `find_similar()` 的调用
- 假 LLM 服务：返回预设的 JSON 提取结果
- 测试用例：
  - 消息不足 EXTRACTION_MIN_MESSAGES → 返回空列表，不调用 LLM
  - LLM 返回有效 JSON → 解析、去重、存储
  - LLM 返回带 markdown 包裹的 JSON → 提取并解析
  - LLM 返回无效 JSON → 容错处理，不崩溃
  - 找到相似记忆 → 合并 importance（取更大值），更新 content
  - 无相似记忆 → 创建新记忆，计算 embedding

### 已有测试先例

- `backend/tests/core/test_agent_tools.py` — 使用内存 SQLite（`sqlite+aiosqlite://`）+ `monkeypatch` + `pytest-asyncio`
- `backend/tests/services/formatters/test_anthropic_formatter.py` — 使用 `pytest.fixture` + 类组织测试

### 建议的测试文件

- `backend/tests/services/test_memory_store.py` — MemoryStore 单元测试
- `backend/tests/services/test_memory_retriever.py` — MemoryRetriever 单元测试
- `backend/tests/services/test_memory_extractor.py` — MemoryExtractor 单元测试

## Out of Scope

- **不切换到向量数据库。** MemoryStore 目前只有 SQLite+FTS5 一个适配器。为未来切换预留的接缝是副产品，不在本次实现范围内。
- **不改变 FTS5 schema 或搜索算法。** 仅移动代码，不优化算法。
- **不提取通知队列。** `push/pop_memory_notifications` 保持模块级，不作为独立模块。
- **不修改 Agent、chat.py、memory_tools 的调用方式。** 向后兼容单例确保现有调用者不变。
- **候选方案 #1-#3、#5-#6。** 各自独立。候选方案 #1 已部分完成（chat.py 拆分），候选方案 #5 已完成（ToolRegistry 注入）。

## Further Notes

- 这是 `/improve-codebase-architecture` 报告中 6 个候选方案中的第 4 个。建议顺序中的候选方案 #1-#2 已通过本次会话之前的重构完成，#5 已在本会话中完成。
- `MemoryService.search()` 是代码库中调用最频繁的服务方法之一（agent.py、chat.py、memory_tools.py 共 4 处调用点）。向后兼容至关重要——如果单例委托链出错，用户聊天中的记忆检索将完全失效。
- 通知队列（`push/pop_memory_notifications`）是一个有趣的架构问题。它本质上是一个跨模块的事件总线，当前用模块级 `defaultdict` 实现。如果未来需要支持多进程或持久化通知，这是一个天然的接缝。
- 提取完成后，`memory_service.py` 从 509 行缩减到约 40 行（只有兼容单例、常量和通知队列函数），三个新模块合计约 450 行（略多于原来的 509 行，因为新增了类定义、import 和方法签名的开销，但每个模块的职责是单一的）。
