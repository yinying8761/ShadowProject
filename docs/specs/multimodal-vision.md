# 视觉能力：从「专家模型转述」改为「主模型自己看图」

> 日期：2026-10-22 | 状态：**待实施** | 关联：`docs/adr/0010-mcp-server-isolation.md`、`docs/adr/0011-provider-capability-matrix.md`、`docs/specs/mcp-integration(done).md`

## Problem Statement

现在的「AI 看屏幕」是**转述架构**：截图不进入对话，而是交给另一个视觉模型（`services/vision_service.py`）或一个 MCP 视觉 server，由它写一段文字，再把这段文字塞进对话。主模型从头到尾**没有看过图片**。

这带来四个问题：

1. **依赖一个已经停用的模型**：`VISION_PRESETS` 里的 `qwen` / `qwen-vl-max` 已停用；而主模型（DeepSeek 新一代）本身已经多模态——我们花钱维持了一条多余、且已经坏掉的链路。
2. **信息有损**：转述丢了布局、颜色、报错原文位置等细节。用户问「屏幕上这个报错什么意思」，主模型只拿到别人的二手描述，再转述一次。
3. **每次多一次模型调用**，而且用的是**另一个**模型（不同人设/不同上下文）——和「角色就该用它自己的脑子看」直接冲突。
4. **为了接一个 MCP 视觉 server，代码里出现了针对单个 server 私有 schema 的适配**（工具名 `mcp__vision__analyze_image`、参数名 `image`、环境变量 `VISION_API_KEY`）——这违背了 MCP「配置即插拔、不为单个 server 写代码」的初衷。2026-10-20 这条适配还因为把一个失败信封当成描述，把一段 429 报错写进了用户消息并在历史里被复读了三天。

## Solution

**把视觉变成主模型的能力，而不是一个外挂——而且与厂商无关。**

- 能力表（模型级）新增 `supports_vision` 与 `image_api`；**任何**多模态模型（DeepSeek、OpenAI GPT、Claude、Gemini、GLM-V、Qwen-VL、任意 OpenAI 兼容端点）只要填这两项就能看图，业务代码不为某一家写分支。
- 主模型多模态时，截图以**图片内容块**的形式进入当轮的请求，主模型直接看。
- 一次「看屏幕」有两条路，都不再经过专家模型：
  - **会话级挂载**（首选，零额外调用）：用户触发看屏幕（「看看我屏幕」/强制视觉）→ 截图作为图片块附在**当轮用户消息**上，同时给一句极短说明。图片**只存在于这一轮请求**，不落库。
  - **工具级**（角色自己中途想看）：`see_screen` 工具内部用**主模型**看图并返回文字（一次模型调用），工具契约仍是 `dispatch → str`，不改变现有工具信封。
- 主模型不支持视觉时：回落到现有 `vision_service`（只保留一个可用预设），**不再经过 MCP 视觉**。
- **删除** `screen_tools.py` 里的 MCP 视觉适配（`_try_mcp_vision`、`VISION_MCP_TOOL`、`vision_mcp_tool` 配置）；`config.py` 的 `VISION_PRESETS` 移除已停用的 `qwen`。
- 屏幕内容**不写进消息历史**：上一轮「把描述拼进用户消息」的做法会让一段两小时前的截图描述永久留在上下文（2026-10-20 真实事故）。这里改成「图片随请求走、文字只留用户原话 + 角色自己的回复」。

## User Stories

1. 作为用户，我说「看看我屏幕」，角色直接描述我屏幕上的东西，不再出现「视觉那边被限流了」这种第三方故障。
2. 作为用户，我不想让角色的屏幕描述出现在历史里被反复复读——那只是一眼，不是我们聊天的内容。
3. 作为用户，我换用 GPT / Gemini / Claude 这些多模态模型时，看屏幕同样能直接用，不需要额外再配一个「视觉模型」。
4. 作为用户，我切换到一个不支持视觉的模型时，看屏幕应当**明确告诉我不可用**，而不是静默失败或给我一段别人的转述。
5. 作为用户，角色在我说话中途想看屏幕时可以自己调用工具去看，不需要我再说一次「看屏幕」。
6. 作为开发者，我想让「模型是否支持视觉」只有一个来源（模型级能力表 + 用户覆盖），新增/切换/换厂商时不用改业务代码。
7. 作为开发者，我想让接入一家新厂商只有两步：填 `supports_vision` + `image_api`；只有连 OpenAI 兼容都不兼容时才加一个 formatter builder。
8. 作为开发者，我不想再为任何一个 MCP server 的私有参数名写适配代码——MCP 的价值是配置即插即拔。
9. 作为开发者，我想让图片载荷的形状分叉只发生在 formatter registry 一处，且未知 `image_api` 会显式报错（不静默丢图）。
10. 作为开发者，我想让「图片不落库」这一点有测试锁住，避免将来有人顺手把 base64 塞进 `messages`。

## Implementation Decisions

### 能力表（单一来源，**按模型而非按厂商**）

视觉能力必须覆盖所有主流厂商（DeepSeek / OpenAI GPT / Anthropic Claude / Google Gemini / 智谱 GLM-V / 通义 Qwen-VL / 任何 OpenAI 兼容端点），而**同一家厂商的内部也有不支持视觉的模型**（`gpt-4o` 能看、老模型不能），所以能力判断落在**模型级**，厂商预设只提供默认值：

```python
LLM_PRESETS = {
    "deepseek":  {..., "supports_vision": True,  "image_api": "openai"},
    "openai":    {..., "supports_vision": True,  "image_api": "openai"},
    "anthropic": {..., "supports_vision": True,  "image_api": "anthropic"},
    "gemini":    {..., "supports_vision": True,  "image_api": "openai"},   # 见下
    "zhipu":     {..., "supports_vision": None,  "image_api": "openai"},   # 取决于模型：glm-4v 能看
    "qwen":      {..., "supports_vision": None,  "image_api": "openai"},   # qwen-vl 能看
    "moonshot":  {..., "supports_vision": False, "image_api": "openai"},
    "custom":    {..., "supports_vision": None,  "image_api": "openai"},
}
```

- `supports_vision` 三态：`True` / `False` / `None`（未知，由用户或模型名推断）。
- 设置里新增 **模型级覆盖** `llm_supports_vision: bool | None`（写进 `user_config` / `.env`），优先于预设。用户填了一个预设不知道的新模型时，这一个开关说了算。
- 暴露 `runtime_config.supports_vision()`；**业务代码只能调这一个函数**，禁止按 provider 名字写 `if`。
- `VISION_PRESETS`（fallback 专家模型）保留 `openai` / `anthropic` / `zhipu` / `custom`，补上可用的 Gemini 条目，**删除 `qwen`**（模型已停用）。

### 图片载荷：按 `image_api` 分叉，而不是按厂商

各家的图片块形状只有**三种**，与厂商名无关：

| `image_api` | 形状 | 用于 |
|---|---|---|
| `openai` | `{"type": "image_url", "image_url": {"url": "data:<mime>;base64,..."}}` | DeepSeek、OpenAI、智谱、通义、Moonshot、以及**任何 OpenAI 兼容端点** |
| `anthropic` | `{"type": "image", "source": {"type": "base64", "media_type": ..., "data": ...}}` | Claude |
| `gemini` | `{"inline_data": {"mime_type": ..., "data": ...}}`（`generateContent` 原生） | 仅当不走 OpenAI 兼容层时 |

**Gemini 的默认策略**：优先使用它提供的 **OpenAI 兼容端点**（`https://generativelanguage.googleapis.com/v1beta/openai/`），这样 `image_api="openai"` 即可，不必新增第三种形状。只有用户显式配置原生 `generateContent` 时，才需要 `gemini` 这一支（放进 formatter registry，不写进业务代码）。

「加一家新厂商」的完整动作因此只有两步：**在预设里填 `supports_vision` + `image_api`**；如果它连 OpenAI 兼容都不兼容，再在 formatter registry 里加一个 builder。**不改 Agent、不改驱动、不改 `see_screen`。**

### 消息载荷

- `Agent.run(...)` 增加 `images: list[tuple[bytes, str]] | None`（图片字节 + mime）。
- 图片进入**本轮最后一条 user 消息**的 content 数组，位置在文本之后；历史组装（`ConversationManager.get_context_messages`）**永远只返回文本**。
- 图片不写 `messages` 表、不进摘要、不进记忆。理由：体积、隐私，以及「屏幕只是一眼」的语义。
- 当前轮次结束即丢弃，不需要清理逻辑。

### formatter：挂在既有的 `MessageFormatter` 缝上

`docs/specs/message-formatter(done).md` 已经建立了 `MessageFormatter` 协议（`base.py` 默认实现 + `AnthropicFormatter` 覆写），并明确预留了「未来加 `GeminiFormatter`」的位置。图片块就加在这条缝上，**不另起一套 registry**：

```python
class MessageFormatter(Protocol):
    def image_part(self, image_bytes: bytes, mime: str) -> dict: ...

# base（OpenAI 兼容形状）
{"type": "image_url", "image_url": {"url": "data:<mime>;base64,..."}}

# AnthropicFormatter 覆写
{"type": "image", "source": {"type": "base64", "media_type": ..., "data": ...}}

# 将来若真的要原生 Gemini（而非其 OpenAI 兼容端点），新增 GeminiFormatter
{"inline_data": {"mime_type": ..., "data": ...}}
```

- 业务代码只调 `formatter.image_part(...)`，永远不自己拼 payload。
- **未知/未实现的形状必须显式报错**（而不是悄悄退化成纯文本请求）——否则接入新厂商时会出现「图没发出去但没人知道」。

### `see_screen` 的两种模式

```
截图（mss → JPEG，现有 _capture_sync 不变）
├─ 主模型支持视觉 → 见「会话级挂载」：返回极短说明（不需要模型调用）
└─ 否则 → vision_service（保留的唯一 fallback）→ 返回描述文本
```

- 会话级挂载的入口仍在 `api/chat.py`（`force_vision or detects_screen_intent`）与 `ScreenCaptureGate`：把截图交给 `Agent.run(images=[...])`，**不再**把描述拼进用户消息字符串。
- `see_screen` 作为**工具**被调用时（角色自主看），走主模型视觉：内部用同一套 `Agent`/`LLMService` 发一次带图片的请求，把结果文本作为工具返回值。这样工具信封不变（`dispatch → str`），专家模型不再是必需品。
- 工具审批不变（`see_screen` 仍需用户同意）。

### 屏幕去重（保持现状，别被"顺手优化"掉）

缓冲区**存的是指纹，不是图片**，且它只保护"我们自己决定要看"的那条路：

| 组成 | 语义 | 本 spec 是否改动 |
|---|---|---|
| `_capture_dhash_sync()` | 64-bit 感知哈希（9×8 灰度，相邻像素），~10ms，不调模型 | 不改 |
| `ScreenFingerprintStore(maxlen=5)` | 最近 **5 个 hash**（FIFO），`is_known()` 对全部比较 | 不改 |
| `DHASH_THRESHOLD = 10` | Hamming ≤10 = 同一屏（时钟跳秒/鼠标移动不算换屏） | 不改 |
| **主动陪伴** | 5 分钟节流 + 指纹已知 → **跳过视觉调用** | 保留（这是去重的唯一用途） |
| **用户发起「看屏幕」** | 不去重：用户要看得看 | **必须保持不去重** |
| **Agent 调 `see_screen` 工具** | 不去重 | 保持 |

- 铁律：**用户发起的看屏幕永不因"同一屏"被跳过**。图片改成进对话之后，跳过就意味着模型手里没有图、只能编——这比省一次调用糟糕得多。
- 去重表里只有 int，没有图片内容：本 spec 的"图片不落库"与它不冲突，两者都别动。
- 主动路径的"跳过"语义也不变：跳过 = 这一轮不给模型屏幕上下文（它本来就不需要）。

### 删除清单

- `screen_tools._try_mcp_vision`、`_last_vision_error`、`_VISION_ERROR_MARKERS`、`VISION_MCP_TOOL`、`_vision_mcp_tool()`
- `config.vision_mcp_tool`
- `config.VISION_PRESETS["qwen"]`
- `data/mcp_servers.json` 里的 `vision` server（用户侧，非代码）
- 相关测试（`test_screen_mcp.py` 的 MCP 部分改为「主模型视觉 + fallback」两组）

### UI

LLM 配置面板在当前模型旁显示能力来源：**「支持视觉（模型默认）」**、**「支持视觉（你手动开启）」**、**「不支持视觉」**。三态必须能分辨，否则用户不知道是自己覆盖的还是预设给的。

- 提供「强制开启/关闭视觉」开关（写 `llm_supports_vision`），用于预设不认识的自定义模型。
- 不支持视觉时，`see_screen` 的审批文案附带一句「当前模型不支持视觉，将使用备用视觉模型」。

## Testing Decisions

| Seam | 形态 | 测什么 |
|---|---|---|
| **S1 formatter registry** | 纯函数（表驱动） | `openai` / `anthropic` / `gemini` 三种 builder 的结构；data URL 前缀；mime 透传；**未知 `image_api` 抛错**（不静默丢图） |
| **S2 能力解析** | 纯函数 | 预设默认 → 生效值；`llm_supports_vision` 覆盖预设；`None`（未知）的兜底策略；三态组合 |
| **S3 能力分派** | 假 Agent + 假 LLMService | `supports_vision` 真/假 → 走「挂载图片」还是「vision_service」；只有一条分支被调用 |
| **S4 see_screen 三态** | 假主模型 + 假 vision_service | 主模型视觉可用 → 不碰 vision_service；不支持 → 调 fallback；都没有 → 明确错误 |
| **S5 不落库** | 内存 SQLite | 一轮带图对话后 `messages` 里没有 base64、没有「已附上截图」之类注记；历史长度与不带图时一致 |
| **S6 换厂商不改业务代码** | 结构断言 | 用 `custom` + `supports_vision=True` + `image_api="openai"` 配一个假端点 → 走主模型视觉分支（证明新厂商无需改代码） |

- 回归：1:1 文本对话的请求体与响应不变；群聊路径复用同一分派（群成员也能看屏幕，但不引入新的并发问题）。
- 手工验收：切换模型（多模态 / 非多模态）各看一次屏幕，日志应打出实际走的分支。

## Out of Scope

- **用户主动发图**（上传/粘贴图片聊天）——本 spec 只做「看屏幕」，但 S1/S2 的载荷与能力表是它的前置。
- **多图 / 历史图片引用**（「你刚才看到的那张图」）——图片不落库，故不支持引用，这是刻意选择。
- **群聊里多个成员同时看屏幕**——沿用现有并发规则，不额外优化截图缓存。
- **本地 OCR / 屏幕理解模型**。
- **MCP 视觉 server 的任何适配**——删除而非修复。

## Further Notes

- **成本**：会话级挂载比现状**少一次**模型调用（现状：专家视觉 1 次 + 主模型 1 次）。工具级与现状调用数相同，但用的是主模型（上下文/人设一致）。
- **隐私**：截图从不落盘、不落库、不进记忆，仅存在于内存中的一次请求；与 `screen_tools` 现有的「never written to disk」约束一致。
- **与 ADR-0010 的关系**：ADR-0010 记录了「不为单个 MCP server 写适配」的理由；本 spec 是把那条临时适配真正删掉的收尾。
- **能力表是共用件**：`docs/specs/native-web-search.md` 的 `native_search` / `search_api` 与本 spec 的 `supports_vision` / `image_api` 落在同一张模型级能力表里（`config.LLM_PRESETS` + 用户覆盖），见 **ADR-0011**。
- **与搜索相反的一处约定**：不认识的 `image_api` 必须**报错**（用户明确要求看屏幕，静默丢图不可接受）；不认识的 `search_api` 则**降级**（搜索是可选增强）。这条不对称要写进 ADR-0011，避免后来者"统一"成同一种处理。
- **加一家新厂商的完整动作**：填 `supports_vision` + `image_api`（两步）；只有这家连 OpenAI 兼容端点都没有时，才加一个 `MessageFormatter` 子类。**不改 Agent、不改驱动、不改 `see_screen`。**
- **formatter 缝的归属**：图片块加在 `docs/specs/message-formatter(done).md` 建立的 `MessageFormatter` 协议上（该 spec 已预留 `GeminiFormatter` 的位置），不新起一套 registry。
