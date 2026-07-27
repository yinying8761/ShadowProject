# TICKETS — ToolRegistry 注入化

**父 Spec:** `docs/specs/candidate-5-tool-registry-injection.md`
**日期:** 2026-07-22
**状态:** ready-for-agent

---

## 依赖关系

```
#1  →  #2
```

Ticket 1 完成 Agent 和 register_tools 的重构，Ticket 2 在此基础上编写测试验证注入接缝有效。

---

## #1 — Agent 接受 ToolRegistry 注入 + register_tools 参数化

**交付内容：**

`Agent.__init__` 新增可选 `tool_registry` 参数（沿用 `llm_service` 注入模式，默认回退到模块级单例）。Agent 内部所有 `tool_registry.xxx()` 调用改为 `self.tool_registry.xxx()`。`register_tools()` 新增可选 `registry` 参数并返回注册表实例。生产行为完全不变。

**阻塞于：** 无 — 可立即开始

**状态：** ready-for-agent

- [ ] `Agent.__init__` 新增 `tool_registry: ToolRegistry | None = None` 参数
- [ ] 添加 `_default_tool_registry()` 模块级私有函数，返回 `core.tool_registry.tool_registry` 单例
- [ ] `self.tool_registry = tool_registry or _default_tool_registry()`
- [ ] `run()` 中 `tool_registry.get_tool_definitions()` → `self.tool_registry.get_tool_definitions()`（第 185 行）
- [ ] `_execute_tools_with_approval()` 中 `tool_registry.needs_approval()` → `self.tool_registry.needs_approval()`（第 287 行）
- [ ] `_execute_tools_with_approval()` 中 `tool_registry.dispatch()` → `self.tool_registry.dispatch()`（第 309 行）
- [ ] `register_tools()` 新增可选 `registry: ToolRegistry | None = None` 参数
- [ ] 函数体内部所有 `tool_registry.register(...)` 改为通过参数获取的 registry
- [ ] `register_tools()` 返回 registry 实例
- [ ] `main.py` 中 `register_tools()` 调用保持不变（无参数调用时行为完全不变）
- [ ] 手动验证：启动应用 → 发送消息触发工具调用（如 `/search` 或 `see_screen`）→ 工具正常执行 → 结果正常返回

---

## #2 — Agent 工具交互单元测试

**交付内容：**

使用假 `ToolRegistry` 实例和假 `LLMService` 测试 Agent 的工具交互逻辑：工具调用、审批流程、错误处理。验证注入接缝有效。

**阻塞于：** #1

**状态：** ready-for-agent

- [ ] 新建 `backend/tests/core/test_agent_tools.py`
- [ ] 编写 `FakeLLMService`：接受预设的 token/tool_use 事件序列，`stream_chat()` 按序 yield
- [ ] 编写 fixture `fake_registry`：独立 `ToolRegistry` 实例，注册 `fake_echo`（返回固定字符串）、`fake_approval`（require_approval=True）、`fake_failing`（抛出异常）
- [ ] 测试：Agent 收到 tool_use 事件 → dispatch 到注册表 → yield 正确 tool_result 事件
- [ ] 测试：工具需要审批且 `approval_callback` 返回 False → yield denied=True 的 tool_result
- [ ] 测试：工具需要审批且 `approval_callback` 返回 True → 正常执行
- [ ] 测试：注册表中找不到工具 → dispatch 返回 JSON error，不崩溃
- [ ] 测试：工具处理器抛出异常 → yield is_error=True 的 tool_result
- [ ] 测试：注入空注册表 → `get_tool_definitions()` 返回空列表，LLM 调用的 tools 参数为空
- [ ] 测试：不注入注册表时 → Agent 使用默认模块级单例（冒烟测试，验证默认行为不变）
- [ ] `pytest backend/tests/core/test_agent_tools.py -v` 全部通过

