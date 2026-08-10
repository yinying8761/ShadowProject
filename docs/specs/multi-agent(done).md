# Multi-Agent — SearchAgent + MCP Vision + 预留 Router

> 日期：2026-08-04 | 状态：待实现 | 父迭代：`iteration-plan.md` Workflow C

## Problem Statement

当前单 Agent 架构有两个问题：

1. **research 工具直接用主 LLM 做摘要**：贵、慢、没有多步检索能力。用户问"Python 3.14 的新特性有哪些"，只能搜一次→读摘要→让主 LLM 总结，没法先搜到了再去点开某个链接深入看。

2. **see_screen 工具耦合 vision API**：必须在 `.env` 配 `VISION_API_KEY` / `VISION_PROVIDER` / `VISION_MODEL`，换一个视觉模型就要改配置。只能用一家 API，不能灵活切换。

3. **未来扩展瓶颈**：加一个新的子 Agent（比如代码执行、文件分析）需要改 Agent 核心代码，没有统一的子 Agent 编排入口。

## Solution

三个相互解耦的改动：

### C1：SearchAgent — 独立搜索子 Agent

把 `research` 从"直接函数调用"升级为"独立 LLM + 工具循环的子 Agent"。SearchAgent 有自己的 LLM（可用更便宜的小模型），可以多步检索：搜索→选结果→fetch_url→判断是否需要补充搜索→返回结构化总结。

对外接口不变——仍然注册为 `research` 工具，MainAgent 感觉不到变化。

### C2：see_screen → MCP Vision Server

截图部分（mss + Pillow + dHash 指纹去重）完全不动。视觉识别从 `vision_service.describe_image()` 改为通过 MCP 协议调用 vision server 的工具（如 `mcp__vision__analyze_image`）。

接入 DeepSeek_vision_mcp（Python stdio，GLM-4V Flash 免费模型）。`.env` 中移除 `VISION_API_KEY` 等视觉配置，改在 `mcp_servers.json` 的 vision server `env` block 中配置。

### C3：Router 架构预留

在 Agent 中增加 `_resolve_handler(tool_name)` 钩子——现在直接调 ToolRuntime.dispatch，以后可以在这里接入 Router 统一分派。RouterAgent 接口定义但暂不实现。

## User Stories

1. As a 用户，I want AI 搜索结果更准确，so that 问复杂问题时能得到多步检索后的精准答案而不是只读搜索结果摘要。
2. As a 用户，I want 搜索成本更低，so that 不用每次搜索都消耗昂贵的主 LLM token。
3. As a 用户，I want 看屏幕功能不再依赖特定的视觉 API 供应商，so that 可以随意换 GLM-4V / Qwen-VL / 其他视觉模型而不用改项目配置。
4. As a 用户，I want 视觉功能免费或极便宜，so that 不用担心 API 费用。
5. As a 用户，I want 接入视觉 MCP 后可以用 OCR 提取图片文字、分析图表、对比图片等，so that 视觉能力更丰富。
6. As a 开发者，I want SearchAgent 的接口和普通工具 handler 一致，so that ToolRuntime 注册时不需要特殊处理。
7. As a 开发者，I want Router 接口预留好但不需要现在就实现完整分派逻辑，so that 以后加第二个子 Agent 时改动最小。
8. As a 用户，I want 看屏幕的速度不变，so that 切到 MCP 方案后不会比现在慢。

## Implementation Decisions

### C1：SearchAgent

**设计**：

SearchAgent 是一个类，`run(query: str) -> str` 是它的入口。内部有自己的 LLM 实例（可配置为更便宜的模型）+ 工具集（`fetch_url` + search backends）。最多 2 轮工具循环。

```
SearchAgent.run(query)
  └─ 第 1 轮: LLM 分析 query → 调用 search → 拿到结果
  └─ 第 2 轮 (如果需要): LLM 判断是否需要点开某条结果 → fetch_url → 最终总结
  └─ 返回 JSON: {"answer": "...", "sources": [...], "confidence": "high/medium/low"}
```

**与现状 `research` 的区别**：

| 维度 | 现状 research | SearchAgent |
|------|-------------|-------------|
| LLM | 主 LLM（贵） | 独立小模型（便宜） |
| 检索步数 | 1 步（搜→摘要） | 最多 2 轮 |
| 工具 | DuckDuckGo/Bing | 同左 + fetch_url |
| 接口 | `async def research(query) -> str` | `search_agent.run(query) -> str` |
| 注册 | `runtime.register("research", handler=research)` | `runtime.register("research", handler=search_agent.run)` |

**SubAgent 基类**：

```python
class SubAgent:
    """Base for sub-agents with independent LLM + tool loop."""
    
    def __init__(self, *, llm_service: LLMService | None = None):
        self._llm = llm_service or LLMService()
    
    async def run(self, prompt: str) -> str:
        raise NotImplementedError
```

SearchAgent 继承 SubAgent，实现 `run()`。

### C2：see_screen → MCP Vision

**截图保留**：`screen_tools.py` 中的 `_capture_sync()`、dHash 指纹去重、`ScreenFingerprintStore` 全部不动。

**视觉识别替换**：`see_screen()` 中原来调 `vision_service.describe_image(image_bytes)`，改为调 MCP vision tool。图片序列化为 base64，通过 ToolRuntime.dispatch 调 `mcp__vision__analyze_image`。

**实现方式**：`see_screen()` 接受可选的 tool_registry 参数来查 MCP 工具。找不到 MCP vision 工具时 fallback 到旧的 vision_service（向后兼容）。

**配置迁移**：
- 移除 `.env` 中的 `VISION_API_KEY`、`VISION_PROVIDER`、`VISION_BASE_URL`
- `mcp_servers.json` 新增 vision server 配置模板，API key 在其 `env` block 中

**9 个可用工具**：`analyze_image`, `ocr_image`, `table_from_image`, `analyze_ui`, `analyze_document_slide`, `describe_chart`, `compare_images`, `tile_image`, `image_info`

### C3：Router 预留

**RouterAgent 占位**：

```python
class RouterAgent:
    """Task router — receives MainAgent requests, dispatches to sub-agents.
    
    Reserved. Enable when there are ≥2 sub-agents.
    """
    pass
```

**Agent 钩子**：在 `Agent._execute_tools_with_approval()` 中增加 `_resolve_handler(tool_name: str)` 方法，现在直接返回 `self.tool_registry`，以后 Router 在这里拦截。

### 改动清单

| 文件 | 改动 |
|------|------|
| `backend/core/sub_agent.py` | **新建** — `SubAgent` 基类 |
| `backend/core/search_agent.py` | **新建** — `SearchAgent(SubAgent)` |
| `backend/core/router_agent.py` | **新建** — `RouterAgent` (占位) |
| `backend/tools/screen_tools.py` | `see_screen()` 优先调 MCP vision tool，fallback 旧 vision_service |
| `backend/tools/search_tools.py` | 保留 `_do_search` / `fetch_url`（SearchAgent 复用）；`research()` 可移除或改为 SearchAgent wrapper |
| `backend/core/agent.py` | 新增 `_resolve_handler()` 钩子 |
| `backend/main.py` | 实例化 SearchAgent，注册到 ToolRuntime；`research` handler → SearchAgent.run |
| `backend/services/vision_service.py` | 保留作为 MCP 不可用时的 fallback |
| `data/mcp_servers.json` | 新增 vision server 配置模板 |
| `.env` / `config.py` | 视觉相关配置改为可选（fallback 时用） |

## Testing Decisions

### 测试原则

- 只测外部行为：SearchAgent 输入 query 返回正确格式的 JSON；MCP vision 工具注册后可用；Router 占位类存在。
- SubAgent 基类的工具循环逻辑单独测试（注入 FakeLLMService）。

### 测试缝

| 缝 | 测试内容 | 参照 |
|---|---|---|
| SearchAgent + FakeLLMService（无 DB/无 HTTP） | 搜索→总结→返回 JSON 格式正确 | `test_agent_tools.py` 的 FakeLLMService |
| MCP vision 工具注册 | 假 MCP server 连接后 `mcp__vision__*` 工具在 ToolRuntime 中 | `test_mcp_manager.py` |
| Agent + SearchAgent handler | `Agent.run()` 触发 research → SearchAgent → tool_result | `test_agent_tools.py` 的 `TestAgentRunWithInjectedRegistry` |
| Router 占位 + SubAgent 基类 | 类存在、接口签名正确 | pure import + isinstance |

### 测试文件

- `backend/tests/core/test_search_agent.py` — Seam 1 + SubAgent 基类
- `backend/tests/core/test_multi_agent.py` — Seam 3 (Agent + SearchAgent handler)
- 直接验证 RouterAgent 导入即可，不需要单独测试文件

## Out of Scope

- **Router 完整实现** — 只定义接口，不做分派逻辑。等 ≥2 个子 Agent 时再实现。
- **DeepSeek_vision_mcp 的安装和配置** — 由用户自行 `pip install` 和配置 API key。项目只提供 `mcp_servers.json` 模板。
- **前端改动** — 无前端改动。
- **文件工具的子 Agent 化** — 读/写/列目录/搜索文件保持直接函数调用。

## Further Notes

### 验证命令

```bash
# SearchAgent
cd backend && python -m pytest tests/core/test_search_agent.py -v

# Agent + SearchAgent integration
cd backend && python -m pytest tests/core/test_multi_agent.py -v
```

### 运行方式

```bash
# MCP vision server 配置模板 (data/mcp_servers.json)
{
  "servers": [{
    "name": "vision",
    "transport": "stdio",
    "command": "python",
    "args": ["-m", "deepseek_vision_mcp"],
    "env": {
      "ZHIPU_API_KEY": "你的智谱API-Key"
    }
  }]
}
```
