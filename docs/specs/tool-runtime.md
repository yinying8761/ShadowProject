# Tool Runtime — ToolRegistry 升级为 ToolRuntime

> 日期：2026-08-04 | 状态：待实现 | 父迭代：`iteration-plan.md` Workflow D

## Problem Statement

当前 `ToolRegistry` 是一个 62 行的薄字典封装，只有注册/调度/工具定义导出/审批标记四个功能。每次工具调用发生了什么完全不可见——调用是否成功、耗时多久、参数是什么、失败原因是什么，全部没有记录。用户无法在设置面板里看到"上次 research 花了 3 秒、成功返回"，也无法排查"为什么 see_screen 老是失败"。

另外，工具调用没有超时保护——如果某个 handler 卡死，整个 Agent loop 会被拖住，用户体验差。

## Solution

`ToolRegistry` 保持不变（作为注册/调度核心），新增 `ToolRuntime` 作为外层包裹，在 `dispatch()` 前后拦截，增加三层能力：

1. **D1 trace_run** — 每次工具调用的追踪（SQLite 落表 + 前端日志页）
2. **D2 sandbox** — 每个 handler 的超时控制（`asyncio.wait_for`）
3. **D3 预留扩展** — `circuit_break` / `rate_limit` 接口占位，feature flag 关掉

对 Agent 透明：`ToolRuntime` 暴露和 `ToolRegistry` 完全相同的四个方法（`register` / `dispatch` / `get_tool_definitions` / `needs_approval`），Agent 不需要改一行代码。

## User Stories

1. As a 用户，I want 在设置面板看到最近的工具调用日志（工具名、参数、耗时、成功/失败），so that 我能了解 AI 在后台做了什么、排查异常。
2. As a 用户，I want 按工具名筛选工具调用日志，so that 我能只看 research 或 see_screen 的调用记录。
3. As a 用户，I want 工具调用有超时保护，so that 一个卡住的 handler 不会让整个对话卡死。
4. As a 开发者，I want 每次 dispatch 自动记录追踪数据到 SQLite，so that 不需要手动加日志就能复现场景。
5. As a 开发者，I want ToolRuntime 接口和 ToolRegistry 完全兼容，so that Agent 零改动即可切换。
6. As a 开发者，I want circuit_breake 和 rate_limit 的接口预留好，so that 以后加熔断限流不用改 ToolRuntime 的对外 API。
7. As a 用户，I want 工具调用日志按时间倒序排列，so that 我能最先看到最近的调用。
8. As a 用户，I want 失败的调用在日志中明显标红，so that 我能一眼看到问题。

## Implementation Decisions

### 架构：渐进包裹

```
ToolRuntime (新增)          ← 对 Agent 暴露 register / dispatch / get_tool_definitions / needs_approval
  ├─ ToolRegistry (现有)     ← 保持注册/调度核心，不受影响
  ├─ ToolTraceStore          ← 异步写入 tool_runs 表
  ├─ sandbox (asyncio.wait_for)
  ├─ circuit_break (预留)
  └─ rate_limit (预留)
```

`ToolRuntime.__init__` 接收 feature flags，默认 tracing + sandbox 开启，circuit_break + rate_limit 关闭。

### D1：trace_run — 工具调用追踪

**ToolRun 模型**：

| 列 | 类型 | 说明 |
|---|------|------|
| `id` | String(36) PK | UUID |
| `call_id` | String(36) | 每次调用的唯一 ID |
| `tool_name` | String(100) | 工具名 |
| `arguments` | JSON | 参数（截断至 2000 字符） |
| `result_summary` | String(500) | 结果摘要（截断） |
| `elapsed_ms` | Integer | 耗时毫秒 |
| `success` | Boolean | 成功 / 失败 |
| `error_message` | String(500), nullable | 失败信息 |
| `conversation_id` | String(36), nullable | 关联会话 |
| `created_at` | DateTime | 时间戳 |

**dispatch 拦截逻辑**：在调用前后打点 → 算耗时 → 判断成功/失败 → 异步写入 `tool_runs`。

**前端展示**：设置面板新增"工具调用日志"标签页，表格展示工具名/参数/耗时/成功✅失败❌/时间，按时间倒序，支持按工具名筛选。参数列过长时截断 + hover 展开。

### D2：sandbox — 超时控制

- `register()` 新增可选参数 `sandbox_config: dict | None`
- `sandbox_config` 包含 `timeout_sec: float`
- `dispatch()` 中用 `asyncio.wait_for(handler(**args), timeout=timeout_sec)` 实现超时
- 超时 → `success=False`，`error_message="Timeout after {N}s"`
- 默认超时：文件读写 10s、网络调用 30s、屏幕截取 15s、其他 10s
- `main.py` 中 `register_tools()` 不变，由 `ToolRuntime._default_timeout(tool_name)` 提供默认值
- 注册时显式传 `sandbox_config` 可以覆盖默认值

### D3：预留扩展开关

```python
class ToolRuntime:
    def __init__(self, registry=None, *,
        enable_tracing: bool = True,
        enable_sandbox: bool = True,
        enable_circuit_breaker: bool = False,  # 预留
        enable_rate_limit: bool = False,        # 预留
        circuit_threshold: int = 5,            # 预留
        rate_limit_per_min: int = 30,          # 预留
    ):
```

- `enable_tracing=False` → dispatch 不写 trace（测试环境用）
- `enable_sandbox=False` → dispatch 不加 `wait_for` 包裹
- 其余参数只存储、不使用，接口占位

### API 合约

**GET /api/tool-runs**

```
Query params:
  - tool_name: str | None   → 按工具名筛选
  - success: bool | None    → 只查成功/失败
  - limit: int (default 50) → 返回条数
  - offset: int (default 0) → 分页偏移

Response:
{
  "total": 150,
  "runs": [
    {
      "id": "uuid",
      "call_id": "uuid",
      "tool_name": "research",
      "arguments": {"query": "Python 3.14"},
      "result_summary": "Python 3.14 released...",
      "elapsed_ms": 3200,
      "success": true,
      "error_message": null,
      "conversation_id": "uuid",
      "created_at": "2026-08-04T10:30:00"
    },
    ...
  ]
}
```

### Agent 兼容性

`Agent.__init__` 的 `tool_registry` 参数类型标注从 `ToolRegistry | None` 改为接受 `ToolRuntime`（接口兼容，`ToolRuntime` 暴露相同四个方法）。不改 Agent 的 dispatch 逻辑。

## Testing Decisions

### 测试原则

- 只测外部行为，不测实现细节。`ToolRuntime` 对 `ToolRegistry` 的代理不算行为——trace 是否写入、超时是否触发才是。
- 使用已有缝：隔离 `ToolRegistry` 注入、内存 SQLite、`FakeLLMService`。

### 测试缝和对应测试

| 缝 | 测试内容 | 参照 |
|---|---|---|
| ToolRuntime + 隔离 ToolRegistry（无 DB/无 LLM/无 HTTP） | dispatch 写 trace、超时 kill、成功/失败标记 | `test_agent_tools.py` 的 fake_registry |
| 内存 SQLite + TestClient | `GET /api/tool-runs` 筛选/分页/空结果 | `test_compact.py` 的 compact_client |
| Agent + ToolRuntime 注入 | full pipeline 中 trace 被正确记录 | `test_agent_tools.py` 的 `TestAgentRunWithInjectedRegistry` |
| Pure async（无 DB/无 HTTP） | 超时触发、超时不写 trace 的边界条件 | `test_relative_dates.py` 的 pure func |

### 测试文件

新建 `backend/tests/test_tool_runtime.py`，包含以上四组测试。

## Out of Scope

- **前端"工具调用日志"UI** — 本 spec 只覆盖后端，前端 UI 作为单独的 follow-up ticket。
- **circuit_break 实现** — 本 spec 只预留接口（feature flag + 占位方法），不实现熔断逻辑（打开/半开/关闭状态机）。
- **rate_limit 实现** — 本 spec 只预留接口，不实现限流（令牌桶/滑动窗口）。
- **trace 数据的过期清理** — 暂不处理，`tool_runs` 表会持续增长。后续可加 TTL 清理 cron。
- **MCP 工具的 sandbox 配置** — MCP 工具注册时不传 `sandbox_config`，使用默认超时 30s。不单独为每个 MCP 工具配超时。

## Further Notes

### 改动清单

| 文件 | 改动 |
|------|------|
| `backend/core/tool_runtime.py` | **新建** — `ToolRuntime` 类（~150 行） |
| `backend/models/tool_run.py` | **新建** — `ToolRun` 模型 |
| `backend/services/tool_trace_store.py` | **新建** — 异步写入 `tool_runs` |
| `backend/database.py` | `init_db()` 导入 `tool_run` 以建表 |
| `backend/api/tool_logs.py` | **新建** — `GET /api/tool-runs` 端点 |
| `backend/main.py` | `register_tools()` 改为注册到 `ToolRuntime`；Agent 注入 `tool_runtime`；注册 API 路由 |
| `backend/core/agent.py` | `tool_registry` 参数类型标注改为接受 `ToolRuntime`（接口兼容，不改逻辑） |
| `backend/eval/runner.py` | 若用到 `ToolRegistry` 需更新为 `ToolRuntime`（或保持兼容） |
| `backend/tests/test_tool_runtime.py` | **新建** |

### 验证命令

```bash
cd backend && python -m pytest tests/test_tool_runtime.py -v
```

### 运行方式

```bash
# 查看追踪日志
curl "http://localhost:8711/api/tool-runs?tool_name=research&limit=50"
```
