# 04 — Eval 框架 v2：用例结构、纯函数化 runner、报告与基线

**What to build:** 把评测框架升级成能承载多轮、失败恢复、不变量三类用例的底座：用例结构可描述多轮与不变量，runner 核心纯函数化并有测试，报告带按类别汇总、每例调用数与 token、失败最小复现，并支持与上一次基线的 diff。

**Blocked by:** None — 可立即开始。

**Status:** ready-for-agent

**参考:** docs/specs/agent-eval-v2.md

- [ ] 默认命令仍能跑完现有单轮用例并写出结果文件
- [ ] 报告含按类别汇总、每例模型调用数与 token、失败用例的最小复现信息
- [ ] 基线 diff 能标出新挂 / 新过 / 未变
- [ ] 评分、报告渲染、基线 diff、用例选择是纯函数并有 pytest 覆盖（修正旧 spec 的「不依赖 pytest」）
- [ ] 类别 / live / 群聊开关可用，默认不跑贵用例
