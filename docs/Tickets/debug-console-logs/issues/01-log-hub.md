# 01 — LogHub 日志总线（环形缓冲 + 落盘 + 订阅）

**What to build:** 后端所有 `print`/框架日志进入日志总线：内存环形缓冲（最近 500 行，满丢最老）+
落盘 `data/logs/companion.log`（5MB×2 滚动，磁盘上限约 15MB）+ 订阅者分发。应用启动时装好
（幂等），此后任何日志都同时进内存缓冲、落盘、并推给订阅者——这是 `/ws/logs` 通道的地基
（ADR-0002）。

**Blocked by:** None — 可直接开始。

**Status:** ready-for-agent

**参考:** `docs/specs/debug-console-logs.md`、`docs/adr/0002-stdout-log-hub.md`

**关键语义：**

- 条目为结构化 `{source, level, message, ts}`；`source ∈ backend|renderer|cmd`；后端 print 默认 `source=backend, level=info`。
- **不迁移任何现有 print**：`sys.stdout` Tee（写穿原 stdout + 进 hub）+ 根 logger 挂 handler（覆盖 uvicorn 等框架日志）。
- 环形缓冲 500 行、线程锁（print 可能来自任何线程）；订阅者注册/取消注册。
- 文件 handler：`data/logs/companion.log`（基于 `resolve_data_dir()`），滚动 5MB×2。
- 安装幂等（重复启动不重复装）；hub 实例设到 `app.state.log_hub`（main.py lifespan 内）。

## Checklist

- [ ] 新建 `services/log_hub.py`：`LogHub` 类（环形缓冲 500、结构化条目、订阅者、线程锁）
- [ ] `install()`：stdout Tee + 根 logger handler + 文件 handler（`data/logs/companion.log` 5MB×2）；幂等
- [ ] `main.py` lifespan 启动时调用 install + 设 `app.state.log_hub`
- [ ] 新建 `backend/tests/test_log_hub.py`：
  - [ ] print 经 Tee 进 hub、条目结构化（source/level/ts）
  - [ ] 缓冲 500 上限：写 600 条 → 剩 500、最老被丢
  - [ ] 订阅者收到推送（注册/取消注册生效）
- [ ] 手动冒烟：起后端 → `data/logs/companion.log` 出现日志且容量受控