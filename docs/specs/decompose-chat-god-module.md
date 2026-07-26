# Spec: 拆分 WebSocket 处理器上帝模块

**状态:** `ready-for-agent`
**日期:** 2026-07-21
**来源:** `/improve-codebase-architecture` 候选方案 #1

---

## Problem Statement

`backend/api/chat.py` 目前是一个 754 行的"上帝模块"——WebSocket 处理器承载了 8 个以上不同的关注点（每日问候编排、消息增强流水线、屏幕捕获门控、主动陪伴会话管理、位置更新处理、工具审批流程等）。每次添加功能都需要编辑同一个文件，零局部性。修改屏幕捕获逻辑和修改每日问候逻辑发生在同一个文件中。无法对任何单一关注点进行独立测试。

## Solution

将 `chat.py` 中的 4 个关注点提取为独立的深层模块，每个模块拥有窄接口、隐藏大量实现。`chat.py` 缩减为约 120 行的薄适配器，仅负责 WebSocket 生命周期和将消息路由到各模块。

### 提取的模块

| 模块 | 接口 | 提取行数 | 职责 |
|---|---|---|---|
| `GreetingOrchestrator` | `async def run(...) → AsyncIterator[event]` | ~100 行 | 每日问候的完整编排 |
| `MessageAugmenter` | `async def augment(content) → str` | ~65 行 | 搜索路由 + POI 注入流水线 |
| `ScreenCaptureGate` | `async def try_capture(...) → str \| None` | ~40 行 | 屏幕捕获 + 指纹去重 + 审批 |
| `ProactiveSession` | 类，包装 ProactiveWatcher | ~200 行 | 监控器生命周期、状态持久化、配置刷新 |

## User Stories

1. 作为开发者，我希望修改每日问候的逻辑时只需要编辑一个专门的模块，而不是在 WebSocket 处理器中翻找，这样改动的风险更可控。
2. 作为开发者，我希望搜索路由和 POI 注入逻辑集中在一个模块中，这样添加新的消息增强步骤（如翻译、敏感词过滤）时不影响其他代码。
3. 作为开发者，我希望屏幕捕获的去重和审批流程独立存在，这样可以在不启动完整 WebSocket 的情况下测试屏幕捕获逻辑。
4. 作为开发者，我希望主动陪伴的监控器生命周期管理不在 WebSocket 处理器中，这样修改监控策略时不需要理解 WebSocket 协议。
5. 作为开发者，我希望 `chat.py` 缩减到 150 行以内，作为纯粹的 WebSocket 适配器——接受连接、路由消息、发送响应。
6. 作为测试者，我希望能够用假依赖（fake send_json、fake agent）独立测试每个提取出的模块，而不需要启动真实的 WebSocket 连接。
7. 作为新加入的开发者，我希望理解每日问候功能时只需阅读一个 100 行的模块，而不是在 754 行的文件中追踪闭包。

## Implementation Decisions

### 决策 1：提取顺序

按依赖关系从少到多依次提取：
1. **GreetingOrchestrator** — 对其他模块零依赖，最自包含
2. **MessageAugmenter** — 仅依赖 `core.router` 和 `services.location_service`，不依赖 WebSocket
3. **ScreenCaptureGate** — 依赖 `tools.screen_tools`，需要注入 send_json 回调用于审批
4. **ProactiveSession** — 最复杂的提取，包装 ProactiveWatcher 及其全部回调

每个阶段完成后验证现有功能不受影响，再进入下一阶段。

### 决策 2：GreetingOrchestrator 的接口

模块接收已解析的上下文（location、weather、memories、days_since_last），加上 agent 实例和 send_json 回调。自己负责角色检查（是否今天已问候）和问候后的标记持久化。

```
run(session, char_id, conv_id, agent, send_json, *,
    location, weather, days_since_last, memories) → AsyncIterator[None]
```

内部通过 send_json 产出事件。如果角色今天已问候，直接发送 skip 事件并返回。如果 agent 产出了 token 内容，标记角色为已问候。

### 决策 3：MessageAugmenter 的接口

纯函数式流水线：接收原始用户消息，依次通过搜索路由 → POI 注入 → 返回增强后的消息。不接触 WebSocket。

```
async def augment(content: str) → str
```

内部调用 `need_search()` 检测搜索意图，命中则查缓存或调用 research 工具。同时检测美食关键词，命中则查询附近 POI 并注入上下文。流水线可扩展——新增增强步骤只需向流水线添加一个阶段。

### 决策 4：ScreenCaptureGate 的接口

接收 send_json 回调和审批回调，封装截图 + 指纹去重 + 结果解析。

```
async def try_capture(send_json, approval_callback, *,
    focus=None, fingerprints, silent_approval=False) → str | None
```

内部处理：审批检查 → 截图 → 指纹记录 → JSON 解析 → 通过 send_json 发送 tool_use/tool_result 事件。返回描述文本或 None。

### 决策 5：ProactiveSession 的接口

一个类，包装 ProactiveWatcher 的完整生命周期。接收所有 getter/setter 回调，暴露 `start()`、`stop()`、`reset_idle()`。

```
class ProactiveSession:
    def __init__(self, conversation_id, char_id_getter,
                 send_json, approval_callback, get_user_config)
    async def start() → None
    async def stop() → None
    def reset_idle() → None
```

内部管理：watcher 创建、配置缓存刷新循环、状态加载/持久化、proactive_trigger 回调（含 build_proactive_context 和 agent.run 调用）。这是最复杂的提取。

### 决策 6：chat.py 保留的内容

提取后 chat.py 保留：
- WebSocket  accept、消息接收循环、断开处理（~30 行）
- `handle_message()` 分发逻辑——根据 msg_type 路由到各处理器（~40 行）
- `handle_location_update()` 闭包（~40 行，相对自包含）
- `handle_daily_greeting()` 简化为：解析上下文 → 调用 GreetingOrchestrator.run()
- 模块级定义：`SCREEN_KEYWORDS`、`detects_screen_intent()`、`build_tool_context_message()`、`ChatRequest`、`send_chat` REST 端点
- 模块级单例 `agent` 和 `conv_manager`

目标：约 180-200 行（从 754 行）。

### 决策 7：文件位置

- `backend/core/greeting_orchestrator.py` — 问候编排器（与 agent.py 同级，都是编排逻辑）
- `backend/services/message_augmenter.py` — 消息增强器（与 location_service 等同级，都是服务适配器）
- `backend/services/screen_capture_gate.py` — 屏幕捕获门（同服务层）
- `backend/services/proactive_session.py` — 主动会话（包装 watcher，服务层）

### 决策 8：模块级单例的处理

当前 `agent = Agent()` 和 `conv_manager = ConversationManager()` 在 chat.py 模块级别创建。提取后，GreetingOrchestrator 和 ProactiveSession 将通过依赖注入接收 agent 实例，而非创建新的。chat.py 保留这些单例的创建。

## Testing Decisions

### 测试哲学

只测试外部行为，不测试实现细节。每个提取的模块通过其公开接口进行测试。使用假依赖（fake）代替真实的 WebSocket、Agent、LLM。

### 测试 GreetingOrchestrator

- **假 Agent**：返回预设的 token 事件序列或空序列
- **假 send_json**：记录所有发送的事件到列表中
- **测试用例**：
  - 角色今天已问候 → 发送 skip 事件，不调用 agent
  - agent 产生 token → 发送 token 事件 + done 事件，标记角色为已问候
  - agent 返回空内容 → 发送 skip 事件，不标记
  - agent 抛出异常 → 异常传播给调用者

### 测试 MessageAugmenter

- **假 research 工具**：返回预设搜索结果
- **假 POI 服务**：返回预设附近地点列表
- **测试用例**：
  - 普通闲聊消息 → 不触发搜索，返回原消息
  - 搜索关键词消息 → 调用 research，返回增强消息
  - 搜索结果缓存命中 → 使用缓存，不调用 research
  - 美食关键词消息 → 注入 POI 上下文

### 测试 ScreenCaptureGate

- **假 send_json**：记录发送的事件
- **假 approval_callback**：返回预设的批准/拒绝
- **假 see_screen**：返回预设的屏幕描述 JSON
- **测试用例**：
  - 需要审批且被拒绝 → 发送 denied 事件，返回 None
  - 需要审批且被批准 → 截图成功，返回描述
  - 静默审批模式 → 跳过审批直接截图
  - 截图失败 → 发送 error 事件，返回 None

### 测试 ProactiveSession

- **假 ProactiveWatcher**：记录 start/stop/reset_idle 调用
- **假 get_user_config**：返回预设配置
- **测试用例**：
  - start 后 watcher 被创建和启动
  - stop 后 watcher 被停止
  - reset_idle 委托给 watcher.reset_idle
  - 配置刷新循环在 start 后运行

### 已有测试先例

项目目前没有测试（无 pytest 配置、无测试文件）。这些测试决策定义了项目的测试基础设施起点。推荐使用 `pytest` + `pytest-asyncio`。

## Out of Scope

- **Agent.run() / run_daily_greeting() 统一**（候选方案 #2）——这是独立的后续工作，在 GreetingOrchestrator 提取后更容易进行
- **chatStore 拆分**（候选方案 #3）——前端重构，不在本次范围内
- **MemoryService 拆分**（候选方案 #4）
- **ToolRegistry 注入**（候选方案 #5）
- **LLMService 格式转换提取**（候选方案 #6）
- `handle_location_update()` 的提取——它相对自包含（~40 行），暂留在 chat.py 中
- `send_chat` REST 端点的提取——逻辑简单，不需要独立模块
- 模块级单例（agent、conv_manager）的生命周期管理——这是更广泛的依赖注入话题，超出本次范围

## Further Notes

- 这是 `/improve-codebase-architecture` 报告中 6 个候选方案中的第一个。报告中建议按顺序执行：GreetingOrchestrator → MessageAugmenter → Agent 统一 → ProactiveSession → chatStore/MemoryService 拆分。
- 每个阶段的 diff 应保持小而可审查（<200 行变更）。
- 提取后的模块应使用 `print(f"[ModuleName] ...", flush=True)` 保持现有的日志风格。
- 如实现过程中发现某模块的接口需要调整（例如发现额外的隐藏依赖），更新此 spec 的 Implementation Decisions 部分。
- 完成后运行应用的手动冒烟测试：发送消息、切换角色、触发每日问候、最小化/恢复窗口验证主动回复。
