# 01 — SubAgent 架构 + SearchAgent + Router 占位

**What to build:** 建立子 Agent 架构——`SubAgent` 基类提供独立 LLM + 工具循环模板，`SearchAgent` 继承它实现多步检索（搜索→选结果→fetch_url→再搜索），替代现在的 `research` 直接函数调用。`RouterAgent` 接口占位，Agent 增加 `_resolve_handler()` 钩子为以后路由接入留好位置。

**Blocked by:** None — 可直接开始。

**Status:** ready-for-agent

- [ ] `SubAgent` 基类：`__init__(llm_service)` + `run(prompt) -> str` + 内置工具循环（最多 N 轮，可配置）
- [ ] `SearchAgent(SubAgent)`：工具集 `fetch_url` + search backends，输入 query，返回 `{"answer": "...", "sources": [...], "confidence": "..."}`
- [ ] `RouterAgent` 占位类：类存在，接口 `run(tool_name, args) -> str` 或 `pass`
- [ ] `Agent._resolve_handler(tool_name)` 钩子：现在直接返回 `self.tool_registry.dispatch`，以后 Router 在这里拦截
- [ ] `main.py`：`research` 工具 handler 改为 `search_agent.run`
- [ ] `search_tools.py`：保留 `_do_search` / `fetch_url` 供 SearchAgent 复用；`research()` 函数保留但可标记 deprecated
- [ ] 测试 Seam 1：`SearchAgent` + `FakeLLMService` → 验证多步检索逻辑和返回格式
- [ ] 测试 Seam 3：`Agent.run()` 触发 `research` → SearchAgent 处理 → 返回正确 tool_result
- [ ] 测试 Seam 4：`RouterAgent` 类可导入，接口签名符合预期
- [ ] 测试：`SubAgent` 基类的工具循环在达到最大轮数后正确退出
