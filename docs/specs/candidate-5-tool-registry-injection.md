# Spec: 将 ToolRegistry 改为可注入，而非模块级单例

**状态:** `ready-for-agent`
**日期:** 2026-07-22
**来源:** `/improve-codebase-architecture` 候选方案 #5

---

## Problem Statement

`backend/core/tool_registry.py` 在模块加载时创建 `tool_registry = ToolRegistry()` 作为全局单例，`main.py` 在启动时通过 `register_tools()` 对其进行变更。`Agent` 通过 `from core.tool_registry import tool_registry` 直接导入并使用它。

这造成了两个问题：

1. **不可测试。** 每个测试共享同一个全局注册表。测试无法注册假工具处理器而不影响其他测试。`Agent` 与真实工具实现紧密耦合——测试 Agent 的 LLM 循环时不得不真正执行 `see_screen`、`write_file` 等副作用的工具。
2. **隐式依赖。** 阅读 `Agent` 代码时，无法从构造函数看出它依赖工具注册表。你必须在方法体内追踪 `tool_registry.get_tool_definitions()` / `needs_approval()` / `dispatch()` 调用才能发现这个依赖。

删除测试：如果删除全局 `tool_registry` 单例，复杂性会集中到 `Agent.__init__` 的签名中（作为显式参数接收），以及 `main.py` 的启动组装流程中。这是好事——依赖被显式化了。

## Solution

给 `Agent` 的构造函数添加可选的 `tool_registry` 参数，与已有的 `llm_service` 注入模式一致。同时给 `register_tools()` 添加可选的 `registry` 参数，让测试可以创建独立注册表并注册假工具。模块级单例保留作为默认回退，确保向后兼容。

### 变更范围

| 文件 | 变更 |
|---|---|
| `backend/core/agent.py` | `__init__` 新增 `tool_registry` 可选参数；内部所有引用切换到 `self.tool_registry` |
| `backend/core/tool_registry.py` | 无变更——API 已足够 |
| `backend/main.py` | `register_tools()` 新增可选 `registry` 参数；返回注册表实例 |
| `backend/api/config.py` | 添加模块级 setter，允许从外部注入注册表（替代直接导入单例） |
| `backend/tests/` | 新增 Agent 测试，使用假注册表 |

## User Stories

1. 作为测试者，我希望能够创建一个独立的 `ToolRegistry` 实例，注册假工具，并将其注入到 `Agent` 中，这样我可以测试 Agent 的 LLM 循环而不触发真实的副作用（如 `write_file`、`see_screen`）。
2. 作为测试者，我希望两个并行的 Agent 测试可以使用不同的假注册表而互不干扰。
3. 作为开发者，我希望从 `Agent.__init__` 的签名就能看到它依赖工具注册表，而不需要追踪方法体内的 import。
4. 作为开发者，我希望 `Agent` 的工具依赖是可替换的——例如在问候模式下注入一个空注册表，从而在类型层面保证不会触发工具调用。
5. 作为开发者，我希望 `register_tools()` 可以接受一个注册表参数，这样在测试夹具中可以复用注册逻辑但隔离副作用。
6. 作为开发者，我希望现有的生产代码（`main.py`、`chat.py`、`config.py`）无需大规模重构即可继续工作——模块级单例作为默认回退。

## Implementation Decisions

### 决策 1：Agent 构造函数注入模式

沿用已有的 `llm_service` 注入模式：可选参数，默认回退到模块级单例。

```python
class Agent:
    def __init__(
        self,
        llm_service: LLMService | None = None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.llm_service = llm_service or LLMService()
        self.tool_registry = tool_registry or _default_tool_registry()
        ...
```

`_default_tool_registry()` 是一个模块级私有函数，从 `core.tool_registry` 导入单例。Agent 内部所有 `tool_registry.xxx()` 调用改为 `self.tool_registry.xxx()`。

涉及的内部调用点（共 3 处）：
- `get_tool_definitions()` — 在 `run()` 的聊天模式中获取工具列表
- `needs_approval()` — 在 `_execute_tools_with_approval()` 中判断是否需要审批
- `dispatch()` — 在 `_execute_tools_with_approval()` 中执行工具

### 决策 2：register_tools() 参数化

`register_tools()` 接受可选的 `registry` 参数，并返回注册表实例：

```python
def register_tools(registry: ToolRegistry | None = None) -> ToolRegistry:
    if registry is None:
        from core.tool_registry import tool_registry as registry
    # ... 所有 tool_registry.register(...) 改为 registry.register(...)
    return registry
```

生产代码不变（无参数调用时行为与现在完全相同），测试可以传入独立实例。

### 决策 3：config.py 的 /tools 端点

`config.py` 第 8 行导入模块级 `tool_registry` 用于 `/api/tools` GET 端点（返回工具定义列表）。对此有两个选项：

- **选项 A（保守）：** 保持不变。`config.py` 的用途是只读查询，不涉及可测试性的核心问题。等未来全面 DI 化时再处理。
- **选项 B（彻底）：** 添加 `set_tool_registry()` 模块级函数，`main.py` 在创建注册表后调用它。`config.py` 通过懒加载访问。

**选择选项 A。** `config.py` 只调用 `get_tool_definitions()`（纯读取），不涉及测试隔离的核心痛点。Agent 是可测试性的瓶颈，先解决它。如果在实现过程中发现选项 B 的成本很低，可以顺带完成。

### 决策 4：chat.py 的模块级 agent 单例

`chat.py` 第 17 行 `agent = Agent()` 在模块加载时创建。注入后，这个单例会通过 `_default_tool_registry()` 获取全局注册表——行为不变。如果未来需要注入特定注册表，调用者可以使用 `Agent(tool_registry=my_registry)`。

### 决策 5：不做的事

- **不删除模块级单例。** 保留 `tool_registry = ToolRegistry()` 在 `tool_registry.py:61`。它继续作为默认回退，保证向后兼容。
- **不引入 DI 框架。** 不用 `dependency-injector` 等库。手动注入足够。
- **不改 `Agent` 以外的模块。** `chat.py`、`greeting_orchestrator.py` 等不在此次范围内接受注入。

## Testing Decisions

### 测试哲学

只测试外部行为：给定消息和假工具 → Agent 产出预期的事件序列。用假注册表替换真实工具，不测试实现细节（如内部方法调用次数）。

### 测试 Agent 的工具交互

- **假 ToolRegistry**：测试夹具创建一个独立的 `ToolRegistry` 实例，注册一个 `fake_echo` 工具（返回固定字符串）和一个 `fake_approval` 工具（标记为 `require_approval=True`）
- **假 LLMService**：返回预设的 token 序列 + tool_use 事件
- **假 approval_callback**：返回预设的批准/拒绝结果

测试用例：
- Agent 收到 tool_use 事件 → 调用注册表中的工具 → yield tool_result 事件
- 工具需要审批且被拒绝 → yield denied=True 的 tool_result
- 工具需要审批且被批准 → 正常执行
- 注册表中找不到工具 → yield is_error=True 的 tool_result
- 工具处理器抛出异常 → yield is_error=True 的 tool_result
- 注入空注册表 → `get_tool_definitions()` 返回空列表，LLM 调用的 tools 参数为 None

### 测试 register_tools()

- 传入独立 registry 实例 → 注册 10 个工具 → 验证 `get_tool_definitions()` 返回 10 个定义
- 不传参数 → 使用模块级单例（集成测试，可选）

### 已有测试先例

`backend/tests/services/formatters/test_anthropic_formatter.py` 使用 `pytest` + fixture 模式。Agent 测试沿用此约定。

### 新增测试文件

- `backend/tests/core/test_agent_tools.py` — Agent 工具交互的单元测试

## Out of Scope

- **删除模块级 `tool_registry` 单例。** 保留作为默认回退，确保 `config.py` 和现有代码不中断。
- **Agent 的 `llm_service` 注入已有，不在此次范围内修改。**
- **`config.py` 的 `/tools` 端点重构。** 保持现状。
- **`chat.py` 中 `agent = Agent()` 的模块级创建。** 保持现状——它会通过默认参数获取全局注册表。
- **完整的 DI 容器或依赖注入框架。** 这只是对手动注入模式的单点应用。
- **候选方案 #1-#4 和 #6。** 各自独立。

## Further Notes

- 这是 `/improve-codebase-architecture` 报告中 6 个候选方案中的第 5 个。候选方案 #1-#4 的提取工作（chat.py 上帝模块拆分、Agent 方法统一、chatStore 拆分、MemoryService 拆分）在此之前可能已经部分完成，但候选方案 #5 不依赖它们——它只触及 `Agent` 和 `main.py` 的接触面。
- `Agent.__init__` 已有 `llm_service` 可选参数作为注入先例——`tool_registry` 的注入遵循相同模式，代码审查者应该感到熟悉。
- 如果后续要扩展（例如按角色/模式注入不同工具集），这个接缝直接支持。例如 `Agent(tool_registry=greeting_tools)` 可以仅暴露只读工具给问候模式。
- 建议执行顺序：先写 Agent 工具交互测试（此时测试会失败，因为 Agent 内部硬编码导入了全局注册表），然后重构 Agent 让其通过 `self.tool_registry` 访问，测试变绿。这是验证重构正确性的安全网。
