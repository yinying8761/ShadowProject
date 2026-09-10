# Log capture: stdout tee hub instead of migrating prints to logging

Backend logging is almost entirely bare `print(..., flush=True)` calls across
core/services (agent, watcher, daily greeting, circuit breaker, MCP manager...).
Iteration 3 needs logs on disk (rotated), in an in-memory ring buffer, and pushed
live to the new in-app debug console.

## Considered Options

- **Migrate all prints to the `logging` framework** — rejected: dozens of call
  sites across core/services; a mechanical sweep risks churn and missed sites in
  a solo project, for no behavioral gain today.
- **logging handler only (leave prints on console)** — rejected: captures only
  framework logs; the majority of debug output (all the `[DAILY]`, `[watcher]`,
  `[CircuitBreaker]` prints) would still be invisible to the hub.
- **stdout tee into a log hub (chosen)** — redirect `sys.stdout` through a Tee
  that writes through to the real stdout and feeds a hub (ring buffer +
  RotatingFileHandler + WS subscribers). Zero call-site changes, 100% coverage
  of existing prints; a root-logger handler picks up framework logs too.

## Consequences

- `sys.stdout` is hijacked process-wide: anything writing to stdout lands in the
  hub (including uvicorn output). Nothing in-process writes binary to stdout, so
  the tee is safe today; must be re-verified if that ever changes.
- Subprocess stdout (MCP servers) does NOT flow through the hub — their output
  arrives via mcp_manager pipes and is logged by that layer.
- The hub is a convergence point: if prints migrate to `logging` later, the hub
  keeps working unchanged (it consumes both sources).
- Log rotation (5MB × 2 backups) bounds disk usage by design; no cleanup job.
