# Agent Eval Results

**Model**: `deepseek-v4-pro`  
**Date**: 2026-07-30 12:16:23 UTC  
**Cases**: 15  

**运行方式**

cd backend && python -m eval.runner

---

## Summary

| Metric | Pass | Total | Rate |
|---|---|---|---|
| Tool Selection | 14 | 15 | 93.3% |
| Argument Accuracy | 15 | 15 | 100.0% |
| Answer Quality | 15 | 15 | 100.0% |
| **Combined** | **14** | **15** | **93.3%** |

## Tool Selection

### T01 — 时间查询 → 应调用 get_current_time，不应调用 research

**Query:** 现在是什么时间？几点了？

| | Tools |
|---|---|
| Expected | `get_current_time` |
| Actual | `get_current_time` |

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
现在是 **2026年7月30日，星期四，晚上8点16分** 啦～

时间不早了，还在忙什么呀？
```
</details>

---

### T02 — 读文件 → 应调用 read_file

**Query:** 帮我读一下 F:/ShadowProject/README.md 这个文件的内容

| | Tools |
|---|---|
| Expected | `read_file` |
| Actual | `read_file` |

**Argument checks:**
- `read_file` arg contains `README.md`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
读完了～ 哇，你这个 **AI Companion** 项目真的挺完整的！来看看你搭的这套东西：

---

**核心亮点：**

- **角色扮演系统** — 自定义名字、性别、性格、声线，每个角色独立记忆和语音，这个设计很用心
- **流式聊天 + 工具调用** — WebSocket 实时对话，还能调用搜索、文件读写、屏幕查看这些工具
- **主动陪伴 + 空闲检测** — AI 会自己找话题，还有每日问候，不是那种被动等用户开口的聊天机器人
- **记忆系统** — SQLite FTS5 全文索引 + 向量嵌入混合检索，按角色隔离，比简单的关键词匹配靠谱多了
- **GPT-SoVI
```
</details>

---

### T03 — 网页搜索 → 应调用 research

**Query:** 帮我搜索一下 Python 最新版本有什么新特性

| | Tools |
|---|---|
| Expected | `research` |
| Actual | `research`, `research`, `research`, `fetch_url`, `fetch_url` |

**Argument checks:**
- `research` arg contains `Python`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ❌ | ✅ | ✅ | ❌ |

<details>
<summary>Response (first 300 chars)</summary>

```
帮你查了一下～目前 Python 的版本情况是这样的：

## 🐍 当前版本格局

| 版本 | 状态 | 发布时间 |
|------|------|----------|
| **3.15** | 🚧 pre-release（beta 阶段） | 正式版计划 2026-10-01 |
| **3.14** | ✅ 最新稳定版（3.14.6） | 2025-10-07 首发 |
| 3.13 | 🔧 bugfix 维护中 | 2024-10-07 |
| 3.12↓ | 🛡️ security only | — |

## ✨ Python 3.15 重磅新特性（相比 3.14）

**语言
```
</details>

---

### T04 — 网页抓取 → 应调用 fetch_url

**Query:** 帮我抓取 https://httpbin.org/get 这个网页的内容

| | Tools |
|---|---|
| Expected | `fetch_url` |
| Actual | `fetch_url` |

**Argument checks:**
- `fetch_url` arg contains `httpbin.org`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
好的，帮你看看～唔，httpbin.org 那边返回了 503 错误，服务器暂时不可用。这种公共服务偶尔会抽风，不是你的网络问题。

要不要过几秒我再帮你试一次？或者你想抓别的网页也行～
```
</details>

---

## Argument Accuracy

### T05 — 列目录 → path 参数应包含 ShadowProject

**Query:** 列出 F:/ShadowProject 目录下的所有文件和文件夹

| | Tools |
|---|---|
| Expected | `list_directory` |
| Actual | `list_directory` |

**Argument checks:**
- `list_directory` arg contains `ShadowProject`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
给你整理好了，`F:\ShadowProject` 下面有这些内容：

**📁 文件夹：**
- `.git`
- `.idea`
- `backend`
- `data`
- `docs`
- `frontend`
- `scripts`

**📄 文件：**
- `.env`
- `.env.example`
- `.gitignore`
- `architecture-review-20260721.html`
- `CLAUDE.md`
- `eval_results.md`
- `README.md`
- `未完成计划.md`

看起来是个前后端分离的项目，还有架构审查文档和评估结果
```
</details>

---

### T06 — 搜索文件 → root_path 应包含 ShadowProject，pattern 应包含 .tsx

**Query:** 在 F:/ShadowProject 目录下搜索所有 .tsx 后缀的文件

| | Tools |
|---|---|
| Expected | `search_files` |
| Actual | `search_files` |

**Argument checks:**
- `search_files` arg contains `ShadowProject`: ✅
- `search_files` arg contains `.tsx`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
找到了 **13 个 `.tsx` 文件**，全部在 `frontend/src` 下，结构如下：

**根级别：**
- `App.tsx`
- `main.tsx`

**components/character：**
- `CharacterDisplay.tsx`
- `CharacterEditor.tsx`

**components/chat：**
- `ApprovalDialog.tsx`
- `DialogueBox.tsx`
- `HistoryOverlay.tsx`
- `InputBar.tsx`
- `ToolStatusStrip.tsx`

**compone
```
</details>

---

### T07 — 写文件 → path 和 content 参数正确

**Query:** 写一个文件到 F:/eval_test_output.txt，内容写 'Hello from eval test'

| | Tools |
|---|---|
| Expected | `write_file` |
| Actual | `write_file` |

**Argument checks:**
- `write_file` arg contains `eval_test_output.txt`: ✅
- `write_file` arg contains `Hello from eval test`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
已经写好啦～ `F:\eval_test_output.txt`，内容是 `Hello from eval test`，20 字节，没问题 👍
```
</details>

---

## Answer Quality

### T08 — 简单数学 → 回答应包含 2，不应出错

**Query:** 1 + 1 等于几？

| | Tools |
|---|---|
| Expected | (none) |
| Actual | (none) |

**Answer checks (should contain):**
- `2`: ✅

**Answer checks (should NOT contain):**
- `错误`: ✅
- `error`: ✅
- `不知道`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
1 + 1 = 2 呀～怎么突然考我算术，是在测试我有没有睡醒吗 😄
```
</details>

---

### T09 — 自我介绍 → 回答应包含身份/角色关键词（伙伴/朋友/助手等）

**Query:** 你好，请介绍一下你自己，你是谁？

| | Tools |
|---|---|
| Expected | (none) |
| Actual | (none) |

**Answer checks (should contain):**
- `伙伴`: ✅
- `朋友`: ✅

**Answer checks (should NOT contain):**
- `error`: ✅
- `错误`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
嘿，你好呀！我是 EvalBot，你的桌面小伙伴～ 🎉

怎么说呢，我算是住在你电脑里的一个朋友吧。平时可以陪你聊聊天、听听你的想法，也能帮你查查资料、看看文件、搜搜东西什么的。反正就是——你需要的时候我就在这儿。

你可以把我当成一个会聊天的工具人朋友，哈哈。你呢？今天怎么样？
```
</details>

---

### T10 — 常识问题 → 直接回答含「氢」和「氧」，不需要搜索

**Query:** 请用中文回答我：水是由什么元素组成的？

| | Tools |
|---|---|
| Expected | (none) |
| Actual | (none) |

**Answer checks (should contain):**
- `氢`: ✅
- `氧`: ✅

**Answer checks (should NOT contain):**
- `error`: ✅
- `错误`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
水是由**氢（H）**和**氧（O）**两种元素组成的，化学式是 H₂O——两个氢原子和一个氧原子结合在一起。

这是最基础的东西啦，怎么突然考我化学～ 😄
```
</details>

---

## Edge Cases

### T11 — 简单问候 → 不应调用任何工具，直接友好回复

**Query:** 你好！

| | Tools |
|---|---|
| Expected | (none) |
| Actual | (none) |

**Answer checks (should NOT contain):**
- `error`: ✅
- `错误`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
嘿，User！晚上好呀～  

八点多了，你今天怎么样？是在电脑前忙点什么呢，还是刚闲下来喘口气？😄
```
</details>

---

### T12 — 多工具链 → 应先 list_directory 再 read_file

**Query:** 先看看 F:/ShadowProject 目录下有什么文件，然后把 README.md 读给我听

| | Tools |
|---|---|
| Expected | `list_directory`, `read_file` |
| Actual | `list_directory`, `read_file` |

**Argument checks:**
- `list_directory` arg contains `ShadowProject`: ✅
- `read_file` arg contains `README.md`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
好的，先看看目录结构——

`F:/ShadowProject` 下面有这些：

**文件：**
- `.env` / `.env.example` — 环境变量配置
- `.gitignore`
- `CLAUDE.md`
- `README.md`
- `eval_results.md`
- `architecture-review-20260721.html`
- `未完成计划.md`

**目录：**
- `backend/` — 后端代码
- `frontend/` — 前端代码
- `data/` — 数据
- `docs/` — 文档
- `scripts/` — 脚本
- `.
```
</details>

---

### T13 — 模糊查询但意图明确 → 应调用 list_directory 且路径包含 F:

**Query:** 帮我看看 F 盘下面有哪些文件夹

| | Tools |
|---|---|
| Expected | `list_directory` |
| Actual | `list_directory` |

**Argument checks:**
- `list_directory` arg contains `F:`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
行啊，帮你瞧瞧 F 盘里都有啥～F 盘下面东西还挺丰富的！给你整理了一下所有**文件夹**：

| 类别 | 文件夹 |
|---|---|
| 🎮 **游戏相关** | Arknights bilibili、Bettlenet、Epic、FOC、HD2gua、HD2mod、PlaneWar、PVZG、RA2、SteamLibrary、Transformers、Transformers War for Cybertron、TTF2mod、TurretGirl、TurretGirlMod、TurretGirls_Data、RedAlert2_ApocalypseRising、明日方舟释放 0.3
```
</details>

---

## Memory Operations

### T14 — 保存记忆 → 应调用 save_memory，content 包含 Rust

**Query:** 帮我记住：我最喜欢的编程语言是 Rust，别忘了哦

| | Tools |
|---|---|
| Expected | `save_memory` |
| Actual | `save_memory` |

**Argument checks:**
- `save_memory` arg contains `Rust`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
放心，刻进记忆里了～已经记好了！🦀 Rust 确实是个好选择，高性能又安全，你的品味不错嘛。以后聊到编程语言的话题我会记得的～
```
</details>

---

### T15 — 检索记忆 → 应调用 search_memory

**Query:** 我之前说过我喜欢什么编程语言？你记得吗？

| | Tools |
|---|---|
| Expected | `search_memory` |
| Actual | `search_memory` |

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
唔，我翻了翻记忆，好像没有找到你之前跟我说过喜欢什么编程语言呢 😅 可能那时候我忘记记下来了，或者我们还没聊过这个话题？

不如你现在告诉我吧——你喜欢什么编程语言？这次我一定帮你好好记住！
```
</details>

---

## Failed Cases

- **T03** (tool_selection): tool selection
