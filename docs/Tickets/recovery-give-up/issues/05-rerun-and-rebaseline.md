# 05 — 回归验证：recovery 子集 + 全量评测 + 基线重建

**What to build:** 在三件落地后统一验证：recovery 子集从 1/7 提升到 ≥6/7，全量评测重跑并重建基线，把新数字与残余失败回填到 STATE.md 与 triage 文档。

**Blocked by:** 02、03、04（本目录内的编号）

**Status:** ready-for-agent

**参考:** docs/specs/recovery-give-up.md

- [ ] recovery 子集重跑：目标 ≥6/7（R02/R05 靠断言，其余靠账本与收尾）
- [ ] 全量评测重跑并重建基线（旧 25/33 不得再作为质量分数）
- [ ] 残余失败逐条归类（产品 / 断言 / 环境），写进 triage 文档
- [ ] STATE.md §1 与 §4 的数字、缺口、下一步同步更新
- [ ] 评测报告与基线按生成物处理（报告不入库，基线入库）
