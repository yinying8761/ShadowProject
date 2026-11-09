# 08 — T04 的 live 边界：同样不许"非 live 却联网"

**What to build:** `T04`（「帮我抓取 https://httpbin.org/get」）声明了 `fetch_url` 却标着 `live=False`，默认运行时会真的联网——与 T03 是**同一条 ADR-0012 边界规则**的违反（#76 只修了 T03，因为票面只写了 T03）。按同样方式把 `live` 改对，**断言一个字都不许削弱**。

**Blocked by:** None — 可立即开始。

**Status:** ready-for-agent

**参考:** docs/specs/recovery-give-up.md、`docs/adr/0012-eval-layering.md`（边界规则 #1）

- [ ] `T04` 改为 `live=True`，`expected_tools` / `expected_args_contain` 原样未动
- [ ] `--dry-run` 里 T04 不再出现（与 T03 一样被 live 开关排除）
- [ ] 断言未被削弱（不是"删掉 fetch_url 期望"来换通过）
- [ ] 有测试或运行时断言证明：标记 `live=False` 的用例不会发起真实网络请求
- [ ] 全量测试不低于 943（当前基线）
