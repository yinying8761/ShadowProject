# ShadowProject

[English](README.en.md) | 中文

桌面 AI 伙伴。常驻桌面，能读写文件、联网搜索、看屏幕、跨会话记住你说过的事，支持多角色和群聊。

后端 83 个 Python 模块、11,720 行（不含测试），前端 Electron + React + TypeScript。625 个 pytest 用例（619 通过 + 6 个需显式开启的 live 用例），11 篇 ADR，30 篇 spec。用例数随开发持续变动，这里只给一个量级。

## 设计要点

### 工具调用与审批

模型不能直接执行任何东西。一次工具调用走完整条链路：

```
装配上下文 → 流式调用 → 解析工具请求 → 用户审批 → 异步执行 → 结果回环
```

审批是串行闸门，同一时刻只放行一个，多角色并发时排队出现，超时或连接断开按拒绝处理。内置 10 个工具（文件读写、列目录、按文件名搜索、时间、屏幕、抓网页、联网检索、记忆查询与保存）；MCP 外部工具以 stdio / SSE 两种传输接入，工具名加 `mcp__{server}__{tool}` 前缀做隔离。长程搜索交给 SearchAgent 子智能体，主循环最多 5 轮链式调用。

### 可靠性

工具执行统一经过参数校验、熔断、退避重试、超时沙箱：

- 参数不符合 JSON Schema 时不直接报错，返回一段说明让模型自己改参数重试。
- 重试按错误分类，429 / 5xx 可以重试，参数错误不重试；能否重放看操作是否幂等。
- 熔断是 CLOSED / OPEN / HALF_OPEN 状态机，连续失败后隔离该工具，降级和恢复行为都有测试。

### 群聊并发

多个角色同时思考。设计写在 [ADR-0009](docs/adr/0009-concurrent-group-turns.md)，几处不太直观的地方：

- 并发到达的顺序和发言顺序不一致，数据库时间戳精度又不足以区分先后，于是引入会话内单调序号，读路径全部改按序号排序。
- 唤醒时排除说话者本人，否则模型会接着自己刚说的话继续说。
- 成本用两道上限约束（每成员思考轮数、全局消息条数），额度在发起前预留，避免生成完再丢弃。
- 在途生成不取消，每轮重读整段对话，思考期间落库的消息会进入下一轮上下文。
- 调度状态只由父任务改写、子任务只返回文本，所以并发下没有加锁。

### 可观测

token 消耗按会话、角色、模型计量，工具调用全量 tracing 落表。日志走自建的枢纽：劫持 stdout 做 tee，进内存环形缓冲，同时写轮转文件并实时推给前端（[ADR-0002](docs/adr/0002-stdout-log-hub.md)）。

## 功能

- **长期记忆**：SQLite FTS5 全文索引加向量嵌入双路召回，RRF 融合排序。记忆自动提取、去重、按时间衰减、按角色隔离。
- **多 provider**：Anthropic 原生 SDK 与 OpenAI 兼容协议统一到同一套流式接口，各家消息格式差异收敛在 `MessageFormatter` 里。
- **语音**：GPT-SoVITS 零样本克隆，每个角色独立音色。
- **主动陪伴**：空闲检测加屏幕指纹去重（dHash，Hamming ≤ 10 视为同一屏），结合时间、天气、位置发起话题。
- **屏幕感知**：截图不落盘、不落库，只存在于当轮请求的内存里。

## 界面预览

| Token 用量统计 | 多供应商模型配置 | 主动陪伴 |
|:---:|:---:|:---:|
| ![Token 用量统计](docs/screenshots/usage.png) | ![模型配置](docs/screenshots/model-config.png) | ![主动陪伴](docs/screenshots/proactive.png) |

## 开发状态

| 能力 | 状态 | 说明 |
|---|---|---|
| 工具循环与审批门控 | 已实现 | 10 个内置工具 + MCP 扩展，最多 5 轮链式 |
| 可靠性流水线 | 已实现 | 校验、熔断、重试、超时；参数错误由模型自纠 |
| 群聊并发编排 | 已实现 | 事件唤醒、两道上限、单调序号 |
| 记忆检索 | 已实现 | FTS5 与向量双路召回，RRF 融合 |
| 可观测 | 已实现 | token 计量、工具 tracing、日志枢纽 |
| 评测 v1 | 已实现 | 15 条单轮用例，自动跑分 |
| 声线克隆、主动陪伴、屏幕感知 | 已实现 | GPT-SoVITS、dHash 去重、内存截图 |
| 模型能力表 | 计划 | [ADR-0011](docs/adr/0011-provider-capability-matrix.md)，已拆票 #61–#69；视觉与搜索两条链路待接入 |
| 主模型直接看屏幕 | 计划 | [spec](docs/specs/multimodal-vision.md) |
| 原生 web search | 计划 | [spec](docs/specs/native-web-search.md) |
| 评测 v2 | 计划 | [spec](docs/specs/agent-eval-v2.md) |

### 三项改造

**视觉**。现在是截图交给另一个视觉模型写一段文字，再把这段文字塞进对话，主模型始终没看到图。信息有损，每次多一次调用，而且依赖的那几个视觉模型已经停用了。改造后图片作为内容块随当轮请求发出，主模型直接看图，同时删掉一段为单个 MCP server 写的私有适配，算是 [ADR-0010](docs/adr/0010-mcp-server-isolation.md) 的收尾。

**搜索**。现在是自己抓 HTML（ddgs 元搜索 / bing 结果页抓取 / searxng / Azure Bing），反爬、被墙、页面改版都会让整块能力失效，也拿不到可靠引用。改为 provider 原生搜索排第一顺位，本地抓取全部保留为降级。实现上按 `search_api` 分叉，不按厂商名写 if。

**评测**。行为类用例（单轮、多轮、失败恢复、视觉、搜索）用真模型比对；群聊这类非确定系统改用不变量断言，覆盖每成员轮数、全局条数、`<silent>` 不落库、序号单调等 8 条，用脚本化假模型驱动，秒级完成、不消耗 token。再加基线 diff 和成本列，否则改完 prompt 只看到「今天 73%」，不知道比上次好还是坏。

三项共用一张模型级能力表（`supports_vision` / `image_api` / `native_search` / `search_api`）。这里有一处刻意的不对称：不认识的 `image_api` 必须报错，用户明确要求看屏幕，静默丢图不可接受；不认识的 `search_api` 则降级，搜索是可选增强，不该因为配置缺项打断对话。

## 工程方法

- 每个非平凡决策写 ADR（现有 11 篇），记录备选方案、取舍和代价。
- 新功能先写 spec 再拆 ticket（30 篇 spec 和 `docs/Tickets/`），实现完在文件名上标 `(done)`。
- 关键行为用测试固定（当前 625 个用例，含 6 个需显式开启的 live 用例）。不变量类测试会先构造违规场景，确认断言本身能红。用例数随开发持续变动，不保证与仓库实时一致。
- 大改前后写专项审查，放在 `docs/analysis/`，群聊并发那轮的三份合计约 90KB。
- 项目采用 AI 辅助开发（Claude Code / Codex / DeepSeek Harness），架构、接口契约、测试设计与代码评审由我负责。后端加前端源码约 1.6 万行代码里，AI 生成的占比不低，上面这些 ADR、spec 和测试就是让它稳定下来的约束。

## 局限

- 只做了 Windows。屏幕采集用 `mss`，窗口排除依赖 `WDA_EXCLUDEFROMCAPTURE`，浮动窗是 Electron 主进程逻辑。
- 单机单用户，没有账号体系。审批是运行时约束，不是权限系统。
- 前端组件代码的 AI 参与度高于后端，我负责界面需求、接口契约和验收。
- 视觉和搜索还是旧链路，见上节。
- 评测只有 15 条单轮用例，覆盖偏窄，平时只在功能更新后重跑。
- 没有 CI。真模型评测有成本也有随机性，只有不变量用例适合进流水线。
- 语音依赖本地部署 GPT-SoVITS，不是开箱即用。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Electron + React 18 + TypeScript + Tailwind CSS + Zustand |
| 后端 | Python 3.10+ / FastAPI + WebSocket + SQLAlchemy + aiosqlite |
| AI | Anthropic / OpenAI / DeepSeek / Gemini / 通义 / 智谱 / Moonshot 多 provider |
| 检索 | SQLite FTS5 + 向量嵌入，RRF 融合 |
| 语音 | GPT-SoVITS 零样本声线克隆 |

## 项目结构

```
├── backend/
│   ├── main.py              # FastAPI 入口、工具注册、MCP 初始化
│   ├── core/                # Agent 主循环、子智能体、工具注册表与运行时、熔断
│   ├── services/            # LLM、视觉、记忆、群聊编排、审批门、重试、可观测
│   ├── tools/               # 文件、搜索、屏幕、记忆、时间
│   ├── api/                 # REST + WebSocket 路由（含 tool_logs）
│   ├── models/              # SQLAlchemy ORM
│   └── eval/                # 标注测试集 + 自动跑分
├── frontend/
│   ├── electron/            # 主进程、preload、浮动窗口
│   └── src/                 # components / hooks / stores / services / i18n
├── docs/
│   ├── adr/                 # 架构决策记录（11 篇）
│   ├── specs/               # 功能规格，实现完标 (done)
│   ├── analysis/            # 专项技术审查
│   └── Tickets/             # 需求拆解
├── scripts/                 # dev.bat / install.bat / setup-tts.bat 等
└── .env.example
```

## 快速开始

### 环境要求

- Windows 10/11
- Python 3.10+（推荐 3.10.6）
- Node.js 18+
- [可选] NVIDIA GPU + GPT-SoVITS（语音功能）

### 安装

```bash
scripts\install.bat
```

或手动安装：

```bash
cd backend && pip install -r requirements.txt
cd frontend && npm install
```

### 配置

```bash
cp .env.example .env
```

至少填 `LLM_API_KEY`，其余可选。完整配置项见 `.env.example`：

| 配置 | 说明 |
|---|---|
| `LLM_*` | 对话模型的 provider、model、base_url、api_key |
| `VISION_*` | 备用视觉模型，主模型不支持视觉时使用 |
| `SEARCH_BACKEND` | 本地搜索后端：duckduckgo / searxng / bing / bing_web（默认 duckduckgo） |
| `AMAP_API_KEY` | 高德 Key（位置 + 天气，免费） |
| `TTS_REF_BASE` | GPT-SoVITS 安装路径 |
| `MAX_CONTEXT_TOKENS` | 上下文窗口，`.env.example` 默认 16000（代码内默认 8000） |

### 启动

```bash
scripts\dev.bat                      # 一键启动

# 或分别启动
cd backend && python main.py         # 后端 :8722
cd frontend && npm run electron:dev  # 前端 :16173
```

### 语音（可选）

```bash
scripts\setup-tts.bat
```

自动完成克隆仓库、虚拟环境、PyTorch、依赖和预训练模型下载。模型下载失败的话，去 B 站搜 "GPT-SoVITS 整合包" 手动下载。

给角色克隆声线：准备一段 3-10 秒干净单人音频（无 BGM、无噪音），放到 GPT-SoVITS 目录并记下音频说的文本；在 GPT-SoVITS 目录启动 `runtime\python.exe api_v2.py`；把音频路径和对应文本配置给角色。推荐参数 `temperature=0.6`、`top_k=30`、`text_split_method=cut5`。

参考音频选中性、温柔语调，长度 5-8 秒最佳；日语音源也能克隆出中文声音（`prompt_lang` 设为 `ja`）。

### 屏幕感知的备用视觉

复制 `mcp_servers.example.json` 为 `data/mcp_servers.json`，在 `vision` 条目里填 `VISION_API_KEY` 即可用免费的 GLM 视觉模型（`VISION_BASE_URL` 与 `VISION_MODEL` 已预设为智谱的 OpenAI 兼容端点与 `glm-4.6v-flash`）。免费档经常返回 429（`code 1305`，访问量过大），那是上游限流，稍后重试即可，不是配置写错了。这条路为单个 MCP server 的参数名写了适配代码，视觉改造落地后会删除。

## 文档

| 目录 | 内容 |
|---|---|
| [`docs/adr/`](docs/adr) | 架构决策记录，建议从 [0009 群聊并发](docs/adr/0009-concurrent-group-turns.md) 和 [0011 能力表](docs/adr/0011-provider-capability-matrix.md) 看起 |
| [`docs/specs/`](docs/specs) | 功能规格，`(done)` 后缀表示已实现 |
| [`docs/analysis/`](docs/analysis) | 专项审查，含群聊并发三轮审查与架构复盘 |
| [`docs/Tickets/`](docs/Tickets) | 需求拆解与进度 |

## License

MIT
