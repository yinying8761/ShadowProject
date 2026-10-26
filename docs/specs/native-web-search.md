# 搜索：优先用提供商原生 web search / fetch，本地抓取降级

> 日期：2026-10-22 | 状态：**待实施** | 关联：`docs/adr/0003-search-decision-rights.md`、`docs/specs/search-augmenter-degrade(done).md`

## Problem Statement

搜索结果来自**自己抓 HTML**（`tools/search_tools.py`）：

- `ddgs` 元搜索，且为了国内网络把引擎钉死在 `bing,mojeek`（有代理才加回 duckduckgo）；
- `bing_web` 直接抓 Bing 结果页 HTML；
- `searxng`（自建）、`bing`（Azure 付费 API）作为备选。

脆弱点全在这里：反爬、被墙、页面结构一改解析就废、拿不到可靠的引用来源。`fetch_url` 同样用 BeautifulSoup 抽取正文（`MAX_PAGE_TEXT=6000`），遇到 JS 渲染页就只剩空壳。

与此同时，主流提供商已经把这个能力做进了 API：DeepSeek 新增网页搜索与抓取、OpenAI 的 Responses `web_search`、Anthropic 的服务端 `web_search` 工具、Gemini 的 `google_search` grounding。**让模型厂商去爬网页，比自己维护一个爬虫稳得多**，而且原生结果通常**自带引用**。

## Solution

引入 **provider 原生搜索后端**，作为搜索链的第一顺位；本地抓取全部保留为降级。原生实现必须**与厂商无关**：能力按模型声明，形状按 `search_api` 分叉，不写 `if provider == "deepseek"`。

- 能力表（模型级，与 `multimodal-vision.md` 同一张）新增 `native_search: bool` / `native_fetch: bool` / `search_api: openai|anthropic|gemini|deepseek|none`。
- `LLMService` 新增 `native_search(query, limit) -> list[SearchHit] | None` 与 `native_fetch(url) -> PageText | None`：`None` 表示「该模型不支持或本次失败」，由上层降级，而非抛错。
- `search_tools._do_search` 按**后端链**依次尝试：`native → ddgs → bing_web → searxng → azure_bing`；第一个成功即返回，并把实际后端与失败原因写进日志。
- `fetch_url` 同样：`native_fetch → BeautifulSoup 抽取`。
- **决策权不变**（ADR-0003）：主 LLM 决定「要不要搜」，SearchAgent 决定「怎么搜」。本 spec 只更换「怎么搜」的最优实现，不引入新的自动搜索。
- 结果统一结构，带来源：`{title, url, snippet, source}`；总结时保留引用（回答里能看到 `[1] url` 之类的出处）。

## User Stories

1. 作为用户，我问「最近有什么新东西」时，答案应当基于真实的网页内容并给出处，而不是我猜的或者抓取失败的空白。
2. 作为用户，我不想因为某个搜索站点改版/封 IP 就整块能力失效。
3. 作为用户，我不想为了搜索去配代理——国内网络下也该能用。
4. 作为用户，我换成 GPT / Gemini / Claude 时，搜索应当照样能用它们各自的原生能力，不需要我改配置或换搜索服务。
5. 作为开发者，我想让「用哪个搜索后端」由一个可枚举的链决定，失败能自动降级并留下日志。
6. 作为开发者，我想让原生搜索的响应解析只在一个地方（`search_api` → 统一结构），换厂商不影响 SearchAgent。
7. 作为开发者，我想让接入一家新厂商只有两步：填 `native_search` + `search_api`；只有形状不兼容时才加一个 parser。
8. 作为开发者，我想保住现有 `research` 工具的对外契约（返回总结文本 + 来源），不因为换后端改变 Agent 的行为。
9. 作为开发者，我想在 eval 里能对比「原生 vs 本地抓取」的实际结果质量。

## Implementation Decisions

### 后端链与降级

```
research(query) → SearchAgent
                   └─ search 工具 → _do_search
                                     ├─ native_search      (provider 原生；需 native_search=True)
                                     ├─ ddgs               (bing/mojeek；有代理时加 duckduckgo)
                                     ├─ bing_web           (HTML 抓取，现有实现)
                                     ├─ searxng            (SEARXNG_URL)
                                     └─ azure_bing         (BING_API_KEY)
                                     └─ fetch_url → native_fetch → BeautifulSoup
```

- 每个后端返回统一 `list[SearchHit]`；`[]` 视为「无结果但成功」，`None`/异常视为「失败，继续下一个」。
- 链的顺序由 `search_backend_order()` 计算（纯函数，便于单测）：去掉不可用项（无 key / 无配置 / 能力不支持），保留配置顺序。
- 新增配置 `search_prefer_native: bool = True`（关掉即回到纯本地链，用于对照与排障）。
- 日志一行：`[Search] backend=native api=anthropic query=… hits=5 fallbacks=none`，失败项写成 `ddgs: timeout`。

### 原生后端：按 `search_api` 分叉，而不是按厂商

| `search_api` | 调用形态 | 典型模型 |
|---|---|---|
| `openai` | Responses/Chat 的 `web_search` 工具调用，取 `url_citation` 注解 | OpenAI GPT 系列、任何提供该工具的 OpenAI 兼容端点 |
| `anthropic` | 服务端工具 `web_search`，解析 `web_search_result` 块 | Claude |
| `gemini` | `google_search` grounding（`groundingMetadata` 里带 `uri`/`title`），或走 Gemini 的 **OpenAI 兼容端点**复用 `openai` 分支 | Gemini |
| `deepseek` | DeepSeek 官方搜索接口 | DeepSeek |
| `none` | 不尝试原生，直接进本地链 | 其余 |

**Gemini 的默认策略**同视觉：优先走它的 OpenAI 兼容端点；只有用户显式用原生 `generateContent` 时才需要 `gemini` 这一支。

```python
SEARCH_PARSERS = {
    "openai":    _parse_openai_citations,
    "anthropic": _parse_anthropic_blocks,
    "gemini":    _parse_gemini_grounding,
    "deepseek":  _parse_deepseek_search,
}
```

- 统一超时（`search_timeout`，默认 20s）与错误折叠：任何异常 → 返回 `None` + 一条日志，**不向上抛**（保证链能继续）。
- 未知/缺失 `search_api` → 视为不支持，**直接降级**（搜索是可选能力，不该因为配置缺项而报错；这与视觉相反，视觉必须显式报错，因为用户明确要求"看"）。
- `native_fetch` 同理：返回 `(title, text)`，`text` 截断到 `MAX_PAGE_TEXT`。

### 与既有契约的关系

- `research` 工具签名/返回形态不变（Agent 的 prompt 与 eval 用例不受影响）。
- `message_augmenter` 的「提示注入」路径不变（ADR-0003）。
- `fetch_url` 工具签名不变。
- 本地抓取代码**明确保留**：它是降级路径，也是没有原生能力的 provider 的唯一路径。

### 配置与可见性

- 能力表驱动：设置页显示「当前模型：原生搜索 ✓ / 本地抓取」。
- `search_proxy`、`SEARXNG_URL`、`BING_API_KEY` 语义不变。

## Testing Decisions

| Seam | 形态 | 测什么 |
|---|---|---|
| **S1 后端链计算** | 纯函数 | 能力/配置组合 → 期望链；`search_prefer_native=False` → 链里没有 native；缺 key → 对应项被剔除 |
| **S2 原生响应解析** | 纯函数（每种 `search_api` 一份固定响应体快照） | `openai`（url_citation）/ `anthropic`（result 块）/ `gemini`（groundingMetadata）/ `deepseek` 四种 → 统一 `SearchHit`；空结果、缺字段、带引用 |
| **S3 `search_api` 分派** | 纯函数 | 已知 api → 对应 parser；`none` / 缺失 / 未知 → 不尝试原生、直接进本地链（不报错） |
| **S4 降级** | 假后端 | native 抛错/返回 None → 落到 ddgs；ddgs 也失败 → 落到 bing_web；全失败 → 返回明确的空结果（不抛） |
| **S5 fetch 降级** | 假后端 | `native_fetch` 返回 None → BeautifulSoup 抽取；两者都失败 → 工具返回错误信封 |
| **S6 契约不变** | 现有 `research` 用例 | 工具签名/返回结构不变；SearchAgent 仍能走完 ≤2 轮 |

- 真实网络的用例标记 `live`（默认跳过，同 `test_mcp_amap_live.py` 的 `MCP_LIVE_TESTS` 约定），避免 CI 依赖外网。
- eval：新增 `search` 类别用例，断言「答案包含来源 URL」，并在报告里标注实际后端与 `search_api`。

## Out of Scope

- **自建搜索引擎 / 索引**（如自托管 SearXNG 之外的方案）。
- **搜索结果缓存**（原 5 分钟缓存在 ADR-0003 已删除，不借本次加回）。
- **站点白名单/黑名单、按域名加权**。
- **JS 渲染页的抓取**（如无头浏览器）——`native_fetch` 能覆盖多少算多少，剩余交给用户换源。
- **为搜索单独计费/配额管理**。

## Further Notes

- **成本对比**：原生搜索通常按 provider 的搜索计费，且省掉我们自己的带宽与解析；本地链仍作为零成本兜底。
- **质量差异**：原生结果通常自带摘要与时间戳，比抓 HTML 的 snippet 干净；但覆盖的中文站点可能不如 Bing 抓取广——这正是保留双路径的理由。
- **加一家新厂商的两步**：填能力（`native_search` + `search_api`）；若它的返回形状不在现有四种里，再加一个 parser。**不改 SearchAgent、不改 `research` 工具、不改后端链逻辑。**
- **与视觉共用一张能力表**：`supports_vision` / `image_api` / `native_search` / `search_api` 落在同一处（`config.LLM_PRESETS` + 用户覆盖），见 **ADR-0011**。
- **与视觉相反的一处约定**：遇到不认识的 `image_api` 必须**报错**（用户明确要求看屏幕，静默丢图不可接受）；遇到不认识的 `search_api` 则**降级**（搜索是可选增强，不该因为配置缺项打断对话）。这条不对称是 ADR-0011 的决定，避免后来者"统一化成同一种处理"。
- **风险**：provider 的原生搜索返回格式属于各家的私有契约，变化不受我们控制；因此 **S2 每个 api 都必须有固定响应体快照测试**，且解析失败必须降级而不是抛错。
