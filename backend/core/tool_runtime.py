"""
ToolRuntime — wraps ToolRegistry with tracing, sandbox, and future extension points.

Exposes the same interface as ToolRegistry (register / unregister / dispatch /
get_tool_definitions / needs_approval) so Agent requires zero behavioural changes.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from core.tool_registry import ToolRegistry, ToolHandler


# ── Tool category → default timeout (seconds) ──────────────────────────
_NETWORK_TOOLS = {"fetch_url", "research", "mcp__"}
_SCREEN_TOOLS = {"see_screen"}
_FILE_TOOLS = {"read_file", "write_file", "list_directory", "search_files"}


def _default_timeout_sec(tool_name: str) -> float:
    """Return the default sandbox timeout for *tool_name*."""
    if tool_name in _FILE_TOOLS:
        return 10.0
    if tool_name in _NETWORK_TOOLS or tool_name.startswith("mcp__"):
        return 30.0
    if tool_name in _SCREEN_TOOLS:
        return 15.0
    return 10.0


class ToolRuntime:
    """Thin wrapper around ToolRegistry that adds tracing + sandbox + placeholders.

    Parameters
    ----------
    registry:
        The inner ``ToolRegistry`` to delegate to.  When *None* the
        module-level singleton is used (production path).
    enable_tracing:
        When ``True`` (default) every ``dispatch()`` call is persisted to
        the ``tool_runs`` table via ``ToolTraceStore``.
    enable_sandbox:
        When ``True`` (default) handlers are wrapped in
        ``asyncio.wait_for`` with a per-tool timeout.
    enable_circuit_breaker:
        Reserved — stored but not implemented.
    enable_rate_limit:
        Reserved — stored but not implemented.
    circuit_threshold:
        Reserved.
    rate_limit_per_min:
        Reserved.
    """

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        *,
        enable_tracing: bool = True,
        enable_sandbox: bool = True,
        enable_circuit_breaker: bool = False,
        enable_rate_limit: bool = False,
        circuit_threshold: int = 5,
        rate_limit_per_min: int = 30,
    ):
        self._registry = registry or _default_registry()
        self.enable_tracing = enable_tracing
        self.enable_sandbox = enable_sandbox
        self.enable_circuit_breaker = enable_circuit_breaker
        self.enable_rate_limit = enable_rate_limit
        self.circuit_threshold = circuit_threshold
        self.rate_limit_per_min = rate_limit_per_min

        # Per-tool sandbox config: tool_name → {"timeout_sec": float}
        self._sandbox: dict[str, dict] = {}

        # Tracing store (lazy-init on first dispatch to avoid import at module level)
        self._trace_store: Any = None

    # ── Public interface (mirrors ToolRegistry) ────────────────────────

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: ToolHandler,
        require_approval: bool = False,
        *,
        sandbox_config: dict | None = None,
    ):
        """Register a tool, optionally with sandbox config.

        *sandbox_config* may contain ``timeout_sec`` to override the
        per-category default.
        """
        self._registry.register(name, description, parameters, handler, require_approval)
        if sandbox_config is not None:
            self._sandbox[name] = sandbox_config

    def unregister(self, name: str):
        self._registry.unregister(name)
        self._sandbox.pop(name, None)

    def get_tool_definitions(self) -> list[dict]:
        return self._registry.get_tool_definitions()

    def needs_approval(self, name: str) -> bool:
        return self._registry.needs_approval(name)

    async def dispatch(
        self,
        name: str,
        arguments: dict,
        *,
        conversation_id: str | None = None,
    ) -> str:
        """Execute a tool call with tracing and optional sandbox timeout.

        Parameters
        ----------
        conversation_id:
            Optional conversation to associate the trace record with.
        """
        call_id = str(uuid.uuid4())
        start = time.perf_counter()
        success = True
        error_msg: str | None = None
        result = ""

        # Resolve timeout for sandbox
        sandbox_cfg = self._sandbox.get(name)
        timeout = sandbox_cfg["timeout_sec"] if sandbox_cfg else _default_timeout_sec(name)

        try:
            import asyncio

            if self.enable_sandbox:
                result = await asyncio.wait_for(
                    self._registry.dispatch(name, arguments),
                    timeout=timeout,
                )
            else:
                result = await self._registry.dispatch(name, arguments)
        except asyncio.TimeoutError:
            success = False
            error_msg = f"Timeout after {timeout:.0f}s"
            result = json.dumps({"error": error_msg}, ensure_ascii=False)
        except Exception as exc:
            success = False
            error_msg = str(exc)
            result = json.dumps({"error": error_msg}, ensure_ascii=False)

        # Determine success from result JSON if no exception was caught above
        if success and not error_msg:
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and "error" in parsed:
                    success = False
                    error_msg = str(parsed["error"])
            except (json.JSONDecodeError, TypeError):
                pass  # non-JSON result → success stays True

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        # ── Persist trace ──────────────────────────────────────────
        if self.enable_tracing:
            await self._trace(call_id, name, arguments, result, elapsed_ms, success, error_msg, conversation_id)

        return result

    # ── Internals ──────────────────────────────────────────────────────

    def _get_trace_store(self):
        """Lazy-init the trace store (avoids import at module level)."""
        if self._trace_store is None:
            from services.tool_trace_store import ToolTraceStore
            self._trace_store = ToolTraceStore()
        return self._trace_store

    async def _trace(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict,
        result: str,
        elapsed_ms: int,
        success: bool,
        error_message: str | None,
        conversation_id: str | None,
    ):
        """Persist a trace record asynchronously."""
        try:
            store = self._get_trace_store()
            await store.save(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                result_summary=str(result)[:500] if result else None,
                elapsed_ms=elapsed_ms,
                success=success,
                error_message=error_message,
                conversation_id=conversation_id,
            )
        except Exception as exc:
            print(f"[ToolRuntime] trace write failed: {exc}", flush=True)


def _default_registry() -> ToolRegistry:
    """Return the module-level singleton (lazy to avoid import cycles)."""
    from core.tool_registry import tool_registry
    return tool_registry
