# MCP Tool Server 集成 — Spec

> 状态：草稿 | 日期：2026-07-29

## 1. 问题

### 当前状态

ShadowProject 有 10 个硬编码工具（`read_file`, `write_file`, `search_memory` 等），全部在 `backend/main.py` 的 `register_tools()` 中手动注册。工具逻辑和注册代码耦合在一起。

### 痛点

- 每加一个新工具就要写 Python 代码 + 改 `main.py` + 重启后端
- 无法复用社区已有的 MCP server 生态（filesystem、github、postgres、brave-search 等数百个）
- `requirements.txt` 中已声明 `mcp>=1.6.0` 依赖但无实际集成
- 项目面试竞争力评估中将 MCP 列为高优先级缺失项

### 目标

ShadowProject 作为 **MCP Client**，通过配置文件就能连接任意 MCP Tool Server，Agent 自动获得远端工具能力。用户只需编辑 JSON 配置，无需写代码。

---

## 2. 架构概览

```
mcp_servers.json               ← 用户配置（声明有哪些 MCP server）
       │
       ▼
McpManager                     ← 新增模块，管理所有 MCP server 连接
  ├─ 读取配置
  ├─ 按 transport 类型建立持久连接（stdio / SSE）
  ├─ 通过 MCP SDK 发现远端工具（list_tools）
  ├─ 将远端工具包装为 ToolRegistry handler
  │   命名规则: mcp__<server>__<tool>
  └─ 转发工具调用到远端（call_tool）
       │
       ▼
ToolRegistry（已有，不变）
  ├─ 原生工具: read_file, write_file, ...
  └─ MCP 工具: mcp__filesystem__read, mcp__github__create_issue, ...
       │
       ▼
Agent.run()（已有，不变）
  └─ get_tool_definitions() → 原生 + MCP 工具
  └─ dispatch() → 本地 handler 或 MCP 远端调用
```

**核心设计：MCP 工具对 Agent 和 LLM 完全是透明的 — 它们和原生工具长得一样。**

---

## 3. 配置文件设计

### 路径

```
{data_dir}/mcp_servers.json
```

默认 `data/mcp_servers.json`，可通过环境变量 `MCP_CONFIG_PATH` 覆盖。

### Schema

```jsonc
{
  "servers": [
    // ── stdio 传输：启动本地进程通信 ──
    {
      "name": "filesystem",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@anthropic-ai/mcp-server-filesystem", "F:/"],
      "env": {}
    },

    // ── SSE 传输：连接远程 HTTP 服务 ──
    {
      "name": "remote_tools",
      "transport": "sse",
      "url": "http://localhost:3001/sse",
      "headers": {}
    },

    // ── Python MCP server ──
    {
      "name": "python_tools",
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "my_mcp_server"],
      "cwd": "./my_mcp_project"
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | 唯一标识，用于工具命名前缀，只允许 `[a-z0-9_]` |
| `transport` | string | 是 | `"stdio"` 启动子进程，`"sse"` 连接 HTTP/SSE 端点 |
| **stdio 专属** | | | |
| `command` | string | 是 | 可执行文件，如 `npx`, `python`, `node` |
| `args` | string[] | 否 | 命令行参数 |
| `env` | dict | 否 | 额外环境变量（如 `GITHUB_TOKEN`） |
| `cwd` | string | 否 | 工作目录 |
| **sse 专属** | | | |
| `url` | string | 是 | MCP SSE 端点 URL |
| `headers` | dict | 否 | 额外 HTTP 头（如 `Authorization`） |

---

## 4. 核心模块：`McpManager`

### 位置

`backend/services/mcp_manager.py`

### 公开接口

```python
class McpManager:
    """管理所有 MCP server 连接，将远端工具注册到 ToolRegistry"""

    def __init__(self, registry: ToolRegistry):
        """绑定目标 ToolRegistry"""

    async def connect_all(self, config_path: str | Path) -> dict[str, int]:
        """
        读取配置文件，连接所有 server，注册工具。

        返回 {"connected": N, "failed": M, "tools": T}

        - 某个 server 连接失败不影响其他 server
        - 已注册的工具名称记录在 self._mcp_tool_names 中用于清理
        """

    async def disconnect_all(self):
        """断开所有连接，从 ToolRegistry 中移除 MCP 工具"""

    @property
    def connected_servers(self) -> list[str]:
        """当前已连接的 server 名称列表"""
```

### 连接生命周期（单 server）

```
connect_server(server_cfg)
  │
  ├─ 建立 transport（stdio_client 或 sse_client）
  ├─ 创建 ClientSession
  ├─ await session.initialize()
  ├─ result = await session.list_tools()
  │    └─ result.tools: list[Tool]
  │         ├─ tool.name         → 原始工具名
  │         ├─ tool.description  → 工具描述
  │         └─ tool.inputSchema  → JSON Schema（dict）
  │
  ├─ 对每个 tool：注册到 ToolRegistry
  │    name: f"mcp__{server_name}__{tool.name}"
  │    description: f"[MCP:{server_name}] {tool.description}"
  │    parameters: tool.inputSchema
  │    handler: _make_mcp_handler(session, tool.name)
  │    require_approval: True（MCP 工具默认需要用户确认）
  │
  └─ 维护 session 和 transport context 用于后续清理
```

### 工具调用转发

```python
async def handler(**kwargs):
    result = await session.call_tool(original_tool_name, kwargs)
    return _format_mcp_result(result)
```

**结果格式化**（`_format_mcp_result`）：`CallToolResult.content` 是 `list[TextContent | ImageContent | EmbeddedResource]`。遍历 content 列表，提取所有 `TextContent.text` 字段，用换行拼接为字符串返回。如果是 `isError=True`，返回 `json.dumps({"error": text})` 格式（与现有工具错误处理一致）。

### 错误处理

| 场景 | 行为 |
|---|---|
| 配置文件不存在 | 打印警告，返回 `{"connected": 0}`，不阻塞启动 |
| 配置 JSON 解析失败 | 打印错误 + 文件路径 + 行号，跳过 |
| 单个 server 连接失败 | 打印 `[McpManager] server 'xxx' connection failed: ...`，继续连接下一个 |
| 连接后 list_tools 失败 | 断开该 server，打印错误，继续 |
| 运行时 call_tool 失败 | 返回 `{"error": "..."}` 字符串（ToolRegistry.dispatch 已有 catch） |
| server 进程崩溃 | 下次调用时返回连接已断开的错误信息 |

### 安全策略

- MCP 工具**默认 require_approval=True**（用户需在弹窗中确认）
- 后续可通过配置文件 `"auto_approve": ["mcp__filesystem__read"]` 白名单免确认
- 工具名中的 `mcp__` 前缀使来源在审批弹窗中可辨识

---

## 5. 现有代码改动

### `backend/config.py` — 添加 1 个配置项

```python
# ---- MCP ----
mcp_config_path: str = ""  # 空=使用默认 data/mcp_servers.json
```

### `backend/main.py` — lifespan 中 3 处改动

```python
# 1) 导入
from services.mcp_manager import McpManager
from core.tool_registry import tool_registry

# 2) lifespan startup（init_db 之后）
mcp_manager = McpManager(tool_registry)
mcp_config = settings.mcp_config_path or str(data_dir / "mcp_servers.json")
result = await mcp_manager.connect_all(mcp_config)
print(f"[Startup] MCP: {result['connected']} connected, "
      f"{result['failed']} failed, {result['tools']} tools")

# 3) lifespan shutdown（yield 之后）
await mcp_manager.disconnect_all()
```

---

## 6. 文件清单

### 新建

| 文件 | 说明 |
|---|---|
| `backend/services/mcp_manager.py` | McpManager 类，~150 行 |
| `data/mcp_servers.example.json` | 带注释的配置模板 |
| `backend/tests/test_mcp_manager.py` | 测试（约 6-8 个用例） |

### 修改

| 文件 | 改动 |
|---|---|
| `backend/config.py` | +1 行配置字段 |
| `backend/main.py` | +8 行 lifespan 集成 |

### 不改的文件

- `backend/core/tool_registry.py` — 接口够用，无需改动
- `backend/core/agent.py` — 透明，改动对 Agent 不可见
- `backend/services/llm_service.py` — 不变
- 所有现有 `backend/tools/*.py` — 不变

---

## 7. 测试计划

### 单元测试 (`test_mcp_manager.py`)

| 用例 | 验证点 |
|---|---|
| `test_connect_all_no_config` | 配置文件不存在 → connected=0，不抛异常 |
| `test_connect_all_bad_json` | 文件内容非法 → 打印错误，connected=0 |
| `test_connect_all_empty_servers` | `{"servers": []}` → connected=0 |
| `test_tool_name_generation` | `_make_tool_name("github", "create_issue")` → `"mcp__github__create_issue"` |
| `test_register_mcp_tools` | mock 了 list_tools 返回的 Tool，验证注册到 registry 的工具名/描述/参数正确 |
| `test_dispatch_mcp_tool` | mock session.call_tool 返回 TextContent，验证 dispatch 结果字符串 |
| `test_dispatch_mcp_tool_error` | mock session.call_tool 返回 isError=True，验证 dispatch 返回 JSON error |
| `test_disconnect_all_cleans_up` | 验证 disconnect 后 registry 中不再有 mcp__ 前缀工具 |

### 集成测试

1. 启动后端，日志显示 `[Startup] MCP: 0 connected, 0 failed, 0 tools`（无配置文件时优雅降级）
2. 配置一个真实 `@anthropic-ai/mcp-server-filesystem`，在对话中让 Agent 读文件验证
3. 配置一个不存在的 SSE URL，验证原生工具（读文件等）不受影响

---

## 8. 后续扩展（不在本次范围）

- **工具审批白名单**：配置中 `"auto_approve"` 字段，免去高频安全工具的弹窗
- **动态热加载**：文件监听 `mcp_servers.json` 变化，无需重启后端即可增删 server
- **前端管理面板**：设置页面中可视化 MCP server 列表 + 开关
- **资源 & 提示词**：MCP 的 `list_resources` / `list_prompts` 集成
- **MCP Server 模式**：ShadowProject 自身暴露为一个 MCP server，供其他 Agent 调用
