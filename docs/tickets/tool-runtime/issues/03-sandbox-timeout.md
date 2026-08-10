# 03 — 沙箱超时控制（D2）

**What to build:** `ToolRuntime.register()` 新增可选 `sandbox_config` 参数，`dispatch()` 中用 `asyncio.wait_for` 包裹 handler 执行，超时自动 kill。每个工具类别有合理默认超时。

**Blocked by:** 01 — 需 ToolRuntime.register/dispatch 骨架。

**Status:** ready-for-agent

- [ ] `ToolRuntime.register()` 新增可选参数 `sandbox_config: dict | None`，包含 `timeout_sec: float`
- [ ] `ToolRuntime._default_timeout(tool_name: str) -> float`：文件读写 10s、网络(fetch_url/research) 30s、屏幕截取 15s、其他 10s
- [ ] `ToolRuntime.dispatch()` 中：`enable_sandbox=True` 时用 `asyncio.wait_for(handler(**args), timeout=timeout_sec)` 包裹执行；`enable_sandbox=False` 时直接调用 handler
- [ ] 注册时显式传 `sandbox_config` 覆盖默认超时
- [ ] 超时 → `success=False`，`error_message="Timeout after {N}s"`，trace 正常写入 tool_runs（elapsed_ms 约等于超时值）
- [ ] `asyncio.TimeoutError` 被正确捕获，不向上传播
- [ ] 测试 Seam 4：注册慢 handler（`asyncio.sleep(0.5)` + timeout=0.1）→ dispatch → 验证返回 error 且 success=False；验证未超时的正常 handler 不受影响
- [ ] 验证 `enable_sandbox=False` 时不包裹 `wait_for`，慢 handler 不会被 kill
