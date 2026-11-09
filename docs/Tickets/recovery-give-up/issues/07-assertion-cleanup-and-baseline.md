# 07 — 断言收尾与基线三态：T09、剩余关键词、needs_review 入基线、T03 联网

**What to build:** 把 #73 没覆盖到的断言问题一次收干净，并让基线格式与新分母口径一致：T09 的唯一实质检查仍是关键词；R01/R03/R04/R06/R07/V02 的 `answer_should_contain_any` 仍是关键词闭集；`needs_review` 在基线里被记成「未失败」，与报告的分母不一致；另外非 live 集合里的 T03 仍会真的联网，违反 ADR-0012 的边界规则。

**Blocked by:** None — 可立即开始。

**Status:** ready-for-agent

**参考:** docs/specs/recovery-give-up.md

- [ ] 没有可判定检查的用例改为 `needs_review`（单列、不进分母），不再用关键词闭集代替语义判断
- [ ] 基线支持第三态 `needs_review` 并升 `BASELINE_VERSION`，基线与报告的分母口径一致
- [ ] 标记 `live=False` 的用例不再发起真实网络请求（有测试或运行时断言证明）
- [ ] T09 的检查改为可判定性质或明确标注为例外并说明理由
- [ ] 重跑后逐例对比失败集变化，解释每一条变化的原因
