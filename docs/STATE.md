# STATE.md — 现在这个仓库有什么

> **这份文件只回答三个问题**：我现在有什么（§2）、最近做到哪了（§3）、下一步是什么（§4）。
> 它不是架构文档（那是 `CONTEXT.md`），也不是需求文档（那是 `docs/specs/`）。
>
> **刷新时间**：2026-11-06 · **基准提交**：`7d60820`（eval 批次末）+ 本批修复 · **与 origin/master**：本文件提交后同步（以 `git log -1` 为准）
>
> **维护约定**：§1/§3/§5 是机械事实，每次提交前重跑 §6 的命令就能更新；§4 是人工段，只在焦点变化时改。

---

## 1. 一屏现状

| 项 | 值 |
|---|---|
| 后端规模 | **97** 个 Python 模块 / **13,970** 行代码（不含 tests / venv） |
| 测试 | **866 passed**（`HEAD 7d60820` + 3 处未提交改动；已排除 2 个无法收集的 MCP 模块，原因见 §4） |
| 决策记录 | **12** 篇 ADR（`0001`–`0012`） |
| 规格 | **30** 篇 spec，其中 **27** 篇已 `(done)` |
| 需求拆分 | **15** 个 ticket 目录（`docs/Tickets/`） |
| 前端 | Electron 33 + React 18 + TypeScript + Tailwind + Zustand |
| 未推送 | 0（提交本文件时，eval/视觉/搜索那 9 个提交已一并推送，见 §3） |

---

## 2. 子系统地图（"我做过什么"的入口）

> 一行一个子系统。**入口**是你会想去翻的那个文件；**决策**是"当初为什么这样"的那篇文章。

| 子系统 | 一句话 | 入口 | 决策 / 规格 |
|---|---|---|---|
| Agent 主循环 | 装配上下文 → 流式调用 → 解析工具 → 审批 → 执行 → 回环，最多 5 轮 | `core/agent.py` | spec `tool-runtime` |
| 工具运行时 | 参数校验、熔断、退避重试、超时沙箱；错误统一成信封 | `core/tool_runtime.py`、`core/tool_registry.py` | spec `error-recovery` |
| 审批 | 串行闸门，同一时刻一个；超时/断连按拒绝 | `services/approval_gate.py`、`api/chat.py` | spec `error-recovery` |
| 对话记录渲染 | 唯一的时间戳+说话人渲染器；排序只走 `chronological_order()` | `core/transcript.py`、`models/message.py` | ADR-0009 |
| 群聊并发 | 全员起跑、事件唤醒、每成员 K 轮 + 全局 M=9、单调序号 | `core/group_turn.py`、`services/group_chat.py` | **ADR-0006 / ADR-0009** |
| 群记忆补账 | 打开群对话时"先提取→后 compact"，跨日按窗口补 | `services/group_memory.py` | ADR-0008 |
| 能力表 | 模型级能力（视觉/原生搜索）单一来源，业务代码不分厂商 | `services/capabilities.py`、`config.LLM_PRESETS` | **ADR-0011** |
| 视觉（看屏幕） | 截图作为图片块进当轮请求，主模型自己看；不落盘不落库 | `services/vision_turn.py`、`vision_tool.py`、`tools/screen_tools.py` | spec `multimodal-vision` |
| 搜索 | provider 原生搜索优先，本地抓取自动降级；按 `search_api` 分叉 | `services/search_backends.py`、`search_hits.py`、`core/search_agent.py` | ADR-0003 + spec `native-web-search` |
| 记忆 | FTS5 + 向量双路召回、RRF 融合、按角色隔离 | `services/memory_store.py`、`memory_retriever.py`、`embedding_service.py` | spec `memory-isolation` |
| 评测 | 行为类真模型比对 + 群聊不变量 + 基线 diff + 成本列 | `eval/runner.py`、`scoring.py`、`group_turn_eval.py`、`baseline.py` | **ADR-0012** + spec `agent-eval-v2` |
| MCP 接入 | 每个 server 一个 actor 任务，坏的不拖垮别的 | `services/mcp_manager.py` | **ADR-0010** |
| 可观测 | token 计量、工具 tracing、日志枢纽（stdout tee + 环形缓冲 + 轮转） | `services/usage_store.py`、`tool_trace_store.py`、`log_hub.py` | ADR-0002 |
| 主动陪伴 | 空闲检测 + 屏幕指纹去重（dHash，Hamming ≤10） | `services/proactive_session.py`、`proactive_watcher.py` | — |
| 语音 | GPT-SoVITS 零样本克隆，每角色独立音色 | `services/tts_service.py` | — |
| REST / WS | 聊天流、历史、群 CRUD、工具日志 | `api/chat.py`、`conversation.py`、`group.py`、`tool_logs.py` | `CONTEXT.md` §5 |

---

## 3. 最近这批做了什么（9 个未推送提交）

| 提交 | 内容 |
|---|---|
| `a65b0be` | 模型级能力表（ticket 01 / ADR-0011）——**已推送** |
| `61fe8ef` | 视觉：截图进入当轮对话，主模型自己看（02） |
| `c00a4ed` | `see_screen` 走主模型 + 退役 MCP 视觉适配（05） |
| `5f857a9` | 搜索链：原生搜索进首位、本地自动降级（03） |
| `c1521e1` | 原生抓取 + Gemini 原生搜索分支（06） |
| `75904e2` | 能力表不撒谎 + DeepSeek 解析器 + 删掉不可达的 gemini 图片形状 |
| `8b77e3c` | 评测框架 v2：用例结构 / 纯函数 runner / 报告与基线（04） |
| `2a49128` | 多轮与失败恢复用例（07） |
| `5722e5e` | 群聊不变量：假模型、秒级、零 token（08） |
| `7d60820` | 视觉/搜索 live 用例（09） |
| 本批 | `fix(eval)`：`Sequence` 导入 + 群聊排序键 + ADR-0012；`docs`：本文件 + 交接快照 |

> 结论：**ticket 01–09 全部落地**，9 张票对应的能力都在仓库里，只是还没推出去。

---

## 4. 当前焦点 / 已知缺口 / 下一步（人工段）

> 来源：审查者会话（`session-06ed43db…`）2026-11-06 对账，三句原样引用。

- **当前焦点**：eval v2 全量已跑通（**25/33**，84 次模型调用 / 292,538 tokens；失败项 `T03/R01/R04/R05/R06/R07/V01/S03`），正在 triage 6 个 recovery 失败与 `--group` 挂起。
- **已知缺口**：
  1. `--group` 免费层起步即挂起、零输出；
  2. 7 个 recovery 用例挂了 6 个，尚未归因；
  3. 标 `live=False` 的用例实际在联网（违反 ADR-0012 的边界，有运行时证据）；
  4. **pydantic 2.14.1 × mcp 1.8.1 导入故障**：`mcp` 内部还在 `from pydantic._internal._typing_extra import eval_type_backport`，该符号自 pydantic 2.12 被移除 → `test_mcp_manager.py` / `test_mcp_amap_live.py` 在**收集阶段**就失败，故 §1 的测试数不含它们。**应用仍能启动**（`mcp` 在 `mcp_manager.py` 是函数内懒加载），只有真连 MCP 时才抛错。两条常规修法都被 ADR-0010 的死结堵住（`pydantic<2.12` 无 cp314 wheel；升级 `mcp` 会撞 `starlette<0.47`）。**用户已决定暂不修。**
  5. README / `.env.example` 部分描述过期，等大任务结束后统一改。
- **下一步**：triage recovery 失败 → 修 `--group` → 提交那三处改动 + ADR-0012 → 再回填本文件。

### 4.1 工作区里"已改未提交"的东西（别丢）

| 文件 | 状态 | 说明 |
|---|---|---|
| `backend/eval/scoring.py` | 已改 | 补 `Sequence` 导入（修复） |
| `backend/eval/group_turn_eval.py` | 已改 | 手写排序键 → `*Message.chronological_order()` |
| `docs/adr/0012-eval-layering.md` | 未跟踪 | 评测分层决策（行为类 / 不变量 / live 的边界） |
| `backend/eval_results.md` | 未跟踪 | 最近一次全量报告（37 KB） |
| `eval_baseline.json` | 未跟踪（**仓库根**） | 基线；入库才有 diff 可看 |
| `.gitignore` | 已改 | 新增 `.dsh-plugins/`（DSH 插件源码） |
| `docs/analysis/vision-search-eval-status.md` | 未跟踪 | 交接快照，本文件 §4 的原始来源之一 |

> **关于 866 这个数字**：它是**当前工作区**的实测值，已包含 `scoring.py` / `group_turn_eval.py` 两处待提交修复（这两处不增删用例，所以提交后数字不变）。时点 = `HEAD 7d60820` + 3 处未提交改动；之后每提交一批都会变，按 §6.4 的约定更新。

---

## 5. 这个文件怎么刷新

```bash
# 规模与提交
git log --oneline -12
git rev-list --left-right --count origin/master...HEAD
git status --short

# 测试（跳过当前无法收集的 MCP 模块）
cd backend && python -m pytest tests/ -q \
  --ignore=tests/test_mcp_manager.py --ignore=tests/test_mcp_amap_live.py

# 计数
python - <<'PY'
from pathlib import Path
root = Path(".")
skip = {"tests","__pycache__",".venv",".venv-vision","node_modules"}
ps=[p for p in root.rglob("*.py") if not (set(p.parts)&skip)]
code=sum(1 for p in ps for l in p.read_text(encoding="utf-8",errors="replace").splitlines()
         if l.strip() and not l.strip().startswith("#"))
print("py modules:",len(ps),"code lines:",code)
print("specs:",len(list((root/"docs/specs").glob("*.md"))),
      "done:",len([p for p in (root/"docs/specs").glob("*.md") if "(done)" in p.name]))
print("adrs:",len(list((root/"docs/adr").glob("*.md"))))
PY
```

**什么时候刷**：每次推送前；或每次"好久没打开这个仓库"时。
**机器能生成的部分**：§1、§3、§5 与 §2 的表格骨架——**下一步值得写一个 `scripts/gen_state.py`**，把它变成"重跑一条命令"，而不是靠人记得改。

---

## 6. 权威与快照（谁说了算）

1. **阅读顺序**：`CONTEXT.md`（架构约定与领域词）→ 本文件（现状）→ 最近三篇 ADR（`0010` MCP 隔离、`0011` 能力表、`0012` 评测分层）。
2. **本文件是唯一入口，也是唯一权威**。细节与诊断过程见 `docs/analysis/vision-search-eval-status.md`（**当时的交接快照，非权威**；其中的"未提交工作"与测试数字以本文件为准）。`docs/analysis/` 下其余文档同理——与本文冲突时，一律以本文为准。
3. **多会话并行**：本仓库同时有别的会话在改。动笔前先 `git status`，否则容易把别人未提交的改动当成自己的成果、或把别人的 bug 当成自己的。
4. **数字怎么维护**：写代码的那个会话在提交后会把最终 commit SHA / 标题 / 测试数字发给本文件所在的会话，只改 §1 与 §4.1 的两行——避免两个人同时改同一个文件。

---

## 7. 团队与流转（为什么会有"二号实施者"）

本仓库由多个 AI 会话按固定分工协作，各自有独立的上下文：

| 角色 | 职责 | 产出 |
|---|---|---|
| **二号实施者** | 写代码、跑测试、提交 | 提交 + 通过的测试 |
| **审查者** | 复查实施者的交付；**大问题写成文档**，**小问题自己直接修** | `docs/analysis/*.md`、修复提交 |
| **文档/对账**（写本文件的会话） | 维护 STATE.md、跨会话对账、起草 spec/ADR、验收文档一致性 | `docs/STATE.md`、spec/ADR |

- **一号实施者已归档**：它跑得很久、上下文与压缩记忆过长，对生成代码的质量有顾虑，因此换成二号从干净上下文开始。归档不等于删除——需要时仍可回看它的会话转录。
- **流转**：实施者交付 → 审查者复查 → 大问题进 `docs/analysis/`（并回写本文件 §4）→ 小问题审查者直接修 → 推送。
- **为什么要记这条**：三个月后回来看，看到"二号实施者""审查者""归档的一号"会一头雾水。分工和交接规则应当和代码一起留在仓库里。

