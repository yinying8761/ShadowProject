# 01 — ToolRuntime 核心 + 追踪落表（D1 + D3）

**What to build:** 新建 `ToolRuntime` 类包裹现有 `ToolRegistry`，在 `dispatch()` 前后拦截，自动记录每次工具调用的 trace 数据到 SQLite `tool_runs` 表。同时预留 D3 扩展开关。对 `Agent` 透明——接口兼容，Agent 只改类型标注。

**Blocked by:** None — 可直接开始。

**Status:** ready-for-agent

- [ ] `ToolRun` 模型：UUID PK、call_id、tool_name、arguments(JSON, ≤2000chars)、result_summary(≤500chars)、elapsed_ms、success、error_message(nullable)、conversation_id(nullable)、created_at
- [ ] `ToolTraceStore` 服务：异步写入 `tool_runs`，接口 `save(run: ToolRun) -> None`
- [ ] `ToolRuntime` 类：包裹 `ToolRegistry`，暴露 register / unregister / dispatch / get_tool_definitions / needs_approval 五个方法，接口签名和 ToolRegistry 一致
- [ ] `ToolRuntime.__init__` 接收 feature flags：`enable_tracing`(默认True)、`enable_sandbox`(默认True)、`enable_circuit_breaker`(默认False)、`enable_rate_limit`(默认False)、`circuit_threshold`、`rate_limit_per_min`
- [ ] `ToolRuntime.dispatch()` 拦截：调用前打点 → 执行 → 算耗时 → 判成功/失败 → `enable_tracing=True` 时异步写 `tool_runs`
- [ ] `enable_tracing=False` 时 dispatch 不写 trace（测试环境用）
- [ ] `database.py` 导入 `tool_run` 模型以建表
- [ ] `main.py` 中 `register_tools()` 改为接受 `ToolRuntime`（或 duck-typing 兼容），Agent 注入 `tool_runtime` 而非裸 `tool_registry`
- [ ] `Agent.__init__` 参数类型标注从 `ToolRegistry | None` 改为接受 `ToolRuntime`，不改 dispatch 逻辑
- [ ] `eval/runner.py` 中 `ToolRegistry()` 替换为 `ToolRuntime(registry=ToolRegistry(), enable_tracing=False, enable_sandbox=False)`
- [ ] 测试 Seam 1：ToolRuntime 包裹隔离 ToolRegistry，dispatch 假工具 → 验证 trace 写入（成功/失败/耗时）
- [ ] 测试 Seam 3：Agent + ToolRuntime 注入 → FakeLLMService 发 tool_use → 验证 trace 被记录
