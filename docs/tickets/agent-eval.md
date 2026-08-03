# Tickets — Agent Eval 评测体系

基于 spec: `docs/specs/agent-eval.md`

---

## #1 — Eval 框架 + 测试用例

**What to build:** 新建 `backend/eval/` 模块。`test_cases.py` 定义 `EvalCase` 数据结构和 15 条手工标注用例（覆盖工具选择/参数准确性/回答质量/边界情况/记忆操作五个类别）。`runner.py` 实现跑分脚本：内存 SQLite 建表 → 创建隔离测试角色和会话 → 对每条用例调用 `Agent.run()` 并 auto-approve 所有工具 → 收集 tool_use 事件和完整回复文本 → 逐条对比期望值（工具名/参数子串/回复关键词）→ 输出 `eval_results.md`。Stub 记忆检索避免 FTS5 依赖和记忆污染。

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] `backend/eval/test_cases.py` — `EvalCase` 数据类 + 15 条手工标注用例，按 category 分组
- [ ] `backend/eval/runner.py` — 跑分脚本：内存 DB、seed 测试角色、auto-approve 工具、逐条运行收集事件、对比期望、输出汇总
- [ ] 隔离策略：测试角色 EvalBot + 独立 Conversation + Stub 记忆检索
- [ ] 每条用例记录：实际工具名列表、实际参数、完整回复文本、各维度通过/失败
- [ ] 报告包含：用例详情表（含期望 vs 实际对比）+ 四维准确率汇总
- [ ] 报告末尾标注 LLM 模型版本和跑分时间戳

---

## #2 — 实际跑分 + 报告提交

**What to build:** 运行 `python -m eval.runner` 通过真实 LLM API 跑完 15 条用例（当前使用 deepseek-v4-pro，temperature=0.7），生产环境不做假。将结果写入项目根目录的 `eval_results.md`，提交到仓库。可选在 README 中加一行指向评测结果。

**Blocked by:** #1 — Eval 框架 + 测试用例

**Status:** ready-for-agent

- [ ] `python -m eval.runner` 跑完 15 条用例，所有用例通过 LLM 实际调用（非 mock）
- [ ] `eval_results.md` 存在于项目根目录，含每条用例的通过/失败详情和四维准确率
- [ ] 报告标注 LLM 模型（deepseek-v4-pro）和跑分日期
- [ ] `eval_results.md` 提交到仓库
