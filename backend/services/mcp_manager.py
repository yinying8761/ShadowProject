"""
MCP (Model Context Protocol) Client Manager.

Connects to external MCP tool servers, discovers their tools,
and registers them into ShadowProject's ToolRegistry so the
Agent can use them transparently — no code changes needed per tool.

Supports two transports:
- stdio: launches a local subprocess (e.g. npx, python)
- sse: connects to a remote HTTP/SSE endpoint

**One actor task per server** (`_serve`). The transport and session context
managers are entered *and* exited by that task, and the connect timeout is an
``anyio`` cancel scope *inside* it. Both rules exist for the same reason: an
MCP server must be unable to take its neighbours down.

- anyio refuses to exit a cancel scope from a task other than the one that
  entered it ("Attempted to exit a cancel scope that isn't the current tasks's
  current cancel scope"). Exiting from the caller used to leave the stdio
  subprocess and its scope behind; the leaked scope then made *every* later
  connect in that task fail — one broken server killed the rest (#observed
  2026-10-19 with a vision server that crashed on import).
- ``asyncio.wait_for`` cancels the awaited coroutine on timeout. Cancelling
  inside the SDK's task group leaves it half-torn-down, with the same
  neighbour-killing effect. So the manager waits on a *result future* instead
  and, on timeout, asks the actor to unwind via a stop event — it never
  cancels it.
"""

from __future__ import annotations

import asyncio
import json
import os as _os
from pathlib import Path
from typing import Any

import anyio
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession
from mcp.types import CallToolResult

# Per-server connect timeout (seconds). A hung MCP server must never block
# app startup or the health-check loop indefinitely — on Windows/Python 3.14
# a stdio server that starts but never answers `initialize` would otherwise
# freeze the FastAPI lifespan forever (uvicorn never binds the port).
CONNECT_TIMEOUT_SEC = 20.0

#: How long to wait for a server that is still winding down, and the slack the
#: manager adds on top of the actor's own timeout before it gives up on a
#: connect result (the actor's timeout is the one that normally fires).
CLOSE_TIMEOUT_SEC = 5.0
READY_SLACK_SEC = 5.0


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Generate the prefixed tool name for an MCP-sourced tool.

    >>> mcp_tool_name("github", "create_issue")
    'mcp__github__create_issue'
    """
    return f"mcp__{server_name}__{tool_name}"


def format_connect_result(result: dict[str, int]) -> str:
    """One-line summary of a connect/reconnect result, for logs."""
    return (
        f"{result['connected']} connected, "
        f"{result['failed']} failed, {result['tools']} tools"
    )


def _format_mcp_result(result: CallToolResult) -> str:
    """Convert an MCP ``CallToolResult`` to a string for the LLM.

    Extracts text from every ``TextContent`` block and joins them.
    When *isError* is true wraps the text in a JSON error envelope so
    the existing ``ToolRegistry`` error-handling path treats it correctly.
    """
    texts: list[str] = []
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(text)
    output = "\n".join(texts)
    if result.isError:
        return json.dumps({"error": output}, ensure_ascii=False)
    return output


class McpManager:
    """Discover, connect, and manage MCP tool servers.

    Each server gets a persistent ``ClientSession`` owned by its own actor
    task.  Its tools are registered into *registry* with an ``mcp__<server>__``
    prefix so they are easy to spot in logs, approval dialogs, and tool
    definitions.

    Usage in FastAPI lifespan::

        manager = McpManager(tool_registry)
        await manager.connect_all("data/mcp_servers.json")
        ...
        await manager.disconnect_all()
    """

    def __init__(self, registry: Any) -> None:
        # Any → ToolRegistry, but we avoid a circular import by duck-typing.
        self._registry = registry
        self._connections: dict[str, dict[str, Any]] = {}
        #: server name → the actor task holding its transport + session
        self._tasks: dict[str, asyncio.Task] = {}
        #: server name → the event that asks its actor task to wind down
        self._stops: dict[str, asyncio.Event] = {}
        self._health_task: asyncio.Task | None = None

    # ── Public API ──────────────────────────────────────────────────

    async def connect_all(self, config_path: str | Path) -> dict[str, int]:
        """Read *config_path*, connect every listed server, register tools.

        Returns a summary dict: ``{"connected": N, "failed": M, "tools": T}``.

        Failures for individual servers are logged but never raised —
        one misbehaving server won't prevent the rest (or the app) from
        starting.
        """
        config_path = Path(config_path)
        result: dict[str, int] = {"connected": 0, "failed": 0, "tools": 0}

        if not config_path.exists():
            print(f"[McpManager] config not found: {config_path} — skipping")
            return result

        try:
            raw = config_path.read_text(encoding="utf-8")
            config: dict = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"[McpManager] invalid JSON in {config_path}: {exc}")
            return result

        servers: list[dict] = config.get("servers", [])
        if not servers:
            print("[McpManager] no servers configured")
            return result

        targets: list[dict] = []
        for server_cfg in servers:
            name: str = server_cfg.get("name", "")
            if not name:
                print("[McpManager] skipping unnamed server entry")
                result["failed"] += 1
                continue
            if name in self._connections:
                print(f"[McpManager] server '{name}' already connected")
                continue
            targets.append(server_cfg)

        # Connect concurrently: every server is isolated in its own actor task,
        # so one that hangs until its timeout delays only itself. Connecting
        # them one by one would cost N x 20s of startup for N dead servers.
        counts = await asyncio.gather(
            *(self._connect_server(cfg) for cfg in targets),
            return_exceptions=True,
        )

        for server_cfg, count in zip(targets, counts):
            name = server_cfg["name"]
            if isinstance(count, BaseException):
                # Per-server isolation: one dead MCP server must never kill
                # app startup.
                if isinstance(count, (KeyboardInterrupt, SystemExit)):
                    raise count
                print(f"[McpManager] '{name}' failed: {count!r}", flush=True)
                count = None
            if count is None:
                result["failed"] += 1
            else:
                result["connected"] += 1
                result["tools"] += count
                print(f"[McpManager] '{name}': {count} tool(s) registered", flush=True)

        return result

    async def reconnect_all(self, config_path: str | Path) -> dict[str, int]:
        """Revive dead sessions and connect anything missing, dropping nothing.

        ``connect_all`` alone skips servers that are already connected, so the
        health check has to run first to revive the dead ones.  Healthy
        servers are never disconnected — a failed reconnect must not leave the
        app worse off than before the command.
        """
        await self.health_check()
        return await self.connect_all(config_path)

    async def disconnect_all(self) -> None:
        """Disconnect every server and unregister its tools."""
        for name, conn in list(self._connections.items()):
            await self._close_connection(name, conn)
            print(f"[McpManager] '{name}' disconnected")
        self._connections.clear()

    @property
    def connected_servers(self) -> list[str]:
        """Names of currently-connected MCP servers."""
        return list(self._connections.keys())

    async def health_check(self) -> dict[str, bool]:
        """Ping every connected server; reconnect the unhealthy ones.

        Returns a mapping of server name → healthy (after any reconnect).
        """
        results: dict[str, bool] = {}
        for name, conn in list(self._connections.items()):
            if await self._ping(conn):
                results[name] = True
                continue
            results[name] = await self._reconnect(name, conn)
        return results

    def start_health_check(self, interval: float = 30.0) -> asyncio.Task:
        """Start the periodic health-check background task."""
        if self._health_task is None:
            self._health_task = asyncio.create_task(self._health_loop(interval))
        return self._health_task

    async def stop_health_check(self) -> None:
        """Cancel the periodic health-check background task and await it."""
        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None

    async def _health_loop(self, interval: float) -> None:
        """Background loop: health-check every *interval* seconds."""
        while True:
            await asyncio.sleep(interval)
            try:
                await self.health_check()
            except Exception as exc:
                print(f"[McpManager] health check failed: {exc}", flush=True)

    # ── Connection lifecycle ────────────────────────────────────────

    async def _connect_server(self, cfg: dict) -> int | None:
        """Start one server's actor task and wait for its connect result.

        Returns the tool count, or ``None`` when the server failed (the reason
        is logged). The actor task is never cancelled from here: on timeout we
        only ask it to wind down and move on, so the next server is unaffected.
        """
        name: str = cfg.get("name", "")
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()

        task = asyncio.create_task(self._serve(cfg, ready, stop), name=f"mcp-{name}")
        self._tasks[name] = task
        self._stops[name] = stop
        task.add_done_callback(lambda t, n=name: self._reap(n, t))

        done, _pending = await asyncio.wait(
            {ready}, timeout=CONNECT_TIMEOUT_SEC + READY_SLACK_SEC
        )
        if ready not in done:
            # The actor's own timeout is shorter, so this is a safety net:
            # ask it to unwind (never cancel it) and report the failure.
            stop.set()
            print(f"[McpManager] '{name}' failed: connect timed out", flush=True)
            return None

        kind, payload = ready.result()
        if kind != "ok":
            print(f"[McpManager] '{name}' failed: {payload}", flush=True)
            return None
        return payload

    async def _serve(
        self, cfg: dict, ready: asyncio.Future, stop: asyncio.Event
    ) -> None:
        """Own one server's connection for its whole lifetime (actor task)."""
        name: str = cfg.get("name", "")
        transport_cm = session_cm = None
        tool_names: list[str] = []

        try:
            transport_cm, session_cm, session = await self._connect_transport(cfg)
            try:
                # The handshake is the only part that can hang on a server that
                # started but never answers, so it is the only part under the
                # timeout — an anyio scope, which keeps the cancellation here.
                with anyio.move_on_after(CONNECT_TIMEOUT_SEC) as scope:
                    await session.initialize()
                    list_result = await session.list_tools()

                if scope.cancelled_caught:
                    self._resolve(
                        ready,
                        ("failed", f"no answer within {CONNECT_TIMEOUT_SEC:.0f}s"),
                    )
                    return

                tool_names = self._register_tools(name, session, list_result.tools)
                self._connections[name] = {
                    "cfg": cfg,
                    "session": session,
                    "tool_names": tool_names,
                }
                self._resolve(ready, ("ok", len(tool_names)))

                # Hold the connection open here: entering and exiting the
                # context managers must both happen in this task.
                await stop.wait()
            finally:
                self._connections.pop(name, None)
                self._unregister_tools(tool_names)
                await self._close_transport(name, session_cm, transport_cm)
        except asyncio.CancelledError:
            self._resolve(ready, ("failed", "connection cancelled"))
            raise
        except BaseException as exc:
            self._resolve(ready, ("failed", repr(exc)))
            print(f"[McpManager] '{name}' connection ended: {exc!r}", flush=True)

    async def _connect_transport(self, cfg: dict) -> tuple[Any, Any, ClientSession]:
        """Open the transport + session for *cfg* (no handshake yet)."""
        transport: str = cfg.get("transport", "stdio")
        if transport == "stdio":
            return await self._connect_stdio(cfg)
        if transport == "sse":
            return await self._connect_sse(cfg)
        raise ValueError(f"unknown transport '{transport}'")

    async def _connect_stdio(
        self, cfg: dict
    ) -> tuple[Any, Any, ClientSession]:
        """Open a stdio transport + session for *cfg*."""
        env = cfg.get("env") or None
        if env:
            merged = dict(_os.environ)
            merged.update(env)
            env = merged

        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env=env,
            cwd=cfg.get("cwd"),
        )

        transport_cm = stdio_client(params)
        read, write = await transport_cm.__aenter__()

        # If the session fails to start, close the transport too — otherwise
        # a half-open stdio transport (and its subprocess) leaks.
        session_cm = ClientSession(read, write)
        try:
            session: ClientSession = await session_cm.__aenter__()
        except BaseException:
            await self._close_cm("transport", cfg.get("name", ""), transport_cm)
            raise

        return transport_cm, session_cm, session

    async def _connect_sse(
        self, cfg: dict
    ) -> tuple[Any, Any, ClientSession]:
        """Open an SSE transport + session for *cfg*."""
        url: str = cfg["url"]
        headers: dict | None = cfg.get("headers") or None

        transport_cm = sse_client(url, headers=headers)
        read, write = await transport_cm.__aenter__()

        session_cm = ClientSession(read, write)
        try:
            session: ClientSession = await session_cm.__aenter__()
        except BaseException:
            await self._close_cm("transport", cfg.get("name", ""), transport_cm)
            raise

        return transport_cm, session_cm, session

    def _register_tools(self, name: str, session: ClientSession, tools) -> list[str]:
        """Register *tools* under ``mcp__<name>__`` and return their names."""
        tool_names: list[str] = []
        for tool in tools:
            full_name = mcp_tool_name(name, tool.name)

            self._registry.register(
                name=full_name,
                description=f"[MCP:{name}] {tool.description or ''}",
                parameters=tool.inputSchema,
                handler=_make_mcp_handler(session, tool.name),
                require_approval=True,  # external tools default to approval
            )
            tool_names.append(full_name)
        return tool_names

    def _unregister_tools(self, tool_names: list[str]) -> None:
        for tool_name in tool_names:
            self._registry.unregister(tool_name)

    async def _close_transport(
        self, name: str, session_cm: Any, transport_cm: Any
    ) -> None:
        """Exit the session/transport context managers in the actor's task."""
        await self._close_cm("session", name, session_cm)
        await self._close_cm("transport", name, transport_cm)

    async def _close_cm(self, label: str, name: str, cm: Any) -> None:
        """Exit one context manager, in the actor's own task.

        No extra ``CancelScope`` here: anyio requires scopes to be exited in
        reverse order of entry, so wrapping this in a (shield) scope of our own
        makes the SDK's own task group *not* the current scope and the exit
        fails with "Attempted to exit a cancel scope that isn't the current
        tasks's current cancel scope".
        """
        if cm is None:
            return
        try:
            await cm.__aexit__(None, None, None)
        except Exception as exc:
            print(f"[McpManager] error closing {label}_cm for '{name}': {exc}", flush=True)

    async def _close_connection(self, name: str, conn: dict) -> None:
        """Ask *name*'s actor task to unwind and wait for it.

        The actor unregisters its tools and closes its transport in its own
        task, which is the only place anyio allows it.
        """
        stop = self._stops.pop(name, None)
        if stop is not None:
            stop.set()
        task = self._tasks.get(name)
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=CLOSE_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            print(
                f"[McpManager] '{name}' did not close within {CLOSE_TIMEOUT_SEC:.0f}s",
                flush=True,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            print(f"[McpManager] '{name}' close error: {exc!r}", flush=True)

    def _reap(self, name: str, task: asyncio.Task) -> None:
        """Done-callback: drop the actor and consume its exception, if any."""
        self._tasks.pop(name, None)
        self._stops.pop(name, None)
        self._connections.pop(name, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            print(f"[McpManager] '{name}' task ended: {exc!r}", flush=True)

    @staticmethod
    def _resolve(ready: asyncio.Future, outcome: tuple[str, Any]) -> None:
        """Resolve *ready* once, with a value (never an exception)."""
        if not ready.done():
            ready.set_result(outcome)

    async def _ping(self, conn: dict) -> bool:
        """Return True when the server's session answers ``list_tools``."""
        try:
            await conn["session"].list_tools()
            return True
        except Exception:
            return False

    async def _reconnect(self, name: str, conn: dict) -> bool:
        """Tear down and re-establish *name*; True on success.

        On failure the connection is gone (its tools are already
        unregistered) so the next health check retries it.
        """
        cfg = conn.get("cfg")
        await self._close_connection(name, conn)

        if cfg is None:
            return False
        count = await self._connect_server(cfg)
        if count is None:
            print(f"[McpManager] '{name}' reconnect failed", flush=True)
            return False
        self._reset_breakers(self._connections[name]["tool_names"])
        print(f"[McpManager] '{name}' reconnected: {count} tool(s)", flush=True)
        return True

    def _reset_breakers(self, tool_names: list[str]) -> None:
        """Reset the circuit breaker for each *tool_name* (duck-typed registry)."""
        reset_breaker = getattr(self._registry, "reset_breaker", None)
        if reset_breaker is None:
            return
        for tool_name in tool_names:
            reset_breaker(tool_name)


def _make_mcp_handler(session: ClientSession, tool_name: str):
    """Return an async handler that forwards calls to *session.call_tool*.

    The closure keeps a reference to the persistent *session* — every
    invocation routes through the same long-lived MCP connection.
    """

    async def _handler(**kwargs: Any) -> str:
        result = await session.call_tool(tool_name, kwargs)
        return _format_mcp_result(result)

    return _handler
