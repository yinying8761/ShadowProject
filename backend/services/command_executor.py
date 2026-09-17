"""
Debug-console commands — a fixed whitelist, dispatched by name (ticket 04).

Security boundary: the command string is only ever used as a dictionary key.
Nothing here evaluates it, spawns a process, or touches the filesystem.

Every result line is written back to the log hub as a ``source=cmd`` entry,
so the panel view, the log file and any other connected client all see the
same thing (ADR-0002).
"""

from __future__ import annotations

import os
import platform
from typing import Awaitable, Callable

from services.log_hub import CMD, LogHub

class CommandExecutor:
    """Runs whitelisted debug-console commands and logs their output."""

    def __init__(
        self,
        hub: LogHub,
        *,
        describe_model: Callable[[], str] | None = None,
        reconnect_mcp: Callable[[], Awaitable[str]] | None = None,
        reload_config: Callable[[], str] | None = None,
    ) -> None:
        self._hub = hub
        self._describe_model = describe_model
        self._reconnect_mcp = reconnect_mcp
        self._reload_config = reload_config
        self._handlers: dict[str, Callable[[], Awaitable[None]]] = {
            "clear": self._clear,
            "status": self._status,
            "help": self._help,
            "mcp reconnect": self._mcp_reconnect,
            "config reload": self._config_reload,
        }

    async def run(self, command: str) -> None:
        """Run one command; its output goes to the hub as ``[cmd]`` lines."""
        name = command.strip().lower()
        handler = self._handlers.get(name)
        if handler is None:
            self._emit(f"unknown: {command.strip()}")
            await self._help()
            return
        await handler()

    def _emit(self, text: str) -> None:
        # Plain text only: the label lives in the entry's `source` field (the
        # panel renders `[cmd]`, the log file prints it via %(name)s), so
        # embedding it here too would double-label (ADR-0002).
        self._hub.add(text, source=CMD)

    async def _clear(self) -> None:
        self._hub.clear()
        self._emit("log buffer cleared")

    async def _help(self) -> None:
        # Derived from the dispatch table, so help can never advertise a command
        # that would come straight back as "unknown".
        self._emit("available: " + ", ".join(sorted(self._handlers)))

    async def _status(self) -> None:
        self._emit(
            f"log buffer: {len(self._hub.lines())}/{self._hub.ring_size} lines, "
            f"{self._hub.subscriber_count} subscriber(s)"
        )
        self._emit(f"log file: {self._hub.log_file or 'not installed'}")
        self._emit(f"model: {self._model_line()}")
        self._emit(f"process: pid={os.getpid()} python={platform.python_version()}")

    async def _mcp_reconnect(self) -> None:
        if self._reconnect_mcp is None:
            self._emit("mcp reconnect unavailable: no MCP manager")
            return
        try:
            self._emit(await self._reconnect_mcp())
        except Exception as exc:
            self._emit(f"mcp reconnect failed: {exc}")

    async def _config_reload(self) -> None:
        if self._reload_config is None:
            self._emit("config reload unavailable: no config store")
            return
        try:
            self._emit(self._reload_config())
        except Exception as exc:
            self._emit(f"config reload failed: {exc}")

    def _model_line(self) -> str:
        """Current model and SDK type, e.g. ``deepseek-v4-flash (openai)``."""
        if self._describe_model is None:
            return "unknown (not wired)"
        try:
            return self._describe_model()
        except Exception as exc:
            # A debug console should say why something is unavailable, not die.
            return f"unknown ({type(exc).__name__})"
