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
| 测试 | **890 passed**（`96d4426`；已排除 2 个无法收集的 MCP 模块，原因见 §4。工作区有在途改动时以干净 worktree 为准） |
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

> 来源：审查者会话（`session-06ed43db…`）2026-11-06 的对账与 triage。

- **当前焦点**：eval v2 全量 **25/33**；recovery 子集已复跑（**1/7**，28 次调用 / 84,817 tokens），根因是产品行为而非评测——见缺口 1。
- **已知缺口**：
  1. **恢复能力 1/7（11-09 复跑，逐条读过实际输出）**——失败结构：**4 个"不放弃"（R01/R03/R04/R06）+ 2 个 `answer` 断言过窄（R02/R05）+ 1 个正面样例（R07）**。
     - 不放弃：同一失败工具重试到 5 轮上限（R01 `read_file`×5、R04 `research`×4、R06 换文件读到耗尽）；R03 是熔断后仍换工具硬试。**R01 还暴露第二个缺陷：5 轮耗尽后最终回答是空的**（缺"轮次用完也要收尾"）。
     - 断言过窄：R02 回「那我就不写了」、R05 回「试了几次都没抓到画面」——叙述都对，只是关键词表（`未写入`/`没有执行`、`没看成`/`看不到`）没有等价说法 → **属 eval 问题，不是产品缺陷**。
     - 修复：产品侧按工具记账 + 轮次耗尽收尾；eval 侧放宽/语义化 `answer` 断言。归类见 [`docs/analysis/eval-triage-2026-11-06.md`](analysis/eval-triage-2026-11-06.md)。**产品侧已修**：`dbf6a0e`（账本）+ `96d4426`（收尾），890 passed；待 #74 复跑确认 R01/R03/R04/R06 转绿。
  2. **用例级不稳定**：R02/R03 在 11-04 通过、11-09 失败，R07 反之；恢复类用例单跑一次不足以定论，比较时至少两遍。
  3. `--group` 免费层起步即挂起、零输出（33 条里不含群聊不变量用例）。
  4. 标 `live=False` 的用例实际在联网（违反 ADR-0012 的边界，有运行时证据）。
  5. **pydantic 2.14.1 × mcp 1.8.1 导入故障**：`mcp` 内部还在 `from pydantic._internal._typing_extra import eval_type_backport`，该符号自 pydantic 2.12 被移除 → `test_mcp_manager.py` / `test_mcp_amap_live.py` 在**收集阶段**就失败，故 §1 的测试数不含它们。**应用仍能启动**（`mcp` 在 `mcp_manager.py` 是函数内懒加载），只有真连 MCP 时才抛错。两条常规修法都被 ADR-0010 的死结堵住（`pydantic<2.12` 无 cp314 wheel；升级 `mcp` 会撞 `starlette<0.47`）。**用户已决定暂不修。**
  6. README / `.env.example` 部分描述过期，等大任务结束后统一改。
  7. **（候选，未决）** 基础设施失败应与行为失败分开记账（`not_run` / `infra_error`，不进通过率分母）——审查者的建议，会扩大判定语义，需单独立项。
  8. **【已实测确认】视觉能力表对 `deepseek-v4-pro` 撒谎**：实际模型是 `deepseek-v4-pro`（`data/config.yaml` 覆盖 `.env` 里的 flash——**配置双来源、后者静默生效，改 `.env` 可能无效**）。带合法 `image_url`、预算给足（1500）的请求明确回「我无法查看这张图片」，而预设 `supports_vision: True` 仍会发图。→ `None` 救不了（视觉规则是"未知也试一次"），只有按模型显式 `False`，或给 ADR-0011 补**模型级默认表**。⚠️ 早期用 `max_tokens=60` 得到的空串是**推理 token 吃光输出**的假象，不能当"看不见图"的证据。
  9. **`25/33` 不能当质量基线**：至少 2 条假红（R02/R05），且任何 `answer` 断言都可能同病（与 S03 的 host 精确匹配**同源**）。断言语义化落地前，它只能当"问题清单"，不能当分数。
  10. **收尾调用未计入 `llm_usage`**：轮次耗尽那次收尾请求不写用量。两个后果：① 成本列少算 ≤1 次/轮；② **评测侧**——`max_model_calls` 预算断言读的是 `llm_usage` 行数，走过收尾的回合会少算 1 次，可能造成「预算内」**假通过**（仅限「轮次耗尽/账本叫停」路径，每轮最多 1 次）。已决定暂不修（动 `usage_store` 的 round 语义风险大于收益）。将来补记的坐标：`core/agent.py::_stream_wrap_up` 留住 `usage` 事件 → `save(round_num=max_tool_rounds, …)`（0–4 是工具轮，5 空着）。**#74 复跑时：走过收尾的用例，别把 `budget` 通过当独立证据。**
  11. **离线守卫有盲区**：守卫只检查「用例声明的 `expected_tools`」。`expected_tools=None` 的用例（M01、M05、G01–G06）把工具选择交给模型，**没有任何东西能证明它们不会在真实模型下调 `fetch_url`**。要覆盖需给这些用例强制加断言——超出 #08 范围，未做，待另开票。
      **别和 §4.3 那条混淆**：`_NETWORK_TOOLS` 反向核对守卫保证的是「盲区不会扩大」（新增网络工具会红），**本条说的是「盲区存在」**——两件事，别合并成"守卫已覆盖离线边界"。
- **下一步**：~~产品侧两件~~（**已完成**：`dbf6a0e` 账本 / `96d4426` 收尾）→ **#73 断言迁移（在途）** → **#74 recovery 复跑 + 全量重跑 + 基线重建** → 能力表按模型 `False` / ADR-0011 模型级默认表 → T03 → `--group` 另开。

### 4.1 工作区里"未入库"的东西

| 文件 | 状态 | 说明 |
|---|---|---|
| `backend/eval_results.md` | 未跟踪（**故意**） | 全量报告是生成物（37 KB），每次跑都会变；triage 依据已固化在 `docs/analysis/eval-triage-2026-11-06.md`，故不入库 |

> 上一批未提交工作（`scoring.py`、`group_turn_eval.py`、ADR-0012、`eval_baseline.json`、交接快照、`.gitignore`）已随 `9bd76ed` 入库。

### 4.2 #74 复跑口径（照抄执行，别改参数）

```powershell
cd F:\ShadowProject\backend
$env:PYTHONIOENCODING='utf-8'
python -u -m eval.runner --out eval_results_74.md --baseline-out eval_baseline_74.json
```

六条纪律：① 不 `--live`；② 不 `--group`（该层起步即挂起，另案）；③ **不传 `--baseline`**（会把未运行用例报成"已删除"，产生假 diff）；④ `--baseline-out` **必须重定向**（默认会覆盖入库的 `eval_baseline.json`）——跑完删 `eval_baseline_74.json`，`eval_results_74.md` 按生成物处理、不入库；⑤ **跑前 `git status` 里 `eval_baseline.json` 必须干净**，跑后再确认它的 blob 与 HEAD 相同——不依赖谁记性好；⑥ **跑前先报预算**：`--dry-run` 的用例数 × 每例大致调用数（1 轮≈1 call，多轮/重试另计）与预计 tokens，**得到用户确认才跑**。

**报数格式**：**分子与分母分开报**，再列失败集变化。不要只给百分比——上一轮 R02/R05 是"分子不变、分母变小"，只看百分比会误读成改进。

**两种"变绿"必须分开记**（性质完全不同）：

| 类型 | 例子 | 含义 |
|---|---|---|
| **正当变绿** | #75 之后的 R01/R03/R04/R06 | 断言不再误判**正确的产品行为**（重试/替补本来就是 #71 允许的） |
| **移交判断** | #73 的 R02/R05 → `needs_review` | 判不了，交给人或评分模型，**不进分母**——不是通过 |

在 STATE 里绝不把两者合并写成"通过率上升"。

### 4.2.1 点单价格表（估算，锚点来自实测）

实测锚点：全量 84 calls / 292,538 tokens；recovery 7 条 28 calls / 84,817 tokens → **约 3.0–3.5k tokens/次调用**。

| 类别 | 用例 | 默认跑 | 估算调用 | 估算 tokens |
|---|---|---|---|---|
| tool_selection | 4（2 live） | 2 | ~2 | ~7k |
| argument_accuracy / answer_quality / edge_case | 9 | 9 | ~10 | ~34k |
| memory | 2 | 2 | ~2 | ~7k |
| multi_turn | 6 | 6 | ~15 | ~50k |
| **recovery** | 7 | 7 | **28（实测）** | **~85k（实测）** |
| group（不变量） | 6 | 需 `--group` | 0 | **0（假模型）** |
| live：search | 3 | 需 `--live` | ~5 | **~100k+**（原生搜索单次 19–28k） |
| live：vision | 2 | 需 `--live` | ~4 | ~15k |

**结论：只跑 recovery ≈ 8.5 万；全量非 live ≈ 20 万；加 live ≈ 30 万+。按需点单，别默认全量。**

**⚠️ 这是 token 规模，不是报价**：

- 成本要分三档单价——**新输入 / 缓存命中的输入 / 输出（含 reasoning）**；缓存命中的单价通常远低于新输入，输出又比输入贵一个档。
- **reasoning token 计入输出计费**；`max_tokens=60` 把输出吃光那个坑，本质就是推理吃掉了预算——报价按 **output 预算**估，比按回答长度估准。
- **我们自己的记录里没有 cached 拆分**（`llm_usage` 只有 prompt/completion/total；eval 报告只有每例 total）→ **真钱只能看服务商账单**，本表只用于判断"这次是几万还是几十万量级"。
- 同一场景连跑会吃到缓存，**"跑两遍"的成本通常低于 2×**，但仍不是免费。

### 4.3 当前阻塞：API 余额（HTTP 402）

- **`#75` 已落地**（`cbf3f67`，pytest **924 passed**）：`tools` 断言改为「必备集合 + 禁止集合 + 每工具调用次数上界」，报告三行分列。
- **评测复跑被 `HTTP 402 Insufficient Balance` 挡住**：recovery 7 条全部 `run` 失败、`calls=0` → **无效失败，不能当分母**；`20/28` 里的 7 条同理。**在此期间不得引用任何新百分比。**
- 替代 key 已查尽（两个 DeepSeek key 同值、OpenCode 401 过期、无本地 Ollama）→ **唯一出路是充值**。
- **#75 里 R01/R03/R04/R06 的"绿"是断言层 + 旧实测 trace 的复算**（单测锁定），**不是新跑出来的结果**；真实前后对比必须等充值后重跑。
- **更正一条**：T03 的旧红是**旧断言**造成的（`research`×3 + `fetch_url`×2 被精确集合判负）；新断言下同一 trace 复算为 **pass**。它剩下的唯一问题是 `live=False` 却联网，属 #76。
- **`#76` 已落地**（`e205221`，pytest **943 passed**，**零 API 调用**）：9 例进 `needs_review`（R02/R05 + R01/R03/R04/R06/R07/V02/T09，单列、不进分母）；`BASELINE_VERSION=3`（三态；旧 v2 可读并标 legacy；无法合并时**拒绝覆盖并以 2 退出**，不再静默覆盖）；已入库的 `eval_baseline.json` 经 `git diff` 核实**未动**；T03 只把 `live=True`，断言未削弱。
- **T04 是同类违规，票面漏了**：`T04` 声明 `fetch_url` 却 `live=False`，默认运行会联网 → 已开**票 08**（本地；GitHub issue 待网络恢复后补建）。
- **票 08 已落地**（`8d00fae`，pytest **948 passed**）：`T04` 归入 `live`（`expected_tools` / `expected_args_contain` 与 HEAD 字节相同），默认集 27→26；守卫测试泛化成**四条**，其中一条导入 `core.tool_runtime._NETWORK_TOOLS` 并断言测试集合覆盖它——**将来运行时新增网络工具，守卫会红**，盲区不会被静默扩大。根基线经 `git diff` 与 blob 双重核实未动。
- **口径决定（我拍的）**：R01/R03/R04/R06/R07/V02 这 7 例保留 `needs_review`——它们虽已具备可判定检查，但仍残留"有没有编造内容"的语义成分；按 ADR「语义结果只报告、不静默算通过」，不进分母。

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

