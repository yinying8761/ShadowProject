# Tickets — MCP Tool Server 集成

基于 spec: `docs/specs/mcp-integration.md`

---

## #1 — MCP Manager 核心 + 接入

**What to build:** 新增 `McpManager` 模块，读取 `mcp_servers.json` 配置文件，通过 MCP SDK 连接外部 tool server（支持 stdio 和 SSE 两种传输），自动发现远端工具并注册到 `ToolRegistry`。Agent 可以通过 `mcp__<server>__<tool>` 前缀工具调用外部 MCP server。启动时初始化，关闭时断开。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] `backend/services/mcp_manager.py` — McpManager 类：配置解析、stdio_client/sse_client 连接、initialize + list_tools 发现远端工具、包装 handler（closure 内通过 session.call_tool 转发）、disconnect_all 清理
- [ ] 工具命名规则：`mcp__{server_name}__{tool_name}`，描述前缀 `[MCP:{server_name}]`
- [ ] MCP 工具默认 `require_approval=True`
- [ ] MCP 结果格式化：提取 `CallToolResult.content` 中 `TextContent.text` 拼接返回，`isError=True` 时返回 `{"error": ...}` JSON
- [ ] `backend/config.py` — 添加 `mcp_config_path` 配置项（默认空，使用 `data/mcp_servers.json`）
- [ ] `backend/main.py` — lifespan startup 中创建 McpManager 并 connect_all；shutdown 中 disconnect_all
- [ ] `data/mcp_servers.example.json` — 带注释的配置模板（至少含一个 stdio 示例和一个 SSE 示例）
- [ ] 单个 server 连接失败不阻塞其他 server 或原生工具启动
- [ ] 启动日志打印连接结果：`[Startup] MCP: N connected, M failed, T tools`
- [ ] 配置一个真实 MCP server（如 `@anthropic-ai/mcp-server-filesystem`），对话中让 Agent 调用 MCP 工具成功

---

## #2 — 测试 + 容错完善

**What to build:** McpManager 的完整测试覆盖，以及配置解析、连接失败时的容错边界验证。确保无配置文件时优雅降级、非法 JSON 时清晰报错、断网时原生工具不受影响。

**Blocked by:** #1 — MCP Manager 核心 + 接入

**Status:** ready-for-agent

- [ ] `backend/tests/test_mcp_manager.py` — 至少 8 个测试用例
- [ ] `test_connect_all_no_config` — 配置文件不存在时 connected=0，不抛异常
- [ ] `test_connect_all_bad_json` — 文件内容非法时打印错误，connected=0
- [ ] `test_connect_all_empty_servers` — `{"servers": []}` 时 connected=0
- [ ] `test_tool_name_generation` — `_make_tool_name("github", "create_issue")` → `"mcp__github__create_issue"`
- [ ] `test_register_mcp_tools` — mock `list_tools` 返回的 Tool，验证注册到 registry 的工具名/描述/参数正确
- [ ] `test_dispatch_mcp_tool` — mock `session.call_tool` 返回 TextContent，验证 dispatch 结果字符串
- [ ] `test_dispatch_mcp_tool_error` — mock `session.call_tool` 返回 `isError=True`，验证返回 JSON error
- [ ] `test_disconnect_all_cleans_up` — disconnect 后 registry 中不再有 `mcp__` 前缀工具
- [ ] 集成验证：无配置文件启动后端，日志显示 `[Startup] MCP: 0 connected, 0 failed, 0 tools`
- [ ] 集成验证：配置不存在 SSE URL，原生工具（如 read_file）仍正常可用
