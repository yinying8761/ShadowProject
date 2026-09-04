# Eval Answer Assertion — answer_should_contain_any（OR 语义）

> 日期：2026-08-27 | 状态：待实现

## Problem Statement

eval 的 `answer_should_contain` 是 **AND 语义**（所有关键词都必须出现）。但有些用例想表达的是"回答需包含**任一**关键词"（同义词），比如 T09「自我介绍」期望回答含「伙伴 / 朋友 / 助手」任意一个角色词，而不是三个都要出现。当前 T09 写成 `["伙伴", "朋友"]`，回复里只有"小伙伴"（含"伙伴"、无"朋友"），被误判 ❌。评测框架缺一个 OR 断言。

## Solution

给 `EvalCase` 增加一个 OR 字段 `answer_should_contain_any`（任选其一），与现有 AND 字段
`answer_should_contain` 并行。判定 = AND(contains) ∧ OR(any) ∧ NOT(not_contains)；空列表恒真。
报告渲染新增 "should contain ANY" 块。T09 改用 OR 字段。

## User Stories

1. 作为评测用例编写者，我想表达"回答需包含任一关键词"（如 伙伴/朋友/助手），so that 同义词不会导致误判。
2. 作为评测用例编写者，我想 AND 和 OR 能同时使用（如必须含"氢"+"氧"，且至少含一个角色词），so that 断言足够灵活。
3. 作为评测运行者，我希望 OR 断言的命中/未命中在 `eval_results.md` 里可见，so that 能追溯判分依据。
4. 作为评测维护者，我希望现有 AND 用例（T08 的"2"、T10 的"氢"+"氧"）行为不变，so that 改动不引入回归。

## Implementation Decisions

- `EvalCase` 新增 `answer_should_contain_any: list[str]`（默认空），与 `answer_should_contain`（AND）并列。
- 判定逻辑（决策性片段，来自设计稿）：

```
answer_pass = all(k in text for k in answer_should_contain)                 # AND
          and (not answer_should_contain_any
               or any(k in text for k in answer_should_contain_any))         # OR（空则恒真）
          and all(k not in text for k in answer_should_not_contain)          # NOT
```

- 报告渲染：新增 "Answer checks (should contain ANY)" 块，展示该组关键词整体命中与否。
- T09 改为：`answer_should_contain=[]`，`answer_should_contain_any=["伙伴","朋友","助手"]`。
- **不改** `answer_should_contain` 的 AND 语义（T10 "氢"+"氧" 仍是 AND），只新增 OR 通道。

## Testing Decisions

### 测试原则

- 只测外部行为：`_evaluate_case` 对 AND/OR/NOT 组合的 `answer_pass` 结果。
- 缝：`_evaluate_case` 是纯函数（`EvalCase` + `CaseResult` → 改 `answer_pass`），直接单测，无 LLM/DB/HTTP。
- 参照：`backend/tests/test_relative_dates.py` 的纯函数风格（当前无 eval 单测，本 spec 顺带补一个最小单测）。

### 用例

只 OR 命中、只 OR 未命中、AND+OR 组合、空 OR 恒真、NOT 仍生效、AND 未命中时 OR 命中也算失败。

## Out of Scope

- 不改 `answer_should_contain` 的 AND 语义。
- 不加其它断言类型（正则、近似匹配、数值范围）。
- **不动 `search_agent.py`**（T03 的"反复重搜"是独立问题，另行处理）。

## Further Notes

### 改动清单

| 文件 | 改动 |
|---|---|
| `backend/eval/test_cases.py` | `EvalCase` 加 `answer_should_contain_any`；T09 改用该字段 |
| `backend/eval/runner.py` | `_evaluate_case` 加 OR 判定；`_generate_report` 加 "should contain ANY" 渲染 |
| `backend/tests/test_eval_runner.py`（新建，可选） | `_evaluate_case` 纯函数单测 |

### 验证命令

```bash
cd backend && python -m pytest tests/test_eval_runner.py -v   # 若建了单测
cd backend && python -m eval.runner                           # 重跑，看 T09 转绿
```
