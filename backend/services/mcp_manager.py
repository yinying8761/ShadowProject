"""
MCP (Model Context Protocol) Client Manager.

Connects to external MCP tool servers, discovers their tools,
and registers them into ShadowProject's ToolRegistry so the
Agent can use them transparently — no code changes needed per tool.

Supports two transports:
- stdio: launches a local subprocess (e.g. npx, python)
- sse: connects to a remote HTTP/SSE endpoint
"""

from __future__ import annotations

import asyncio
import json
import os as _os
from pathlib import Path
from typing import Any

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession
from mcp.types import CallToolResult

# Per-server connect timeout (seconds). A hung MCP server must never block
# app startup or the health-check loop indefinitely — on Windows/Python 3.14
# a stdio server that starts but never answers `initialize` would otherwise
# freeze the FastAPI lifespan forever (uvicorn never binds the port).
CONNECT_TIMEOUT_SEC = 20.0


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

    Each server gets a persistent ``ClientSession``.  Its tools are
    registered into *registry* with an ``mcp__<server>__`` prefix so
    they are easy to spot in logs, approval dialogs, and tool definitions.

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

        for server_cfg in servers:
            name: str = server_cfg.get("name", "")
            if not name:
                print("[McpManager] skipping unnamed server entry")
                result["failed"] += 1
                continue
            if name in self._connections:
                print(f"[McpManager] server '{name}' already connected")
                continue

            try:
                count = await asyncio.wait_for(
                    self._connect_one(server_cfg), timeout=CONNECT_TIMEOUT_SEC
                )
                result["connected"] += 1
                result["tools"] += count
                print(f"[McpManager] '{name}': {count} tool(s) registered", flush=True)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                # Per-server isolation: one dead MCP server must never kill
                # app startup. The mcp SDK's stdio client can raise
                # BaseExceptionGroup / CancelledError out of its internal
                # anyio task group on Windows (a server that exits instantly
                # poisons the NEXT connect attempt) — plain `except Exception`
                # lets those escape and crash the FastAPI lifespan.
                result["failed"] += 1
                print(f"[McpManager] '{name}' failed: {exc!r}", flush=True)

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

    # ── Internals ───────────────────────────────────────────────────

    async def _close_connection(self, name: str, conn: dict) -> None:
        """Unregister a server's tools and close its session/transport."""
        for tool_name in conn["tool_names"]:
            self._registry.unregister(tool_name)

        for mgr_key in ("session_cm", "transport_cm"):
            try:
                cm = conn[mgr_key]
                await cm.__aexit__(None, None, None)
            except Exception as exc:
                print(f"[McpManager] error closing {mgr_key} for '{name}': {exc}", flush=True)

    async def _ping(self, conn: dict) -> bool:
        """Return True when the server's session answers ``list_tools``."""
        try:
            await conn["session"].list_tools()
            return True
        except Exception:
            return False

    async def _reconnect(self, name: str, conn: dict) -> bool:
        """Tear down and re-establish *name*; True on success.

        On failure the closed connection stays tracked (its tools are
        already unregistered) so the next health check retries it.
        """
        cfg = conn.get("cfg")
        await self._close_connection(name, conn)

        if cfg is None:
            return False
        try:
            count = await asyncio.wait_for(
                self._connect_one(cfg), timeout=CONNECT_TIMEOUT_SEC
            )
            self._reset_breakers(self._connections[name]["tool_names"])
            print(f"[McpManager] '{name}' reconnected: {count} tool(s)", flush=True)
            return True
        except Exception as exc:
            print(f"[McpManager] '{name}' reconnect failed: {exc}", flush=True)
            return False

    def _reset_breakers(self, tool_names: list[str]) -> None:
        """Reset the circuit breaker for each *tool_name* (duck-typed registry)."""
        reset_breaker = getattr(self._registry, "reset_breaker", None)
        if reset_breaker is None:
            return
        for tool_name in tool_names:
            reset_breaker(tool_name)

    async def _connect_one(self, cfg: dict) -> int:
        """Connect a single server and register its tools.  Returns tool count."""
        name: str = cfg["name"]
        transport: str = cfg.get("transport", "stdio")

        if transport == "stdio":
            transport_cm, session_cm, session = await self._connect_stdio(cfg)
        elif transport == "sse":
            transport_cm, session_cm, session = await self._connect_sse(cfg)
        else:
            raise ValueError(f"unknown transport '{transport}'")

        # ── Discover tools ──────────────────────────────────────
        list_result = await session.list_tools()
        tool_names: list[str] = []

        for tool in list_result.tools:
            full_name = mcp_tool_name(name, tool.name)

            self._registry.register(
                name=full_name,
                description=f"[MCP:{name}] {tool.description or ''}",
                parameters=tool.inputSchema,
                handler=_make_mcp_handler(session, tool.name),
                require_approval=True,  # external tools default to approval
            )
            tool_names.append(full_name)

        self._connections[name] = {
            "cfg": cfg,
            "session": session,
            "transport_cm": transport_cm,
            "session_cm": session_cm,
            "tool_names": tool_names,
        }

        return len(tool_names)

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
            await session.initialize()
        except BaseException:
            try:
                await transport_cm.__aexit__(None, None, None)
            except Exception:
                pass  # teardown errors during cleanup must not mask the cause
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
        session: ClientSession = await session_cm.__aenter__()
        await session.initialize()

        return transport_cm, session_cm, session


def _make_mcp_handler(session: ClientSession, tool_name: str):
    """Return an async handler that forwards calls to *session.call_tool*.

    The closure keeps a reference to the persistent *session* — every
    invocation routes through the same long-lived MCP connection.
    """

    async def _handler(**kwargs: Any) -> str:
        result = await session.call_tool(tool_name, kwargs)
        return _format_mcp_result(result)

    return _handler
