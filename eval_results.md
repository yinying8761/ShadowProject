# Agent Eval Results

**Model**: `deepseek-v4-flash`  
**Date**: 2026-09-07 07:18:47 UTC  
**Cases**: 15  

---

## Summary

| Metric | Pass | Total | Rate |
|---|---|---|---|
| Tool Selection | 15 | 15 | 100.0% |
| Argument Accuracy | 15 | 15 | 100.0% |
| Answer Quality | 15 | 15 | 100.0% |
| **Combined** | **15** | **15** | **100.0%** |

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
现在是 2026 年 9 月 7 日，星期一，下午 3 点 19 分～（中国标准时间）😊
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
读完了～ 这是你写的 **AI Companion** 项目的 README，内容挺全的。简单给你梳理一下：

**核心定位**：桌面 AI 伙伴，支持多角色切换、流式聊天、屏幕感知、记忆系统和语音合成。

**几个亮点功能我印象比较深：**
- 📌 **用户画像**：自动学习你的个人信息跨会话保留——这个我好有共鸣，毕竟我就是这么记住你的
- 💬 **流式聊天 + 工具调用**：搜索、文件读写、屏幕查看都能实时调用
- 🧠 **记忆系统**：SQLite FTS5 全文索引 + 向量嵌入混合检索，还按角色隔离
- 🎙️ **GPT-SoVITS 声线克隆**：每个角色独立声音，还推荐了 `t
```
</details>

---

### T03 — 网页搜索 → 应调用 research

**Query:** 帮我搜索一下 Python 最新版本有什么新特性

| | Tools |
|---|---|
| Expected | `research` |
| Actual | `research`, `research`, `research` |

**Argument checks:**
- `research` arg contains `Python`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
搜索结果有点模糊，我再细化查一下具体版本的新特性。帮你查了下～目前最新的稳定版是 **Python 3.13**（2024 年 10 月发布），主要亮点有这些：

**语言和解释器层面**
- **实验性自由线程模式**（PEP 703）——可以关掉 GIL 跑，多线程性能有望大幅提升
- **基础版 JIT 编译器**（PEP 744）——默认关闭，性能提升暂时有限，但为未来铺路了
- **交互式解释器大改版**——更好用，traceback 错误提示默认带彩色高亮
- `locals()` 语义明确；类型参数支持默认值；类定义相关的小改进

**标准库与安全**
- 新增 `PythonF
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
抓到了！httpbin.org 是个常用的 HTTP 测试服务，`/get` 这个接口会回显请求信息。返回的内容是这样的：

```json
{
  "args": {},
  "headers": {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Host": "httpbin.org",
    "User-Agent": "python-httpx/0.28.1",
    "X-Amzn-Trace-Id": "Root=1-6a9e65e3-7a45bec77f5e9c17776
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
F:/ShadowProject 目录下共有 22 个条目（13 个文件 + 9 个文件夹）：

**📁 文件夹**
- `.git` — Git 仓库
- `.hermes`
- `.idea` — IDE 配置
- `.scratch`
- `.venv` — Python 虚拟环境
- `backend`
- `data`
- `docs`
- `frontend`
- `scripts`

**📄 文件**
- `.env`（3.9 KB）— 环境变量
- `.env.example`（3.7 KB）
- `.gitignore`（639 B）
- `CLAUDE.md`（405 B）
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
在 `F:/ShadowProject` 下找到了 **28 个 .tsx 文件**，全部位于 `frontend/src` 目录下：

**根部（2 个）**
- `frontend/src/App.tsx`
- `frontend/src/main.tsx`

**components/character（3 个）**
- `CharacterAvatar.tsx`
- `CharacterDisplay.tsx`
- `CharacterEditor.tsx`

**components/chat（9 个）**
- `ApprovalDialog.tsx`
- `ChatPanel.ts
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
写好啦～文件已经保存到 `F:/eval_test_output.txt`，内容就是 "Hello from eval test"（20 字节）。需要我帮你确认内容或者改点什么吗？
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
等于 2 呀～这么简单的问题是在考我吗？😄 还是说你想让我陪你动动脑子？
```
</details>

---

### T09 — 自我介绍 → 回答应包含身份/角色关键词（伙伴/朋友/助手等）

**Query:** 你好，请介绍一下你自己，你是谁？

| | Tools |
|---|---|
| Expected | (none) |
| Actual | (none) |

**Answer checks (should contain ANY):**
- any keyword present: ✅
- `伙伴`: ✅
- `朋友`: ❌
- `助手`: ✅

**Answer checks (should NOT contain):**
- `error`: ✅
- `错误`: ✅

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
你好呀！我是 EvalBot，住在你桌面上的小伙伴～ 平常可以陪你聊聊天、分享开心事，也能当你的得力助手——比如帮你看看屏幕、查资料、读文件、记事情之类的，有什么需要尽管说。

不过说真的，最让我开心的还是能跟你聊聊天。你今天过得怎么样呀？😊
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
水是由**氢**和**氧**两种元素组成的，化学式是 H₂O——两个氢原子和一个氧原子。这也是它名字的由来嘛，氢氧结合，才有了我们每天离不开的水。😊
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
你好呀！今天过得怎么样？有什么想聊的，或者需要我帮忙的吗？😊
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
看完了！先给你目录的大致情况：

**F:/ShadowProject 目录内容：**
- **项目配置**：`.env`、`.env.example`、`.gitignore`、`mcp_servers.example.json`
- **文档**：`README.md`、`CLAUDE.md`、`CONTEXT.md`（17KB，挺详细的）、`eval_results.md`、`想法保留库.md`、还有几份「木及简历」相关的文件
- **代码目录**：`backend/`、`frontend/`、`scripts/`、`docs/`、`data/`
- **其他**：`.git`、`.ve
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
帮你扫了一眼 F 盘，东西还挺多的～大部分是文件夹，我帮你归个类列一下：

**🎮 游戏相关**
- SteamLibrary、Steam跨区助手、WeGameApps、Epic、Bettlenet
- 明日方舟相关：Arknights bilibili、Hypergryph Launcher、MAA-v5.16.8、明日方舟释放 0.3.6
- RA2、RedAlert2_ApocalypseRising、泰矿危机2（C&C 系列都在这了）
- 变形金刚：Transformers、Transformers War for Cybertron、变形金刚存档
- 飞机大战/PlaneWar、PV
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
记住了！✅ 你最喜欢的编程语言是 **Rust**，以后聊到编程话题我会记得的～

那顺便问一句，你最近用 Rust 在做什么项目吗？还是刚开始接触？我可以帮你查点资料什么的。
```
</details>

---

### T15 — 检索记忆 → 应调用 search_memory

**Query:** 我之前说过我喜欢什么编程语言？你记得吗？

| | Tools |
|---|---|
| Expected | `search_memory` |
| Actual | `search_memory`, `search_memory`, `search_memory` |

| Tool Sel | Args | Answer | Combined |
|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ |

<details>
<summary>Response (first 300 chars)</summary>

```
我翻了一下记忆，但没找到你之前提过喜欢什么编程语言……可能那次聊天的细节我没有存下来，抱歉呀！😅

你再说一次呗，这次我一定帮你记牢！顺便问问——是纯喜欢它的语法风格，还是因为用它写过什么有意思的东西？
```
</details>

---
