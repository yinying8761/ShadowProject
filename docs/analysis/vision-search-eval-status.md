# 视觉 / 原生搜索 / Eval 批次 — 现状与交接（临时报告）

> 生成：2026-11-02 | **临时文件，未提交**，供接手者阅读 | 仓库：`F:\ShadowProject`
>
> ⚠️ **当时的交接快照，非权威**：它写于 ADR-0012 与两处代码修复之前。"未提交工作"与"测试数字"以 `docs/STATE.md` 为准。
>
> 本文件是交接用快照：工作区有一批**已改未提交**的审查修复，加上一个**与本批无关的环境故障**。
> 接手前请先读 §1（环境）和 §5（不要做什么）。

---

## 1. 环境故障（与本批改动无关，但会挡住测试）

| 包 | 版本 | 说明 |
|---|---|---|
| Python | 3.14.4 | |
| pydantic | **2.14.1** | 被外部升级过（10-29 时还是可用的旧版） |
| mcp | **1.8.1** | 旧版，内部 `from pydantic._internal._typing_extra import eval_type_backport` |
| fastapi | 0.115.14 | 要求 `starlette<0.47` |

**症状**：`python -m pytest tests/` 在收集阶段就中断：

```
ImportError: cannot import name 'eval_type_backport' from 'pydantic._internal._typing_extra'
→ ERROR tests/test_mcp_amap_live.py, tests/test_mcp_manager.py
```

该符号在 pydantic 2.12 被移除（内联），所以 mcp 1.8.1 与新 pydantic 不兼容。

**影响范围**：仅 MCP。`mcp` 的导入在 `backend/services/mcp_manager.py:39-42` 是**函数内懒加载**，
所以**后端能正常启动**，只有真去连 MCP 服务（含高德）时才抛错。两个测试模块收集失败。

**两条常规修法都堵死了（已实测）**：

1. `pip install "pydantic<2.12"` → `ResolutionImpossible`：`pydantic-core` 旧版**没有 cp314 wheel**（需 Rust 源码编译）。
2. `pip install -U mcp` → 会拉 **mcp 2.3.0 / 1.30.0 + starlette 1.10**，与 `fastapi 0.115` 的 `starlette<0.47` 冲突。
   这正是 **ADR-0010** 记录的死结，不是新问题。

**建议修法（未实施，等用户点头）**：加一个 3–10 行的兼容垫片
（在 `backend/main.py` 与 `tests/conftest.py` 最早期补上缺失符号），不碰全局 Python，
并把 `mcp_manager.py` 两处裸 import 包成"MCP 不可用"的可诊断错误。
在 `CONTEXT.md` 记一笔"临时，等 mcp 修好 pydantic 上限后删"。

---

## 2. 工作区状态

- **分支**：`master`，`origin/master` = `b63e940`，**本地 4 个提交未推送**：
  `61fe8ef`(02 截图进当轮) → `c00a4ed`(05 see_screen 走主模型) → `5f857a9`(03 原生搜索进链首) → `c1521e1`(06 原生抓取 + Gemini)。
  （ticket 01「能力表」`a65b0be` 已在 `origin/master` 上。）
- **工作区**：18 个文件已改未提交（17 修改 + 1 删除），是本文 §3 那批审查修复。
- **测试基线**（排除 §1 那两个环境坏掉的模块）：**727 passed / 0 failed**。
  健康环境下应为 **754 passed / 6 skipped**（749 基线 + 本批净增 5）。

---

## 3. 本批已完成（未提交）

### 3.1 原生搜索：DeepSeek 的形状已实测并接通

DeepSeek 的 `/responses` + `tools=[{"type":"web_search"}]` **可用且兼容 OpenAI Responses**，但：

- 来源**不在** `message.content[].annotations`（永远为空，所以旧的 `_parse_openai` 解析 0 条），
  而在 `output[]` 的 `web_search_call` 项里：`action.type=="open_page"` 时 `action.url` 是真来源，
  需要**剥掉 `#ws_call_id=...` 片段**。
- **是否有真搜取决于模型**：`deepseek-v4-pro` 会搜（实测 5 次 `web_search_call`），
  `deepseek-v4-flash` 只接受参数、不执行。
- DeepSeek **不把网页正文返回给我们**（正文只进它自己的上下文）→ **原生抓取不可用**。
- 成本参考：单次原生搜索 input 约 **19k–28k tokens**。

已改：`services/search_hits.py` 新增 `_parse_deepseek_search`/`_strip_ws_fragment`/`_title_from_url`
（无标题无摘要，标题从 URL 拼），注册进 `_PARSERS`；`services/llm_service.py` 让 `openai`/`deepseek`
共用同一个 Responses 请求体（响应形状不同、解析器不同）；`SHAPE_SDKS["deepseek"]="openai"`。

### 3.2 能力表改成"不撒谎"

- `config.py`：deepseek 预设 `native_search=None`（未知 → 默认不跑，走本地链）、`native_fetch=False`。
  **行为变化**：原生搜索默认关闭，需在设置里按模型手动开（v4-pro）。
- `capabilities.py`：`IMAGE_APIS=("openai","anthropic")` —— 移除 `gemini` 图片形状
  （`sdk_type` 只可能是 openai/anthropic，选了必然抛"图片格式与底层 SDK 不匹配"）。
  `SEARCH_APIS` 未动（gemini 搜索走 REST，是通的）。
- `formatters/gemini_formatter.py` **已删除**（变为死代码），`__init__.py`/`base.py`/`images.py` 同步清理。

### 3.3 文档已对齐实现

- `docs/specs/native-web-search.md`：补「实测（2026-10-29）」记录（无 annotations、`#ws_call_id` 片段、
  fetch 不可用、执行依赖模型）、把 `SEARCH_PARSERS` 里**四个**编造的函数名改成真实名字、
  S2 增加 DeepSeek 固定响应体快照、记录 searxng 的链规则。
- `docs/specs/multimodal-vision.md`：第 62 行的旧配置契约 `llm_supports_vision`（user_config/.env）
  改为按模型 `capabilities:`（`data/config.yaml`，见 ADR-0011）；第 163 行群聊视觉改成**实情**
  （会话级挂图仅 1:1，群成员只能通过 `see_screen` 工具看屏幕）——**没改代码**。
- `CONTEXT.md`：DeepSeek 搜索已实现/依赖模型/无原生抓取；原生 Gemini 图片形状已移除。
- `research` 沙箱 60s 超时**早已记录**在 `CONTEXT.md:427`，无需改动。

---

## 4. 仍未解决（按优先级）

1. **ADR-0011 与代码现在互相矛盾**：ADR 第 27 行仍写 `image_api: … | "gemini"`、第 121 行仍说
   `GeminiFormatter` 承载原生形状，而该文件已删。建议照 ADR-0006 先例在 ADR-0011 顶部加一段
   "partially superseded"（原生 Gemini 图片形状移除、gemini 搜索仍走 REST），**不要**把死代码救回来。
2. **README / `.env` 已过期**（用户要求大任务结束后再改，本批未动）：
   - `README.md:68-70` 仍把"模型能力表"标为计划；`:96` 仍写"视觉和搜索还是旧链路"；
   - `README.md:195-197` 教用户给 `vision` MCP 条目填 `VISION_API_KEY` —— **ticket 05 已删除该条目**，
     照做配的是不存在的路径；
   - `README.md:7,87` 写 625 个用例，实际 **755**（`README.en.md` 同）；
   - `.env` 视觉段注释仍写 "DeepSeek does NOT support vision"，与实测相反。
3. **代码整洁（判断项，可延后）**：`search_hits.py` 两段重复的 annotation 循环；
   `search_tools.py` 三处 `shared_llm()` 包装（含第三处 lazy import）；
   `llm_service.py` 的 `if api == ...` 级联与 `_PARSERS` 字典成了两套机制；
   `capabilities.py` 三个一行转发；`(can_see, image_api)` 参数组；
   `test_screen_tool.py:133-135` monkeypatch 被测模块私有函数（违反 CONTEXT §6"只测外部行为"）。
4. **待核（未验证，勿当结论）**：`requirements.txt` 里 `anthropic==0.45.*` 是否支持
   `_native_search_request` 使用的 `web_search_20250305` 服务端工具（该 SDK 版本可能早于该特性）。
5. **`requirements.txt` 已过时**：完全没写 `pydantic`、`mcp>=1.6.0` 等于不限版本，且无 dev 依赖、
   无"已知可用组合"。注意：在当前环境里它**写不出可满足的组合**（见 §1），只能照实记录约束。

---

## 5. 接手须知：不要做的事

- **不要** `pip install -U mcp`（会把 starlette 顶到 1.x，撞 fastapi）。
- **不要**试 `pydantic<2.12`（py3.14 无 wheel）。
- **不要**把 `image_api="gemini"` 加回 `IMAGE_APIS`，除非同时让 `sdk_type` 能是 gemini —— 否则又是个必然报错的死选项。
- **不要**把 `web_search` 直接挂给主模型（聊天请求里带 `tools`）。用户已明确决定：
  原生搜索**只作为搜索工具的后端链**，主模型不挂搜索工具 —— 否则 `SearchAgent` 会变摆设。
- **不要**改 `SEARCH_APIS`（gemini 搜索仍可达）。
- `docs/specs/*.md` **没有 `(done)` 后缀不算问题**：那是用户手动标记的动作。

---

## 6. 用户已拍板的决定

| 决定 | 内容 |
|---|---|
| 主模型挂搜索 | **不做**。原生搜索只留在搜索工具的后端链里 |
| 原生搜索默认值 | 默认**关**（`None`/未知），按模型在设置里手动开 |
| README / `.env` | 等大任务结束后再改 |
| spec 的 `(done)` 后缀 | 用户手动标记，不视为违规 |
| 环境故障 | 用户已知情；垫片方案待其点头后实施 |

---

## 7. 如何验证这批改动

```bash
cd backend
# 排除 §1 那两个环境坏掉的模块（其余应全绿）
python -m pytest tests/ -q -p no:cacheprovider \
  --ignore=tests/test_mcp_manager.py --ignore=tests/test_mcp_amap_live.py
# 期望：727 passed

# 只跑本批改到的区域
python -m pytest tests/test_search_hits.py tests/test_search_chain.py \
  tests/test_capabilities.py tests/test_image_parts.py \
  tests/test_llm_config_api.py tests/test_vision_route.py -q -p no:cacheprovider
# 期望：165 passed
```

提交前建议人工过一眼 `services/search_hits.py` 的新 parser 与 `services/llm_service.py`
新增的 `deepseek` 分支（本批最大的一块新逻辑）。
