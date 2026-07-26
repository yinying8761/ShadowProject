## 问题描述

当前 `LLMService`（`backend/services/llm_service.py`，215 行）混合了两个关注点：**连接管理**（创建/缓存 OpenAI 和 Anthropic 客户端）和**消息格式转换**（内部消息格式 ↔ 提供商 SDK 格式）。

格式转换逻辑在 3 个方法中重复或分散：

| 转换 | `_stream_openai` | `_stream_anthropic` | `chat_sync` |
|---|---|---|---|
| 系统消息提取 | — | ✅（Anthropic 需要单独 system 参数） | ✅（Anthropic 路径） |
| `tool` 角色 → 提供商格式 | `{role:"tool", tool_call_id}` | `{role:"user", content:[{type:"tool_result"}]}` | — |
| Assistant 含 `tool_calls` 的消息透传 | ✅（保持 `tool_calls` 字段） | 不适用 | — |
| 工具定义 → 提供商格式 | 包装为 `{type:"function", function:{...}}` | 原样透传 | — |
| 流事件 → 内部事件 | Token + 增量工具调用累积与 JSON 解析 | Token + 最终消息工具提取 | N/A |

**核心问题**：添加第三个 LLM 提供商（如 Google Gemini）意味着第三次复制格式转换逻辑——这违反局部性原则（一处修改，一处生效）。

## 解决方案

创建一个 **MessageFormatter 协议**作为干净接缝，将格式转换从 `LLMService` 中提取出来。每个提供商对应一个独立的 Formatter 实现。

### 新模块架构

```
LLMService（保留：客户端创建、流式循环、错误包装）
  │
  ├── OpenAIFormatter    —— 消息角色转换、工具定义包装、流式工具调用累积
  ├── AnthropicFormatter —— 系统消息分离、工具角色 → tool_result 块、最终消息工具提取
  └── (未来) GeminiFormatter —— 新文件，LLMService 无需改动
```

### MessageFormatter 协议接口

```python
class MessageFormatter:
    def format_messages(messages: list[dict]) -> tuple[str | None, list[dict]]
        """将内部消息列表转换为提供商格式。
        返回 (system_prompt, provider_messages)。
        system_prompt 为 None 表示该提供商不需要单独提取 system 消息。"""

    def format_tools(tools: list[dict] | None) -> list[dict] | None
        """将内部工具定义（Anthropic 格式）转换为提供商格式。
        返回 None 表示没有工具。"""

    def parse_token(event) -> str | None
        """从提供商流事件中提取文本 token。
        返回 None 表示不是 token 事件。"""

    def extract_tool_calls(final_message) -> list[dict]
        """从最终响应中提取工具调用列表。
        返回 [{id, name, arguments}, ...]。
        如果没有工具调用则返回空列表。"""
```

### 改造后的 `LLMService.stream_chat` 流程

```python
async def stream_chat(self, messages, tools=None, model=None):
    formatter = self._get_formatter()          # 根据 sdk_type 选择 formatter
    system, provider_msgs = formatter.format_messages(messages)
    provider_tools = formatter.format_tools(tools)

    async for event in provider_stream(provider_msgs, provider_tools):
        token = formatter.parse_token(event)
        if token:
            yield {"type": "token", "content": token}

    for tc in formatter.extract_tool_calls(final_msg):
        yield {"type": "tool_use", "id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]}
```

## 用户故事

1. 作为**后端开发者**，我希望添加新 LLM 提供商时只需新建一个 Formatter 文件，而不修改 LLMService，以便降低引入 bug 的风险。
2. 作为**后端开发者**，我希望调试消息格式问题时只需看对应 Formatter 的实现，而不需要翻阅整个 215 行的 LLMService。
3. 作为**后端开发者**，我希望独立测试每个 Formatter 的格式转换逻辑——给定内部消息，检查输出格式，而无需真正调用 LLM API。
4. 作为**后端开发者**，我希望 `chat_sync`（非流式调用）也复用同一个 Formatter，消除当前在两处（流式和非流式）分别做系统消息提取的重复代码。
5. 作为**项目维护者**，我希望 LLMService 的职责更加单一——只负责「如何连接和流式传输」，而「如何转换格式」由 Formatter 负责。
6. 作为**代码审查者**，我希望消息格式相关的 PR 只改动 Formatter 文件，而连接管理相关的 PR 只改动 LLMService——减少合并冲突和审查范围。

## 实现决策

### 决策 1：提取范围

**仅提取 `LLMService` 中的格式转换逻辑**。`VisionService` 中的图像格式转换虽然模式相似，但其格式差异较大（图像内容块 vs 对话消息），本轮不纳入，避免过度泛化。

### 决策 2：Formatter 的选择时机

`LLMService` 在 `stream_chat` 和 `chat_sync` 调用时通过 `settings.get_sdk_type()` 动态选择 Formatter，而非在构造函数中固化。理由：项目中 `sdk_type` 是全局配置，不会在运行时切换，但「调用时选择」比「初始化时选择」更灵活，也为将来的测试提供更好的注入点。

### 决策 3：`chat_sync` 也使用 Formatter

当前 `chat_sync` 中的 Anthropic 系统消息提取（第 43-54 行）和 OpenAI 消息透传（第 60-67 行）与流式方法中的逻辑重复。统一通过 Formatter 处理。

### 决策 4：Formatter 不负责客户端创建

Formatter 是**纯数据转换层**，不持有任何 API 客户端引用。客户端创建和缓存保留在 `LLMService` 中。这保持 Formatter 的无状态特性，使其易于单元测试。

### 决策 5：文件组织

在 `backend/services/` 下创建子包 `formatters/`：

```
backend/services/
├── llm_service.py          # 精简后的 LLMService（~80 行）
├── formatters/
│   ├── __init__.py         # 导出 + get_formatter(sdk_type) 工厂函数
│   ├── base.py             # MessageFormatter 协议/抽象基类
│   ├── openai_formatter.py # OpenAI 格式转换（~80 行）
│   └── anthropic_formatter.py # Anthropic 格式转换（~60 行）
```

### 决策 6：向后兼容

`LLMService` 的公开接口（`stream_chat`、`chat_sync`）保持签名不变。事件产出格式（`{type: "token"/"tool_use"/"error"}`）保持不变。调用者（`Agent`）无需任何修改。

## 测试决策

### 什么是好的测试

- 只测试外部行为：给定内部消息列表 → Formatter 产出正确的提供商格式消息
- 不测试实现细节：不检查内部字典结构的具体构建过程
- 不测试真实 API 调用：使用假的事件对象模拟流式响应

### 测试范围

| 测试对象 | 测试内容 | 方法 |
|---|---|---|
| `OpenAIFormatter` | `format_messages`（含 tool 角色、assistant tool_calls 透传） | 单元测试，给定消息列表 → 断言输出格式 |
| `OpenAIFormatter` | `format_tools`（内部格式 → OpenAI function-calling 格式） | 单元测试 |
| `OpenAIFormatter` | `parse_token` 从模拟的 OpenAI chunk 提取文本 | 单元测试 |
| `OpenAIFormatter` | `extract_tool_calls` 从模拟的最终消息提取工具调用 | 单元测试 |
| `AnthropicFormatter` | `format_messages`（系统消息分离、tool 角色 → tool_result 块） | 单元测试 |
| `AnthropicFormatter` | `parse_token` 从模拟的 Anthropic event 提取文本 | 单元测试 |
| `AnthropicFormatter` | `extract_tool_calls` 从模拟的最终消息提取工具调用 | 单元测试 |
| `LLMService` | 集成测试：用真实 API 验证流式聊天仍正常工作 | 需要 API key，标记为集成测试 |

### 测试先行参考

项目当前没有现有测试文件。本轮为 Formatter 创建首批测试，测试文件放在 `backend/tests/services/formatters/` 下，使用 `pytest` + `pytest-asyncio`。

## 不在范围内

1. **VisionService 的格式转换提取** — 虽然模式相似，但图像格式差异较大，本轮不做。
2. **第三个提供商（Gemini 等）的实现** — 只建立 MessageFormatter 协议和两个现有提供商的实现。新提供商在后续需求中自然添加。
3. **LLMService 的其他重构** — 不改变客户端缓存策略、错误处理模式或重试逻辑。
4. **Agent 的修改** — `LLMService` 公开接口不变，Agent 无需任何改动。
5. **流式事件类型的变更** — 当前事件类型（token/tool_use/error/done）保持不变。

## 补充说明

- 这是架构审查报告（2026-07-21）中候选方案 #6 的实施 spec。该方案评级为「探索性」——收益明确但优先度低于 #1（拆分 WebSocket 处理器）和 #2（统一 Agent.run）。
- 该项目当前已有 #1（GreetingOrchestrator 提取）和 #2（Agent mode="greeting" 统一）的部分落地，本方案是该重构链的自然延续。
- Formatter 的无状态特性使其成为项目中**最适合单元测试**的模块——建议优先为 Formatter 建立测试基础设施，作为项目测试文化的起点。
