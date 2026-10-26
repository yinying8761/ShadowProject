# MCP servers: one actor task each, one venv each

The vision MCP server stopped starting (`mcp` 1.8.1 crashes on Python 3.14 while
registering its tools: `TypeError: issubclass() arg 1 must be a class`, from a
parameter annotation that arrives as a string), and the app logged
`MCP: 0 connected, 2 failed, 0 tools` even though only **one** of the two
configured servers was broken. The healthy one failed as well, so the whole MCP
feature looked dead.

Two independent defects were behind that:

1. **Version reality.** The app is pinned to `mcp` 1.8.1 because `amap-mcp-server`
   declares `mcp==1.8.1`. The fix for the crash lives in newer SDKs, but a newer
   SDK cannot be installed into the app's environment at all: on Python 3.14
   `mcp>=1.30` requires `starlette>=0.48`, while `fastapi 0.115` requires
   `starlette<0.47`. The conflict is structural, not a matter of picking the
   right version.
2. **Lifecycle.** `_connect_stdio` entered the transport/session context managers
   manually and stored them, while the whole connect ran inside
   `asyncio.wait_for(...)`. The context managers were therefore entered in
   `wait_for`'s internal task and exited later in the caller's task, which anyio
   refuses ("Attempted to exit a cancel scope that isn't the current tasks's
   current cancel scope"). The exit failed, the stdio subprocess and the cancel
   scope leaked, and from then on **every** connect in that task raised
   `CancelledError` — the first dead server killed the rest.

## Decision

- **Each server gets its own actor task** (`McpManager._serve`). It enters *and*
  exits the transport and session context managers, so anyio's scope rules hold
  by construction.
- **The connect timeout is an `anyio` cancel scope inside that task**
  (`move_on_after` around `initialize()` + `list_tools()`). The manager waits on a
  result future instead of a coroutine: on timeout it sets a stop event and moves
  on. It never cancels an actor, because cancelling inside the SDK's task group is
  what poisoned its neighbours.
- **Servers connect concurrently.** Broken servers each cost one timeout, not one
  timeout each in sequence, so startup does not grow with the number of failures.
- **The result future carries a value** (`("ok", count)` / `("failed", reason)`),
  never an exception, so an abandoned connect cannot surface later as
  "exception was never retrieved".
- **Version isolation is a configuration convention, not an install.** A server
  whose SDK needs differ from the app's gets its own venv, and its config entry
  points `command` at that venv's interpreter by absolute path. The app's
  environment keeps the versions `fastapi` pins; the server's environment is
  irrelevant to the app.
- **A failing server is logged, never raised.** The app starts with whatever
  connected (this was already true and stays true).

## Considered Options

- **Upgrade the app's `mcp`** — rejected: forces `starlette>=0.48` on Python 3.14
  and breaks `fastapi 0.115` (`pip check` reports both conflicts). The dependency
  graph forbids it, not the effort.
- **Keep `asyncio.wait_for` and catch the fallout** — rejected: that *is* the bug.
  Cancellation delivered at an arbitrary await point inside the SDK's task group
  leaves it half torn down, and the damage outlives the call.
- **Exit the context managers from the caller under a shield scope** — tried and
  rejected: anyio scopes must unwind in reverse order of entry, so wrapping the
  exit in a `CancelScope(shield=True)` makes the SDK's task group *not* the
  current scope and the exit fails the same way.
- **One thread with its own event loop per server** — deferred, not rejected:
  strictly stronger isolation (a hung server could not touch the app's loop at
  all), at the cost of routing every `call_tool` through
  `run_coroutine_threadsafe`. Revisit if a server ever manages to stall the app.
- **Pin the vision server to Python 3.12/3.13 instead of a venv** — rejected: it
  puts a second interpreter on the machine, solves nothing for the next server
  with a version floor, and hides the real constraint.
- **Fail fast on a dead server instead of waiting out the timeout** — rejected for
  now: the stdio client gives us no handle on the child process, so an
  instant-exit server is only detectable by the handshake timing out.

## Consequences

- The regression is locked by `tests/test_mcp_manager.py`: a real crashing
  subprocess next to a real server. On the old code that test fails with
  `connected: 0, failed: 2` — the exact field symptom. Fake sessions cannot see
  this bug (there is no task group in a fake), so that test must stay a
  real-subprocess test.
- A hung/dead server costs one `CONNECT_TIMEOUT_SEC` (20s) at startup and nothing
  else; the healthy servers connect in parallel meanwhile.
- `connected_servers` and the registry now reflect **completion** order, not
  config order. Nothing depends on config order any more.
- A server that dies *later* is still handled by the health check: `_ping` fails,
  `_reconnect` stops that actor (its own task closes the transport) and starts a
  new one.
- Each MCP venv is a local artifact (`.venv-vision/` is gitignored). The config
  template must explain the convention, because a bare `"command": "python"`
  silently means "the app's interpreter" and is exactly how the vision server
  broke.
- **A connecting server is not a working feature.** Fixing the crash exposed
  three more mismatches on the `see_screen` path, all of them silent (each only
  makes the call fail and fall back to the legacy model): the tool name was
  hardcoded to `mcp__vision__analyze_image` while the server was named
  `zhipu-vision`; the arguments were sent as `image_data` while the server's
  schema wants `image`; and the config passed `ZHIPU_API_KEY` while
  `deepseek_vision_mcp` reads `VISION_API_KEY` and also loads a `.env` of its own
  (which would have supplied the app's legacy qwen settings). The tool name is
  now overridable via `VISION_MCP_TOOL`; the env vars are pinned in the server's
  `env` block. Verification has to be an actual call through the tool, not a
  `4 tool(s) registered` line.
- The legacy vision path (`services/vision_service.py`, `VISION_PRESETS`) still
  points at the retired `qwen-vl-max`. When MCP is wired it is now **not** used at
  all — `see_screen` treats MCP as authoritative and returns an explicit error on
  failure; the legacy model is reached only when no MCP dispatch is configured.
- **The same "never cancel inside the SDK" rule applies to calls, not just
  connects.** The tool sandbox wraps every handler in `asyncio.wait_for`, so a
  slow `see_screen` was cancelled mid-`call_tool` (2026-10-20, 15s deadline, and
  the backend went away). `see_screen` now owns a shorter deadline (25s) and
  waits with `asyncio.wait`, abandoning the call instead of cancelling it; the
  sandbox deadline is 40s so it never fires first. A wedged session is left to
  the health check, which pings and reconnects it. The general form: **any
  externally imposed timeout around an MCP call must be converted into an
  abandonment, and the deadline must sit inside our code.**
