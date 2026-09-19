# 03 — /ws/logs 调试通道（历史 + 实时 + renderer 上报）

**What to build:** 新增 `/ws/logs` WebSocket：客户端连接即收到最近 500 行日志历史，之后实时
收到新行；客户端上行 renderer 错误（`{source:"renderer", level, message}`）会以
`source=renderer` 进入日志总线。这是前端调试面板的传输通道。

**Blocked by:** #01（通道读 hub 的环形缓冲并订阅实时推送）

**Status:** ready-for-agent

**参考:** `docs/specs/debug-console-logs.md`

**关键语义：**

- 出站协议：`{"type":"logs_history","lines":[...]}`（连接即发最近 500 行）→ 之后
  `{"type":"logs_line","line":{...}}` 逐条推送。
- 入站协议：`{"type":"renderer_log","level":"warn|error","message":"..."}` → WS 层框架为
  `source=renderer` 写入 hub。
- 本机应用，无需鉴权；`main.py` include 该路由。
- hub 从 `app.state.log_hub` 取；**测试只挂 logs 路由 + 注入干净 hub**（不 `import main.app`，
  避免 lifespan 装 Tee 干扰 pytest 的 stdout 捕获）。

## Checklist

- [ ] 新 api 模块登 WS `/ws/logs`：连接即推 `logs_history`（ring 快照）→ 订阅 hub 实时推 `logs_line`
- [ ] 入站 `renderer_log` → 写入 hub（`source=renderer`）
- [ ] `main.py` include 新路由；读取 `app.state.log_hub`
- [ ] 测试（S2，TestClient + `websocket_connect`，只挂 logs 路由 + 注入干净 hub）：
  - [ ] 连接即收 history（含既有行）
  - [ ] hub 新行实时到达订阅者
  - [ ] 上行 `renderer_log` → hub 出现 `source=renderer` 条目
- [ ] 手动冒烟：ws 连 `/ws/logs` 收到历史 + 实时行