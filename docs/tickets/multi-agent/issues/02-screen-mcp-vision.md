# 02 — see_screen 接入 MCP Vision

**What to build:** 截图逻辑保留不动（mss + Pillow + dHash 去重），视觉识别从 `vision_service` 切换为 MCP vision tool。启动时通过 McpManager 连接 DeepSeek_vision_mcp（stdio, GLM-4V Flash 免费），`see_screen()` 优先调 `mcp__vision__analyze_image`，MCP 不可用时 fallback 到旧 `vision_service`。

**Blocked by:** None — 和 01 完全独立。

**Status:** ready-for-agent

- [ ] `see_screen()` 改造：截图不变 → 将图片序列化为 base64 → 通过 ToolRuntime.dispatch 调 `mcp__vision__analyze_image`
- [ ] MCP vision tool 不可用时（未配置/连接失败）自动 fallback 到旧的 `vision_service.describe_image()`
- [ ] `mcp_servers.json` 新增 vision server 配置模板（DeepSeek_vision_mcp, stdio, GLM-4V Flash）
- [ ] `.env` 中 `VISION_API_KEY` / `VISION_PROVIDER` / `VISION_BASE_URL` 改为可选——MCP 方案优先
- [ ] MCP vision server 连接成功后，9 个 `mcp__vision__*` 工具在 ToolRuntime 中可见
- [ ] `config.py` 中 `vision_enabled()` 检查逻辑更新：MCP 可用时返回 True
- [ ] 测试 Seam 2：假 MCP vision server 连接 → 验证 `mcp__vision__analyze_image` 工具注册成功 → 模拟调用返回描述文本
- [ ] 测试：MCP vision 不可用时的 fallback 路径（调旧 vision_service）
- [ ] 测试：dHash 指纹去重在新路径下仍然生效
