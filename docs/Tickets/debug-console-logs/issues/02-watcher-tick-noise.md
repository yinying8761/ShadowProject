# 02 — watcher tick 降噪（源头日志策略）

**What to build:** 主动陪伴的例行 tick 不再每 15s 打一行（原 ~240 行/小时），只在「状态签名
变化（level/threshold/daily_count）」或「即将触发（ok=True）」时打印；设置
`PROACTIVE_TICK_DEBUG=1` 可恢复全量。正常日志不再被例行噪音淹没。

**Blocked by:** None — 可直接开始。

**Status:** ready-for-agent

**参考:** `docs/specs/debug-console-logs.md`

**关键语义：**

- 抽一个**纯判定**（如 `should_log_tick(prev_signature, cur_signature, ok) -> bool`）——
  签名 = `(level, next_threshold, daily_count)`；`ok=True`（即将触发）恒打；签名变化才打；否则静默。
- tick 打印内容沿用现状（含 idle 值），**只改打印时机**。
- `PROACTIVE_TICK_DEBUG=1` 时恒打（全量）。
- ProactiveWatcher 本来就是构造注入（fakes 可测），不碰 DB。

## Checklist

- [ ] 纯判定函数/类 + `ProactiveWatcher` 的 tick 打印处接线
- [ ] `PROACTIVE_TICK_DEBUG=1` 环境变量开关（恢复全量）
- [ ] 新建 `backend/tests/test_proactive_watcher.py`（当前**无** watcher 测试文件）：
  - [ ] 签名不变 → 不打印（capsys 断言无 tick 行）
  - [ ] 签名变化（level/threshold/daily）→ 打印
  - [ ] 即将触发（ok=True）→ 打印
  - [ ] 设 env 开关 → 恢复全量
- [ ] 全量回归：`python -m pytest tests/ -v`