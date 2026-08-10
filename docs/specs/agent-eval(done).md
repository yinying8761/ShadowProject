# Agent Eval 评测体系 — Spec

> 状态：草稿 | 日期：2026-07-30

## Problem Statement

当前 ShadowProject 没有任何 Agent 效果评测体系。开发者无法回答以下问题：

- Agent 的工具调用准确率是多少？
- 在什么类型的 query 上 Agent 容易选错工具？
- 参数传递是否正确？
- 最终回答是否基于工具返回结果？

每次修改 prompt、切换模型、增加工具后，没有回归手段验证 Agent 行为是否退化。在面试场景中，「你的 Agent 工具调用准确率是多少」是一个必问题，没有数据支撑的回答无法令人信服。

## Solution

构建一套轻量级 Agent Eval 框架：

1. **15 条手工标注的测试用例**，覆盖工具选择、参数准确性、回答质量、边界情况、记忆操作五大类别
2. **自动化跑分脚本**，逐条通过真实 LLM API 运行，收集工具调用事件和回复文本，与期望值对比
3. **结构化报告输出**，记录每条用例的通过/失败详情和汇总准确率指标

## User Stories

1. 作为开发者，我想运行 `python -m eval.runner` 一键跑完所有评测用例，无需手动操作
2. 作为开发者，我想在切换 LLM 模型后重新跑分，对比不同模型的工具调用准确率
3. 作为开发者，我想看到每条测试用例的实际工具调用 vs 期望工具调用的对比
4. 作为开发者，我想看到汇总指标：工具选择准确率、参数准确率、答案准确率、综合准确率
5. 作为开发者，我想手动添加新的测试用例（只需在 test_cases.py 中加一条），无需修改跑分逻辑
6. 作为开发者，我想评测使用隔离的测试数据库和测试角色，不污染生产数据
7. 作为面试者，我想在 README 中引用 `eval_results.md` 的数据，证明 Agent 的可靠性
8. 作为开发者，我想测试用例覆盖「不需要工具」的场景，确保 Agent 不会乱调工具
9. 作为开发者，我想测试多工具链场景（列目录 → 读文件），验证 Agent 的连续工具调用能力
10. 作为开发者，我想测试错误处理场景（读不存在的文件），验证 Agent 能优雅降级而非崩溃

## Implementation Decisions

### 评测框架结构

新建 `backend/eval/` 模块，包含两个文件：

- **test_cases.py**：定义 `EvalCase` 数据类和 15 条测试用例
- **runner.py**：异步评测脚本，初始化测试环境 → 逐条运行 → 对比期望 → 输出报告

### EvalCase 数据结构（来自 prototype）

```python
@dataclass
class EvalCase:
    id: str                          # 唯一标识，如 "T01"
    query: str                       # 用户输入
    description: str                 # 测试目的说明
    expected_tools: list[str]        # 期望调用的工具名列表（按序）
    expected_args_contain: dict[str, list[str]]  # tool_name → 参数值应包含的子串列表
    answer_should_contain: list[str] # 最终回复应包含的关键词
    answer_should_not_contain: list[str]  # 最终回复不应包含的关键词
    category: str                    # tool_selection | argument_accuracy | answer_quality | edge_case | memory
```

### 评测隔离策略

- 使用 SQLite 内存数据库（`sqlite+aiosqlite://`），评测结束后不留下任何痕迹
- 创建专用测试角色 `EvalBot` 和测试会话，不与生产角色（小樱、灰暗）混淆
- 自动审批所有工具调用（`approval_callback` 始终返回 `True`）
- Stub 掉记忆检索（`memory_service.search`），避免 FTS5 依赖和记忆污染

### 准确率指标定义

| 指标 | 计算方式 |
|---|---|
| 工具选择准确率 | 实际调用的工具名集合 == 期望工具名集合 |
| 参数准确率 | 对于每条期望的参数子串检查，实际参数值包含该子串 |
| 答案准确率 | 回复包含所有 `answer_should_contain` 且不含任何 `answer_should_not_contain` |
| 综合准确率 | 以上三项全部通过 |

### 评测流程

1. 创建内存数据库 + 初始化表结构
2. Seed 测试角色和会话
3. 对每条 EvalCase：
   - 创建新的 Conversation（隔离上下文）
   - 调用 `Agent.run(user_message=case.query, mode="chat")`
   - 收集所有 `tool_use` 事件（工具名 + 参数）
   - 拼接所有 `token` 事件得到完整回复文本
   - 逐项对比期望值，记录通过/失败
4. 输出 `eval_results.md` 到项目根目录

### 评分规则

- 每条用例每个指标独立评分（通过/失败）
- 最终报告按 category 分组展示
- 报告包含：用例详情表 + 汇总准确率 + 失败用例分析

## Testing Decisions

### 对评测框架本身的测试

- 测试用例数据结构正确性（通过 Python 类型检查）
- Runner 的环境初始化和清理逻辑正确（通过手动运行验证）
- 评测结果的可重现性（通过固定随机种子、使用 deterministic 模型参数）

### 不与现有测试耦合

- Eval 模块独立于 `tests/` 目录
- 不使用 pytest，而是独立的 CLI 脚本
- 不依赖任何测试 fixture 或 mock

## Out of Scope

- **CI 集成**：不在此次范围。评测需要真实 LLM API 调用，每次 CI 运行不现实（成本 + 非确定性）
- **前端 E2E 测试**：仅评测后端 Agent 的工具调用链路
- **记忆系统的量化评估**：RAG recall@K 等指标需要单独的评测体系
- **性能基准测试**：不评测延迟或吞吐量
- **多轮对话评测**：仅评测单轮 query → 工具调用 → 回复

## Further Notes

- 评测结果受 LLM 非确定性影响，同一用例跑两次可能结果不同。建议在报告中标明 LLM 模型和 temperature 参数
- 当前默认使用 `.env` 中的 LLM 配置（deepseek-v4-pro），切换模型后重新跑分即可对比
- 源自 `docs/resume-analysis.md` 高优先级建议：「写 10-20 条手工标注的测试 query，记录 Agent 的工具调用是否正确、答案是否准确」
