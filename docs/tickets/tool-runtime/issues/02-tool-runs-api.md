# 02 — 工具调用日志 API

**What to build:** 新增 `GET /api/tool-runs` 端点，支持按工具名、成功/失败状态筛选，分页返回，按时间倒序排列。前端设置面板后续可直接对接此 API 展示工具调用日志。

**Blocked by:** 01 — 需 ToolRun 模型 + ToolTraceStore 已有数据。

**Status:** ready-for-agent

- [ ] `GET /api/tool-runs` 端点，query params：`tool_name`(可选)、`success`(可选, true/false)、`limit`(默认50)、`offset`(默认0)
- [ ] Response 格式：`{"total": N, "runs": [...]}`，每条 run 含所有字段（id, call_id, tool_name, arguments, result_summary, elapsed_ms, success, error_message, conversation_id, created_at）
- [ ] 结果按 `created_at` 倒序排列
- [ ] `tool_name` 筛选支持精确匹配（空或未传则不过滤）
- [ ] `success` 筛选值 `"true"` / `"false"` 字符串转 bool，无效值忽略（不过滤）
- [ ] `main.py` 注册 tool_logs router
- [ ] 测试 Seam 2：内存 SQLite + TestClient → 预写几条 trace → 验证筛选/分页/倒序/空结果
