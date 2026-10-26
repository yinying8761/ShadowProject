# Agent Eval v2：复杂场景 + 群聊不变量

> 日期：2026-10-22 | 状态：**待实施** | 关联：`docs/specs/agent-eval(done).md`、`docs/adr/0009-concurrent-group-turns.md`

## Problem Statement

现有评测是 15 条**单轮**用例（`backend/eval/test_cases.py`），覆盖工具选择、参数、回答质量、边界、记忆五类，跑法是「一条 query → 一次 Agent.run → 比对工具名/参数/关键词」，报告写 `eval_results.md`。

它对现在的产品已经不够用：

1. **全是单轮**：多轮上下文、指代消解（「它」「那张图」）、话题切换都没测——而这正是角色聊天的主场景。
2. **没有失败恢复**：工具报错、超时、熔断、审批拒绝之后 Agent 是否优雅降级，一条都没测。
3. **新增能力没有评测**：视觉（`multimodal-vision.md`）、原生搜索（`native-web-search.md`）、群聊（ADR-0009）全在评测盲区。
4. **群聊根本无法用「精确比对」评**：并发 + 多人 + 非确定，比对文本必然 flaky。它该测的是**不变量**（每成员 ≤ K 轮、全局 ≤ M 条、`<silent>` 不落库、序号单调……）。
5. **报告没有基线**：改完 prompt 或换模型，只能看到「今天 73%」，不知道比上次好还是坏。
6. **没有成本列**：群聊一个用户轮最多 N×K 次调用，评测跑一次花多少钱目前无从得知。

## Solution

把评测拆成**两类互不干扰的东西**：

- **行为类**（需要真模型）：单轮 + 多轮 + 失败恢复 + 视觉/搜索质量，仍用「期望 vs 实际」比对，但期望从「精确工具名」放宽到「工具集合 + 关键参数 + 回答要点 + 必须出现的来源」。
- **不变量类**（不需要真模型）：群聊的调度语义用一个**脚本化假模型**跑，断言 ADR-0009 写死的那些上界与顺序，快、确定、零 token。

再补三件工程化的事：**基线对比**、**成本列**、**Runner 核心纯函数化并用 pytest 锁住**。

## User Stories

1. 作为开发者，我想一条命令跑完全部行为类用例，并看到按类别分组的准确率。
2. 作为开发者，我想知道这次结果与上次的差异（哪些用例从过变挂、从挂变过）。
3. 作为开发者，我想让群聊不变量用例**秒级完成且不花钱**，能进日常回归。
4. 作为开发者，我想看到每条用例消耗的模型调用数与 token，从而知道评测本身值多少钱。
5. 作为开发者，我想加一条用例只写数据（query / 期望 / 类别），不用改 runner。
6. 作为开发者，我想在多轮用例里断言「第二轮是否正确引用了第一轮的信息」。
7. 作为开发者，我想在换模型后重跑，并直接对比两次基线。
8. 作为开发者，我想让「工具失败后是否降级」这类用例用可控的假工具注入错误，而不是依赖真实网络抖动。
9. 作为开发者，我想让贵/慢的用例（群聊真实并发、真实搜索）显式开关，不被默认跑。

## Implementation Decisions

### EvalCase 扩展

```python
@dataclass
class EvalCase:
    id: str
    kind: Literal["single_turn", "multi_turn", "recovery", "invariant"]
    category: str                  # tool_selection|argument|answer|edge|memory|vision|search|multi_turn|recovery|group
    turns: list[str] = []          # multi_turn: 依次发送的用户消息；single_turn 取 turns[0]
    expected_tools: list[str] = [] # 集合比较（内容，不要求顺序，除非 kind==single_turn 且显式 ordered)
    forbidden_tools: list[str] = []
    expected_args_contain: dict[str, list[str]] = {}
    answer_should_contain: list[str] = []
    answer_should_contain_any: list[str] = []
    answer_should_not_contain: list[str] = []
    must_cite: bool = False        # search: 回答里必须出现来源 URL
    invariants: list[str] = []     # invariant: 注册在 invariant registry 里的名字
    max_model_calls: int | None = None
    live: bool = False             # 需要真实外网/真实群聊并发，默认不跑
```

### 不变量注册表（群聊）

`backend/eval/invariants.py`：名字 → 断言函数，输入是「一次群聊用户轮的事件流 + 落库结果」。至少覆盖 ADR-0009 的承诺：

| 名字 | 断言 |
|---|---|
| `all_members_start` | 用户消息后，每个成员都至少有一次思考轮 |
| `per_member_rounds` | 每成员思考轮 ≤ K（N≤4→3，N>4→2） |
| `global_messages` | 落库的角色消息 ≤ M=9 |
| `silent_not_persisted` | `<silent>` 不落库、不推送 |
| `sequence_monotonic` | 落库消息序号严格递增，且与推送顺序一致 |
| `wake_excludes_speaker` | 新消息不唤醒说话者本人 |
| `new_turn_resets` | 新用户消息 → K/M 重置且全员被唤醒 |
| `no_cancel_in_flight` | 在途轮次从不被取消（以开始/结束事件配对验证） |

- 用**脚本化假模型**驱动：每个成员按预设脚本返回文本或 `<silent>`，调度用真实的 `GroupTurnOrchestrator`，只是 `speak` 换成脚本。这样测的是**调度语义**，不是模型。
- 用例示例：3 人、全员首轮说话、次轮互唤醒、第 4 条触发 M 上界；4 人、其中两人沉默；用户插话重置。

### 行为类用例扩容

- **multi_turn（≥6 条）**：代词指代（「它」→ 上一轮提到的文件）、纠正（「不是这个，是另一个」）、跨轮工具链（先读再改）。
- **recovery（≥5 条）**：工具抛错 → 换工具/如实报告；审批拒绝 → 不重试同一调用；熔断打开 → 明确告知；搜索全失败 → 不编造；视觉不可用 → 说「没看成」。
- **vision（≥2 条，live）**：给定图片，回答必须基于图中内容；不可用时必须承认失败。
- **search（≥3 条，live）**：必须带来源 URL；答案不得与来源冲突；无网络吿失败而非幻觉。

### 报告与基线

- `eval_results.md`（路径不变，README 引用它）新增：按类别汇总、失败用例的**最小复现**（工具调用序列 + 首尾回复片段）、每例 `model_calls`/`tokens`/`cost`。
- 每次运行写 `eval_baseline.json`（摘要 + 逐例通过位）；`--baseline <file>` 输出 diff 段（新挂/新过/仍然挂）。
- token 数从 `llm_usage` 表读（项目已有 `LLMUsageStore`），不额外埋点。

### Runner 结构

- 核心拆成纯函数：`score_case(case, events, reply)`、`render_report(results)`、`diff_baselines(old, new)`、`select_cases(categories, live)`。
- CLI 仍是 `python -m eval.runner`，新增参数：`--only <category>`、`--live`、`--baseline <path>`、`--group`、`--out <path>`。
- **修正旧决定**：原 spec 写「评测框架不依赖 pytest、不使用 mock」。现在正因为要测不变量与降级，**Runner 的纯函数必须有 pytest 覆盖**（`tests/test_eval_runner.py`），CLI 只留 I/O。真模型用例仍不进 CI。

## Testing Decisions

| Seam | 形态 | 测什么 |
|---|---|---|
| **S1 评分纯函数** | pytest | 工具集合比较、禁用工具、参数子串、must_cite、max_model_calls |
| **S2 报告渲染** | pytest + 快照 | 按类别分组、失败最小复现、成本列 |
| **S3 基线 diff** | pytest | 新挂/新过/没变；格式不兼容时给出清晰错误 |
| **S4 不变量注册表** | pytest + 脚本化假模型 | 每个不变量各一条「故意违反 → 断言红」的用例（否则不变量测试本身不可信） |
| **S5 用例选择** | pytest | `--only` / `--live` / `--group` 的组合结果 |

- 不变量用例必须**快**（<2s）且**零网络**：它们是日常回归的一部分。
- 真模型用例保持手动触发（成本 + 非确定性），报告里记录模型名与 temperature。

## Out of Scope

- **CI 集成**（真模型评测不进流水线；不变量用例可以，另开票）。
- **LLM-as-judge 自动评分**（主观质量仍用关键词/要点人工标注）。
- **RAG recall@K / 记忆量化评测**。
- **性能/延迟基准**。
- **前端 E2E**。
- **每个 provider 的横向对比榜**（可在有了基线机制后再做）。

## Further Notes

- **为什么群聊用假模型**：要验证的是「调度是否守规矩」，不是「角色说得好不好」。真模型下这些上界本来就靠 ADR-0009 保证，用真模型跑只会引入 flaky 且每次烧 N×K 次调用。
- **代价**：行为类用例从 15 条涨到 ~25 条，一次全跑约 40–60 次模型调用；`live` 项另计。建议默认只跑非 live。
- **与另两份 spec 的顺序**：视觉/搜索的用例应在那两项落地后再加（否则评的是旧实现）。本 spec 先落地框架 + 多轮 + recovery + 群聊不变量，视觉/搜索用例随各自 spec 补。
