# ShadowProject 简历分析

## 项目概览

| 维度 | 数据 |
|---|---|
| 代码量 | ~10,000+ 行（79 个源文件） |
| 前端 | Electron + React 18 + TypeScript + Tailwind CSS + Zustand |
| 后端 | Python FastAPI + WebSocket + SQLAlchemy + aiosqlite |
| AI 层 | Anthropic / OpenAI / DeepSeek / Qwen / 智谱 / Moonshot 多 provider |
| 测试 | 5 个 pytest 测试文件 |
| 提交 | 11 次 commit，功能迭代清晰 |

---

## 简历亮点提炼

### 亮点 1：全栈桌面应用架构能力

**你做了什么**：独立从零搭建了一个完整的桌面 AI 应用——Electron 主进程（窗口管理、浮动图标、截屏保护）+ React 前端（流式聊天、多角色管理、设置面板）+ Python 后端（FastAPI + WebSocket + 异步 SQLite）。

**简历写法**：
> 独立设计并实现了一款桌面 AI 陪伴应用，采用 **Electron + React + TypeScript** 前端与 **Python FastAPI + WebSocket** 后端的分离架构，支持实时流式对话、工具调用和多角色切换。

---

### 亮点 2：混合检索记忆系统（RAG）

**你做了什么**：实现了 **SQLite FTS5 全文索引 + 向量嵌入余弦相似度** 的混合检索，用 **Reciprocal Rank Fusion (RRF)** 融合两种排序，再加上重要性和时间衰减加权。做了字符级去重（CJK 友好）、自动摘要、过期记忆清理、按角色隔离。

**简历写法**：
> 设计并实现了**混合检索记忆系统（RAG）**：结合 SQLite FTS5 全文索引与向量嵌入余弦相似度，通过 **Reciprocal Rank Fusion (RRF)** 融合双路召回结果，辅以重要性加权和 recency decay 排序。支持记忆去重、自动过期清理、按角色隔离存储。

---

### 亮点 3：多 Provider LLM 抽象层

**你做了什么**：统一封装了 Anthropic 原生 SDK 和 OpenAI 兼容协议的流式调用，支持 6 个 LLM provider 的即插即用切换；额外设计了 Vision 模型的独立通道用于屏幕感知。用 formatter 模式处理不同 provider 的消息格式差异。

**简历写法**：
> 构建了**多 Provider LLM 抽象层**，统一 Anthropic 原生协议和 OpenAI 兼容协议的流式调用，支持 6 家模型服务商的即插即用切换；通过 Strategy 模式处理消息格式差异和工具调用适配。

---

### 亮点 4：Agent 工具调用循环

**你做了什么**：实现了完整的 Agent 循环——系统提示词组装 → 对话历史管理 → LLM 流式调用 → 工具调用解析 → 用户审批回调 → 工具执行 → 结果回传，最多 5 轮工具调用。工具包括：Web 搜索、文件读写、屏幕截图感知、记忆存取。

**简历写法**：
> 实现了 **Agent 工具调用循环**：LLM 流式输出中实时解析 tool_use → 用户审批门控 → 异步工具执行 → 结果注入上下文回环，支持多轮工具链式调用。内置 Web 搜索、文件操作、屏幕感知、记忆存取等工具。

---

### 亮点 5：主动陪伴系统

**你做了什么**：空闲检测 + 屏幕指纹去重（dHash）+ 时间/天气/位置感知 → 自动发起话题。有专门的 proactive watcher 和 session 管理，包含频率限制和 vision API 调用间隔控制。

**简历写法**：
> 设计了**主动陪伴引擎**：基于空闲计时 + 屏幕指纹去重 + 时间/天气/位置上下文的自动话题发起系统，包含频率控制和多模态上下文组装。

---

### 亮点 6：语音克隆集成

**你做了什么**：对接 GPT-SoVITS 神经网络声线克隆，每个角色独立语音配置，零样本克隆，角色切换自动换声线。

**简历写法**：
> 集成了 **GPT-SoVITS 零样本声线克隆**，实现角色级语音合成，支持 3-10 秒音频样本即可克隆声线，切换角色自动切换语音。

---

## 不同岗位的简历写法

### 投「全栈 / 前端工程师」

重点强调 Electron + React + TypeScript + 架构能力：

> **AI Companion — 桌面 AI 陪伴应用** | Electron, React, TypeScript, FastAPI, WebSocket
> - 独立从零搭建全栈桌面应用，前端 Electron + React 18 + TypeScript + Tailwind CSS + Zustand，后端 Python FastAPI + WebSocket + aiosqlite
> - 实现实时流式聊天，WebSocket 双向通信，支持 Markdown 渲染和流式打字效果
> - 设计浮动窗口（最小化后 64x64 圆形头像 + 呼吸灯通知），使用 WDA_EXCLUDEDFROMCAPTURE 保护用户隐私
> - 实现中英文 i18n、Electron 安全 preload 隔离、electron-builder Windows 便携打包

### 投「后端 / AI 应用工程师」

重点强调 Agent 架构、RAG、多 provider：

> **AI Companion — 桌面 AI Agent 应用** | Python, FastAPI, WebSocket, SQLite, LLM
> - 设计 Agent 工具调用循环：流式解析 tool_use → 审批门控 → 异步执行 → 上下文回环，支持多轮链式调用
> - 实现混合检索记忆系统：FTS5 全文搜索 + 向量嵌入余弦相似度 → RRF 融合 → 重要性/时间衰减重排序
> - 构建多 Provider LLM 抽象层，统一 Anthropic/OpenAI 协议，支持 6 家模型即插即用
> - 实现会话自动摘要（后台异步任务）、角色记忆隔离、Jinja2 模板系统提示词引擎

### 投「AI / LLM / Agent 方向」

重点强调 Agent、Memory、多模态、工具系统：

> **AI Companion — 多模态桌面 AI Agent** | LLM, Agent, RAG, TTS, Vision
> - 实现完整的 Agent 框架：系统提示词引擎 + 对话管理 + 工具注册/分发 + 用户审批 + 多轮循环
> - 设计混合检索记忆系统（RAG）：BM25 + Embedding Cosine → RRF 融合，字符级去重，重要性衰减
> - 集成 GPT-SoVITS 零样本声线克隆和多模态屏幕感知（Vision 模型独立通道）
> - 实现主动陪伴引擎：空闲检测 + 屏幕指纹去重 + 多模态上下文组装（时间/天气/位置/记忆）

---

## 建议补充（让简历更有分量）

如果你有时间加强，以下投入产出比最高：

1. **补充测试覆盖率** — 目前只有 5 个测试文件，加上前端测试（Vitest）和后端集成测试会大大加分
2. **写 2-3 篇 ADR（架构决策记录）** — 解释为什么选 RRF 而不是单纯的向量搜索、为什么用 WebSocket 而不是 SSE
3. **加一个 CI/CD** — GitHub Actions 跑测试 + Lint，展示工程素养
4. **写一篇技术博客** — 把记忆系统或 Agent 循环的设计写成文章，面试时有东西可以深入聊

---

## 一句话总结（简历 personal project 栏）

> 独立开发的桌面 AI 陪伴应用（~1 万行代码）：Electron + React 前端，Python FastAPI 后端，实现 Agent 工具调用循环、FTS5+向量混合检索记忆系统、多 LLM Provider 抽象、GPT-SoVITS 声线克隆、主动陪伴引擎。

---

## Agent 岗位竞争力评估

### 总体定位

这个项目在 **Agent 岗位的竞争力处于中等偏上水平**——对于应届生/1-2 年经验的求职者是强加分项，对于 3 年+ Agent 方向资深岗位则需要在深度上加强。

### 分层分析

#### 有竞争力的部分 ✅

| 维度 | 评价 | 说明 |
|---|---|---|
| **Agent 循环完整性** | ★★★★☆ | 你实现了完整的「感知→推理→工具调用→结果回环」闭环，不是简单的 API 套壳。工具注册/分发/审批/多轮循环都是手写的，面试时可以深入讲 |
| **多 Provider 适配** | ★★★★☆ | Anthropic 原生 SDK 和 OpenAI 协议两套体系都接入了，比只会调 OpenAI API 的候选人强一档。Formatter 模式的设计也是加分项 |
| **混合检索 RAG** | ★★★★☆ | FTS5 + Embedding + RRF 融合 + 重要性衰减，这不是调一个向量数据库 SDK，而是自己实现了检索管线。面试官会认可你对 RAG 的理解深度 |
| **工程全栈能力** | ★★★★☆ | Electron + React + Python + WebSocket 全栈，说明你能独立交付产品，不是只会写 demo 的研究型选手 |
| **多模态整合** | ★★★☆☆ | Vision 独立通道 + 屏幕感知 + TTS 声线克隆 + 位置天气，展示了处理多模态输入输出的能力 |

#### 需要补强的部分 ⚠️

| 维度 | 当前状态 | 差距 |
|---|---|---|
| **评测/eval** | 无 | 没有 Agent 效果评测体系。你无法回答「这个 Agent 的工具调用准确率是多少？」「记忆检索的 recall@3 是多少？」——这是 Agent 岗位面试的必问题 |
| **可观测性** | 弱 | 没有 tracing、没有 token 用量统计、没有工具调用成功率 dashboard。Agent 岗位的核心关注点之一就是 debug 和监控 |
| **多 Agent 协作** | 无 | 目前是单 Agent 架构。当前市场热点是 multi-agent、agent swarm、agent-as-judge——你的项目里没有体现 |
| **MCP 协议** | requirements 里有 mcp 但项目里没看到实际集成 | MCP 是 Anthropic 主推的 Agent 工具标准协议，如果你的项目支持 MCP tool server，面试时会非常有说服力 |
| **记忆系统的量化评估** | 无 | RRF 的 k 值为什么选 60？去重 threshold 为什么是 0.85？这些都是拍脑袋的，没有做 ablation |
| **测试覆盖** | 仅 5 个后端测试，无前端测试 | Agent 岗位对可靠性要求极高，测试薄弱的项目在面试中会被追问 |
| **流式处理** | 基础的 token 转发 | 没有做 streaming 中断/重连、没有 token 级别的 tool_use 实时解析（目前是累积后解析） |

### 与市场上 Agent 项目的对比

| 层级 | 典型项目 | 你的项目位置 |
|---|---|---|
| **Tier 1：开源明星** | LangChain、AutoGPT、CrewAI、LangGraph | — |
| **Tier 2：高质量个人作品** | 有 eval + tracing + multi-agent + MCP + 博客背书的项目 | ← **你在这里，但偏下** |
| **Tier 3：Demo 级项目** | 调个 API 封装个 chat、套壳 OpenAI | 你明显高于这层 |
| **Tier 4：Tutorial 级** | 跟着教程做的 TODO app 级项目 | 你远高于这层 |

### 针对性提升建议（按优先级排序）

#### 🔴 高优先级（面试前必做，2-3 天可完成）

1. **加一个 eval 脚本**（1 天）
   - 写 10-20 条手工标注的测试 query，记录 Agent 的工具调用是否正确、答案是否准确
   - 跑一遍记录准确率，放在 README 里或单独 `eval_results.md`
   - 面试时直接说「我的 Agent 工具调用准确率是 X%，具体评测集在 eval/ 下」
   - 这直接把你跟 90% 没有 eval 的项目区分开

2. **集成 MCP 协议**（1 天）
   - 在 `ToolRegistry` 上加一个 MCP client adapter，可以连接外部的 MCP tool server
   - 不需要搞得很复杂，能让 Agent 调用一个 MCP server 提供的工具就够
   - 面试时这是巨大的话题引爆点：「我对比过自己注册的工具 vs MCP 标准协议的优劣」

3. **写一篇技术博客**（半天）
   - 就写「从零实现一个 Agent 工具调用循环」或「FTS5 + 向量混合检索实战」
   - 发在掘金/知乎/个人博客，简历里放链接
   - 面试官会提前看，等于你预先控制了面试话题

#### 🟡 中优先级（有时间就做，1 周）

4. **加 tracing/logging** 
   - 每次 Agent run 记录：用了哪些工具、每轮多少 token、有没有 error
   - 不一定要接 LangSmith，一个简单的 JSON log 就行
   - 面试被问「你怎么 debug Agent 的行为」时直接拿出 log

5. **补充 20+ 测试用例**
   - Agent 循环的边界情况：工具超时、LLM 返回格式错误、并发请求
   - Memory 检索的 recall 测试

6. **尝试 multi-agent 实验**
   - 比如加一个「反思 Agent」在每次回复前 review 一遍
   - 不需要完美，关键是有这个意识和尝试

#### 🟢 低优先级（nice to have）

7. **前端测试 + E2E**
8. **Docker 化 + CI/CD**
9. **多语言 prompt 的 ablation study**

### 面试话术建议

当面试官问「讲讲你这个项目」，按这个结构回答：

1. **一句话定位**（5 秒）：「这是一个桌面 AI Agent，核心是我从零实现的 Agent 工具调用循环和混合检索记忆系统」

2. **Agent 循环深讲**（2 分钟）：画流程——系统提示词 → LLM 流式 → 实时解析 tool_use → 审批门控 → 异步执行 → 结果回环。强调你处理了 Anthropic 和 OpenAI 两套协议的工具调用格式差异

3. **记忆系统深讲**（2 分钟）：为什么混合检索（FTS5 管关键词、Embedding 管语义）→ RRF 为什么比线性加权好 → 记忆隔离和去重怎么做的

4. **主动抛出弱点并展示思考**（1 分钟）：「这个项目目前缺少 eval 体系，我最近在补，目前的工具调用准确率大概是 X%...」

### 最终判断

| 岗位类型 | 竞争力 | 说明 |
|---|---|---|
| **大厂 Agent 校招/实习** | ⭐⭐⭐⭐ | 竞争力强。大多数校招生没有完整 Agent 项目 |
| **创业公司 Agent 工程师（1-3 年）** | ⭐⭐⭐½ | 有竞争力。能独立交付 + 理解 Agent 核心概念 |
| **大厂 Agent 社招（3 年+）** | ⭐⭐⭐ | 需要补 eval/tracing/multi-agent。但作为 side project 仍然是加分项 |
| **纯研究型 Agent 岗位** | ⭐⭐½ | 偏工程，缺理论深度和实验方法论 |

**一句话：这个项目能让你过简历关 + 撑住面试前 10 分钟的技术深聊。之后能不能持续加分，取决于你是否补上 eval 和 tracing 这两个缺口。**
