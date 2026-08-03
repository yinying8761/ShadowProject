# 迭代计划 — Agent Harness 升级

> 日期：2026-07-31 | 状态：待实现

## 总览

四个 Workflow，按依赖链顺序执行：

```
A（用户画像）  →  B（上下文管理）  →  D（Tool Runtime）  →  C（Multi-Agent）
   1-2天             2-3天                3-5天                 4-7天
```

| # | Workflow | 核心目标 | 预计工作量 |
|---|----------|---------|-----------|
| A | 用户画像 | 让 AI 知道用户是谁（名字/性别/身份/自述），按角色分面 | 1-2 天 |
| B | 上下文管理 | 修复删除 bug + /compact + 记忆时间标记 | 2-3 天 |
| D | Tool Runtime | ToolRegistry 升级为 ToolRuntime（追踪 + 沙箱 + 限流/熔断预留） | 3-5 天 |
| C | Multi-Agent | LLM Router 拦截工具调用，分派 SearchAgent / ScreenAgent | 4-7 天 |

**总计约 10-17 天（业余时间）。**

---

## Workflow A：用户画像

**现状**：`user_name` 硬编码为 `"User"`，`relationship` 硬编码为 `"friend"`。无用户身份模型。

### 数据模型

新建 `UserProfile` 表（`backend/models/user_profile.py`）：

| 列 | 类型 | 说明 |
|---|------|------|
| `id` | String(36) PK | UUID |
| `character_id` | String(36) FK → `character_profiles.id`, nullable | NULL = 默认画像（fallback） |
| `user_name` | String(50) | AI 对用户的称呼 |
| `user_gender` | String(10), nullable | `男` / `女` / `其他` / 留空 |
| `user_occupation` | String(80), nullable | `大学生` / `打工人` / `自由职业` ... |
| `user_bio` | Text, nullable | 用户自述，自由文本 |
| `user_relationship` | String(50) | 用户与 AI 角色的关系，如 `朋友` / `助手和用户` |
| `created_at`, `updated_at` | DateTime | 时间戳 |

- 一对多：一个用户面对不同角色可有不同画像
- `character_id=NULL` 的默认行作为 fallback

### API

```
GET  /api/user-profile?character_id=xxx   → 获取画像（无则 fallback 到默认）
PUT  /api/user-profile?character_id=xxx   → 创建/更新画像
```

### 注入机制

**系统提示词（键值格式）：**

```
## About The User
- Name: 小明
- Gender: 男
- Identity: 大学生
- Relationship: 朋友
```

**`user_bio`：** 始终注入系统提示词（不靠记忆检索），置于键值后，自然语言段落。

**同时存 Memory：** 保存画像时 upsert 一条 `user_fact` 记忆（`source=user_stated`），覆盖旧的自述记忆。

### 角色设定格式

**保持不变：**
```
You are 小樱, a 邻家姐姐 female who serves as a 贴心伙伴.
```
> 角色人格注入保持自然语言格式。键值格式会使角色失去沉浸感，不适用。

### 改动清单

| 文件 | 改动 |
|------|------|
| `backend/models/user_profile.py` | **新建** — `UserProfile` 模型 |
| `backend/database.py` | 添加 `user_profile` 表创建 |
| `backend/api/user_profile.py` | **新建** — GET/PUT 端点 |
| `backend/main.py` | 注册路由 + seed 默认行 |
| `backend/core/prompt_manager.py` | `build_system_prompt()` 接收 `user_profile` 参数 |
| `backend/core/agent.py` | 加载 `UserProfile` 并传入 `build_system_prompt()`；保存画像时调用 `save_memory` |
| `DEFAULT_SYSTEM_PROMPT` | `## About The User` 段改为键值格式 |
| `frontend/` | 设置面板新增用户画像编辑表单 |

---

## Workflow B：上下文管理

**现状**：删除消息是硬删除，但前端乐观更新 + 静默吞错误导致网络失败时 DB 数据未删除。提示词/记忆/摘要缺少时间标记，导致 AI 混淆"前天"和"昨天"。

### B1：修复删除 Bug

**根因**：`HistoryOverlay.tsx:35-39` — optimistic update + `catch {/* ignore */}`

**修复**：

```ts
// HistoryOverlay.tsx
} catch {
  // 恢复 UI
  useChatStore.setState((s) => ({
    messages: [...s.messages, removedMsg].sort((a, b) =>
      new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
    ),
  }));
  // 提示用户
  toast.error('删除失败，请检查网络');
}
```

同时在 `chatStore.ts` 新增 `removeMessage` action，避免组件内直接 `setState`。

### B2：Compact（记忆提取时联动裁剪）

**触发时机**：每天记忆提取时（`handle_daily_greeting` 中 `memory_service.extract_and_store()` 之后）

**流程**：
```
1. MemoryExtractor 提取对话 → 存 Memory
2. summarize_and_trim(keep_count=12) → 删最旧消息 → 合并摘要
3. conversation.summary 更新
```

**常量**：`COMPACT_KEEP_COUNT = 12`（约 4-6 轮来回）

**手动触发**：可选，通过 `/api/conversations/{id}/compact` POST 端点支持。

### B3：记忆/摘要时间标记

**机制**：注入系统提示词时，根据 `created_at` 实时计算相对日期。

**格式规则**：

| 时间距离 | 显示 |
|---------|------|
| 今天 | `今天` |
| 昨天 | `昨天` |
| 2-7 天 | `X天前` |
| 1-4 周 | `X周前` |
| 1-5 月 | `X个月前` |
| ≥6 月 | `YYYY年M月` |

**实现**：`PromptManager` 新增 `format_relative_date(dt) -> str` 方法。

**注入位置**：

记忆注入：
```
## Memories About The User
- (3天前) 和用户聊了 Rust 的所有权概念...
- (1周前) 用户说要开始健身计划...
```

摘要注入：
```
## Previous Conversation Summary
(截至3天前) 用户最近在学 Rust，对所有权概念有困惑...
```

### 改动清单

| 文件 | 改动 |
|------|------|
| `frontend/src/components/chat/HistoryOverlay.tsx` | 修复删除错误处理 + UI 恢复 |
| `frontend/src/stores/chatStore.ts` | 新增 `removeMessage` action |
| `backend/core/conversation_manager.py` | `summarize_and_trim` 支持可配置 `keep_count` |
| `backend/core/prompt_manager.py` | 新增 `format_relative_date()` + 记忆/摘要格式化 |
| `backend/core/agent.py` | compact 触发逻辑；记忆注入加时间标记 |
| `backend/api/chat.py` | `handle_daily_greeting` 中记忆提取后触发 compact |
| `backend/api/conversation.py` | 新增 `POST /{id}/compact` 端点 |

---

## Workflow D：Tool Runtime

**现状**：`ToolRegistry` 是一个 62 行的薄字典封装（register / dispatch / get_tool_definitions / needs_approval）。无追踪、无沙箱、无熔断、无限流。

### 架构：渐进包裹

```
ToolRuntime (新增)          ← 对 Agent 暴露 register / dispatch / get_tool_definitions
  ├─ ToolRegistry (现有)     ← 保持注册/调度核心
  ├─ trace_run               ← dispatch 前后拦截
  ├─ sandbox                 ← subprocess 超时控制
  ├─ circuit_break (预留)    ← 接口占位
  └─ rate_limit (预留)       ← 接口占位
```

`Agent` 注入 `ToolRuntime` 替代 `ToolRegistry`，接口不变，零改动。

### D1：trace_run — 工具调用追踪

**存储**：SQLite `tool_runs` 表

| 列 | 类型 | 说明 |
|---|------|------|
| `id` | String(36) PK | UUID |
| `call_id` | String(36) | 每次工具调用的唯一标识 |
| `tool_name` | String(100) | 工具名 |
| `arguments` | JSON | 调用参数（截断至 2000 字符） |
| `result_summary` | String(500) | 结果摘要（截断） |
| `elapsed_ms` | Integer | 耗时（毫秒） |
| `success` | Boolean | 调用是否成功 |
| `error_message` | String(500), nullable | 失败时的错误信息 |
| `conversation_id` | String(36), nullable | 关联会话 |
| `created_at` | DateTime | 时间戳 |

**拦截逻辑**（`ToolRuntime.dispatch`）：
```python
async def dispatch(self, name: str, arguments: dict) -> str:
    call_id = str(uuid.uuid4())
    start = time.perf_counter()
    success = True
    error_msg = None
    result = ""
    try:
        result = await self._registry.dispatch(name, arguments)
    except Exception as e:
        success = False
        error_msg = str(e)
        result = json.dumps({"error": str(e)})
    elapsed = int((time.perf_counter() - start) * 1000)
    await self._trace_store.save(ToolRun(
        call_id=call_id, tool_name=name, arguments=arguments,
        result_summary=str(result)[:500], elapsed_ms=elapsed,
        success=success, error_message=error_msg,
    ))
    return result
```

**前端展示**：设置面板新增"工具调用日志"标签页，表格展示：
- 工具名 / 参数 / 耗时 / 成功 ✅ 或失败 ❌ / 时间
- 支持按工具名筛选、按时间排序

### D2：沙箱 — subprocess 超时控制

**现有 L1 隔离**：文件路径黑名单（`file_tools.py`）— 保持不动。

**新增 L1+**：`register()` 支持 `sandbox_config`：

```python
runtime.register("fetch_url", ..., handler,
    sandbox_config={"timeout_sec": 15, "network": True},
)

runtime.register("research", ..., handler,
    sandbox_config={"timeout_sec": 30, "network": True},
)
```

`ToolRuntime.dispatch()` 中用 `asyncio.wait_for(handler(**args), timeout)` 实现超时 kill。超时视为失败（`success=False`，记录到 `tool_runs`）。

所有工具的默认超时：读/写文件 10s，网络调用 30s，屏幕截取 15s，其他 10s。

### D3：预留扩展开关

`ToolRuntime.__init__` 接收 feature flags：

```python
class ToolRuntime:
    def __init__(self, registry=None, *,
        enable_tracing=True,
        enable_sandbox=True,
        enable_circuit_breaker=False,  # 预留
        enable_rate_limit=False,        # 预留
        circuit_threshold=5,            # 预留
        rate_limit_per_min=30,          # 预留
    ):
```

### 改动清单

| 文件 | 改动 |
|------|------|
| `backend/core/tool_runtime.py` | **新建** — `ToolRuntime` 类（~200 行） |
| `backend/models/tool_run.py` | **新建** — `ToolRun` 模型 |
| `backend/database.py` | 添加 `tool_runs` 表 |
| `backend/services/tool_trace_store.py` | **新建** — 异步写入 `tool_runs` |
| `backend/main.py` | `register_tools()` 改为注册到 `ToolRuntime`；注入 `Agent` |
| `backend/core/agent.py` | `tool_registry` 参数改为接受 `ToolRuntime`（接口兼容） |
| `backend/api/tool_logs.py` | **新建** — `GET /api/tool-runs` 查询端点 |
| `frontend/` | 设置面板新增"工具调用日志"页 |

---

## Workflow C：Multi-Agent（Router + Search/Screen Sub-Agent）

**现状**：单 Agent 架构。`research` 和 `see_screen` 是直接函数调用。没有子 Agent 或编排器。

### 架构：Router 拦截工具调用（方案 B）

```
用户消息 → MainAgent (角色人格 + LLM)
              │  LLM 说"需要搜索+看屏幕"
              │  调用 research → 被 Router 拦截
              ▼
           Router (LLM, 独立系统 prompt)
              │  分析任务，拆分并行/串行子任务
              ├─→ SearchAgent (独立 LLM, 有 fetch_url + duckduckgo 工具)
              │     │ 最多 2 轮工具调用
              │     ▼ 返回结构化结果
              └─→ ScreenAgent (独立 LLM + Vision)
                    │ 看到屏幕内容 → 自然语言描述
                    ▼ 返回描述
              │
              ▼ Router 汇总子 Agent 结果
              │
              ▼ 返回给 MainAgent（作为 tool_result）
              │
              ▼ MainAgent 用人格整合 → 回复用户
```

### 各组件职责

**Router Agent**：
- 系统 prompt：`"你是任务调度员。接收主 Agent 的搜索/屏幕请求，决定是否需要并行执行，分派给子 Agent，汇总结果。返回简洁、结构化的中文总结。"`
- 输入：MainAgent 想调用 research 或 see_screen 时的 query
- 输出：汇总后的结果文本
- 独立 LLM（可用更便宜的小模型）

**SearchAgent**：
- 工具集：`fetch_url`, `duckduckgo`（或复用现有 `_do_search`）
- 系统 prompt：`"你是搜索助手。根据查询搜集信息，返回准确、有来源的总结。"`
- 最多 2 轮工具调用
- 独立 LLM

**ScreenAgent**：
- 工具集：`see_screen`（调用 vision_service）
- 系统 prompt：`"你是屏幕观察员。描述屏幕上的内容，关注用户正在做的事情。"`
- 单次调用，无工具循环
- 独立 LLM（可选 Visual 模型）

**MainAgent**：
- 保持现状——角色的完整人格 + 对话历史 + 工具（research/see_screen → 由 Router 代理）
- 对 Router 的存在的感知透明——它以为自己只是调用了 research 工具

### 改动清单

| 文件 | 改动 |
|------|------|
| `backend/core/sub_agent.py` | **新建** — `SubAgent` 基类（独立 LLM + 工具循环） |
| `backend/core/router_agent.py` | **新建** — `RouterAgent`（LLM Router） |
| `backend/core/search_agent.py` | **新建** — `SearchAgent` |
| `backend/core/screen_agent.py` | **新建** — `ScreenAgent` |
| `backend/core/agent.py` | `_execute_tools_with_approval()` 中：`research` / `see_screen` 走 Router |
| `backend/main.py` | 实例化 Router + 子 Agent，注入 |
| `backend/core/tool_runtime.py` | Router 注册为 research 和 see_screen 的 handler |

---

## 文件清单总览

### 新建文件

```
backend/
├── models/
│   ├── user_profile.py          # A: UserProfile 模型
│   └── tool_run.py              # D: ToolRun 模型
├── api/
│   ├── user_profile.py          # A: GET/PUT /api/user-profile
│   └── tool_logs.py             # D: GET /api/tool-runs
├── core/
│   ├── tool_runtime.py          # D: ToolRuntime 类
│   ├── sub_agent.py             # C: SubAgent 基类
│   ├── router_agent.py          # C: RouterAgent
│   ├── search_agent.py          # C: SearchAgent
│   └── screen_agent.py          # C: ScreenAgent
└── services/
    └── tool_trace_store.py      # D: 追踪存储
```

### 修改文件

```
backend/
├── main.py                      # A/B/D: 注册路由 + ToolRuntime 集成
├── database.py                  # A/D: 新增表
├── config.py                    # A: 可能新增 user_name fallback 配置
├── core/
│   ├── agent.py                 # A/B/C: 用户画像加载 + compact + Router 拦截
│   ├── prompt_manager.py        # A/B: 用户画像注入 + 时间格式化
│   └── conversation_manager.py   # B: 可配置 keep_count
├── api/
│   ├── chat.py                  # B: compact 触发
│   └── conversation.py          # B: POST /{id}/compact
└── tools/
    └── memory_tools.py          # A: 画像保存时写 Memory

frontend/
├── src/
│   ├── components/
│   │   ├── chat/HistoryOverlay.tsx    # B: 删除 bug 修复
│   │   └── settings/                  # A/D: 用户画像页 + 工具日志页
│   └── stores/
│       └── chatStore.ts              # B: removeMessage action
```

---

## 运行方式

```bash
# A: 用户画像
curl -X PUT /api/user-profile?character_id=xxx \
  -d '{"user_name":"小明","user_gender":"男","user_occupation":"大学生","user_bio":"...","user_relationship":"朋友"}'

# B: Compact
curl -X POST /api/conversations/{id}/compact

# D: 查看追踪日志
curl GET /api/tool-runs?tool_name=research&limit=50

# C: Router 对用户透明，正常聊天即可
```
