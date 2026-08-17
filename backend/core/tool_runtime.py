"""
ToolRuntime — wraps ToolRegistry with tracing, sandbox, retry, schema
validation, and extension points.

Exposes the same interface as ToolRegistry (register / unregister / dispatch /
get_tool_definitions / needs_approval) so Agent requires zero behavioural changes.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Awaitable, Callable

import jsonschema

from core.circuit_breaker import CLOSED, OPEN, HALF_OPEN, CircuitBreaker
from core.tool_registry import ToolRegistry, ToolHandler
from services.retry import is_retryable, retry


# ── Tool category → default timeout (seconds) ──────────────────────────
_NETWORK_TOOLS = {"fetch_url", "research", "mcp__"}
_SCREEN_TOOLS = {"see_screen"}
_FILE_TOOLS = {"read_file", "write_file", "list_directory", "search_files"}
_IDEMPOTENT_TOOLS = {
    "read_file", "search_files", "list_directory", "search_memory",
    "fetch_url", "research", "get_current_time",
}


def _default_timeout_sec(tool_name: str) -> float:
    """Return the default sandbox timeout for *tool_name*."""
    if tool_name in _FILE_TOOLS:
        return 10.0
    if tool_name in _NETWORK_TOOLS or tool_name.startswith("mcp__"):
        return 30.0
    if tool_name in _SCREEN_TOOLS:
        return 15.0
    return 10.0


def _default_retry_config(tool_name: str) -> dict[str, int]:
    """Return the default retry config for *tool_name*.

    Idempotent tools retry once on transient failures; everything else
    (including write tools) does not retry by default.
    """
    if tool_name in _IDEMPOTENT_TOOLS:
        return {"max_retries": 1}
    return {"max_retries": 0}


_RETRY_BASE_DELAY = 0.5


def _is_tool_retryable(exc: Exception, retryable_exceptions: tuple[type[Exception], ...] | None) -> bool:
    """True when a tool-handler exception is transient and worth retrying.

    Sandbox timeouts are always retried; otherwise *retryable_exceptions*
    (when given) or the built-in transient set decides.
    """
    if isinstance(exc, asyncio.TimeoutError):
        return True
    if retryable_exceptions:
        return isinstance(exc, retryable_exceptions)
    return is_retryable(exc)


def _format_validation_error(exc: jsonschema.exceptions.ValidationError) -> str:
    """Turn a jsonschema error into a short, LLM-friendly message.

    Uses the JSON pointer path (e.g. ``path``, ``$`` for the root) so the
    LLM knows exactly which argument is wrong, plus jsonschema's own
    human-readable message (e.g. ``'123' is not of type 'string'``).
    """
    loc = "/".join(str(p) for p in exc.path) if exc.path else "$"
    return f"Schema validation failed: {loc} — {exc.message}"


class ToolRuntime:
    """Thin wrapper around ToolRegistry that adds tracing + sandbox + retry + placeholders.

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
        When ``True``, ``dispatch()`` consults a per-tool CircuitBreaker
        and short-circuits open tools.
    enable_rate_limit:
        Reserved — stored but not implemented.
    circuit_threshold:
        Consecutive failures before a breaker opens (default 5).
    rate_limit_per_min:
        Reserved.
    circuit_open_sec:
        Cooldown before an open breaker allows a probe (default 60).
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
        circuit_open_sec: float = 60.0,
        retry_sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self._registry = registry or _default_registry()
        self.enable_tracing = enable_tracing
        self.enable_sandbox = enable_sandbox
        self.enable_circuit_breaker = enable_circuit_breaker
        self.enable_rate_limit = enable_rate_limit
        self.circuit_threshold = circuit_threshold
        self.rate_limit_per_min = rate_limit_per_min
        self.circuit_open_sec = circuit_open_sec
        self._retry_sleep = retry_sleep if retry_sleep is not None else asyncio.sleep
        self._clock = clock if clock is not None else time.monotonic

        # Per-tool sandbox config: tool_name → {"timeout_sec": float}
        self._sandbox: dict[str, dict] = {}

        # Per-tool retry config: tool_name → {"max_retries", "retryable_exceptions"}
        self._retry: dict[str, dict] = {}

        # Per-tool circuit breakers: tool_name → CircuitBreaker
        self._circuit_breakers: dict[str, CircuitBreaker] = {}

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
        retry_config: dict | None = None,
    ):
        """Register a tool, optionally with sandbox/retry config.

        *sandbox_config* may contain ``timeout_sec`` to override the
        per-category default.  *retry_config* may contain ``max_retries``
        and ``retryable_exceptions`` (optional tuple of exception types;
        timeouts are always retried).  When *retry_config* is omitted, a
        per-category default applies (idempotent tools retry once).
        """
        self._registry.register(name, description, parameters, handler, require_approval)
        if sandbox_config is not None:
            self._sandbox[name] = sandbox_config
        if retry_config is not None:
            self._retry[name] = retry_config

    def unregister(self, name: str):
        self._registry.unregister(name)
        self._sandbox.pop(name, None)
        self._retry.pop(name, None)

    def get_tool_definitions(self) -> list[dict]:
        return self._registry.get_tool_definitions()

    def needs_approval(self, name: str) -> bool:
        return self._registry.needs_approval(name)

    def reset_breaker(self, name: str) -> None:
        """Reset the circuit breaker for *name* (used on MCP reconnect)."""
        breaker = self._circuit_breakers.get(name)
        if breaker is not None:
            breaker.reset()

    async def dispatch(
        self,
        name: str,
        arguments: dict,
        *,
        conversation_id: str | None = None,
    ) -> str:
        """Execute a tool call with tracing, retry, circuit breaker, and sandbox timeout.

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
        retry_count = 0

        # Schema validation (Workflow F): reject malformed arguments before the
        # handler runs, so the LLM can correct them on a later round. This sits
        # before the circuit-breaker check on purpose: argument errors are the
        # LLM's responsibility, not a tool-health signal, so they must never
        # count toward tripping a breaker. _validate_args converts only
        # ValidationError into a "Schema validation failed" message; a malformed
        # schema (SchemaError / unresolvable $ref, e.g. a broken MCP
        # inputSchema) raises and is caught here as a generic dispatch failure
        # so dispatch keeps its "returns, never raises" contract.
        try:
            validation_error = await self._validate_args(name, arguments)
        except Exception as exc:
            success = False
            error_msg = str(exc)
            result = json.dumps({"error": error_msg}, ensure_ascii=False)
            await self._persist_trace(
                call_id, name, arguments, result, start, success, error_msg,
                conversation_id, retry_count,
            )
            return result
        if validation_error is not None:
            success = False
            error_msg = validation_error
            result = json.dumps({"error": validation_error}, ensure_ascii=False)
            await self._persist_trace(
                call_id, name, arguments, result, start, success, error_msg,
                conversation_id, retry_count,
            )
            return result

        # Resolve timeout + retry config for this tool
        sandbox_cfg = self._sandbox.get(name)
        timeout = sandbox_cfg["timeout_sec"] if sandbox_cfg else _default_timeout_sec(name)
        retry_cfg = self._retry.get(name)
        if retry_cfg is None:
            retry_cfg = _default_retry_config(name)
        max_retries = retry_cfg.get("max_retries", 0)
        retryable_exceptions = retry_cfg.get("retryable_exceptions")

        # Circuit breaker: short-circuit when open
        breaker = self._breaker_for(name)
        if breaker is not None:
            prev_state = breaker.state
            if not breaker.allow_request():
                success = False
                error_msg = f"Circuit breaker open for {name}"
                result = json.dumps({"error": error_msg}, ensure_ascii=False)
                await self._persist_trace(
                    call_id, name, arguments, result, start, success, error_msg,
                    conversation_id, retry_count,
                )
                return result
            self._breaker_transition(name, breaker, prev_state)

        attempts = 0

        async def _attempt() -> str:
            nonlocal attempts
            attempts += 1
            handler = self._registry.get_handler(name)
            if handler is None:
                return json.dumps({"error": f"Unknown tool: {name}"})
            if self.enable_sandbox:
                result = await asyncio.wait_for(handler(**arguments), timeout=timeout)
            else:
                result = await handler(**arguments)
            return str(result) if not isinstance(result, str) else result

        try:
            if max_retries > 0:
                result = await retry(
                    _attempt,
                    max_retries=max_retries,
                    base_delay=_RETRY_BASE_DELAY,
                    retryable=lambda exc: _is_tool_retryable(exc, retryable_exceptions),
                    sleep=self._retry_sleep,
                )
            else:
                result = await _attempt()
        except asyncio.TimeoutError:
            success = False
            error_msg = f"Timeout after {timeout:.0f}s"
            result = json.dumps({"error": error_msg}, ensure_ascii=False)
        except Exception as exc:
            success = False
            error_msg = str(exc)
            result = json.dumps({"error": error_msg}, ensure_ascii=False)

        retry_count = max(0, attempts - 1)

        # Determine success from result JSON if no exception was caught above
        if success and not error_msg:
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and "error" in parsed:
                    success = False
                    error_msg = str(parsed["error"])
            except (json.JSONDecodeError, TypeError):
                pass  # non-JSON result → success stays True

        # Record the circuit-breaker outcome
        if breaker is not None:
            prev_state = breaker.state
            if success:
                breaker.record_success()
            else:
                breaker.record_failure()
            self._breaker_transition(name, breaker, prev_state)

        await self._persist_trace(
            call_id, name, arguments, result, start, success, error_msg,
            conversation_id, retry_count,
        )

        return result

    # ── Internals ──────────────────────────────────────────────────────

    def _breaker_for(self, name: str) -> CircuitBreaker | None:
        """Return the per-tool breaker, or ``None`` when CB is disabled."""
        if not self.enable_circuit_breaker:
            return None
        if name not in self._circuit_breakers:
            self._circuit_breakers[name] = CircuitBreaker(
                threshold=self.circuit_threshold,
                open_timeout=self.circuit_open_sec,
                clock=self._clock,
            )
        return self._circuit_breakers[name]

    def _breaker_transition(self, name: str, breaker: CircuitBreaker, prev: str) -> None:
        """Log a circuit-breaker state transition for *name*."""
        new = breaker.state
        if new == prev:
            return
        if new == OPEN:
            if prev == HALF_OPEN:
                reason = "probe failed"
            else:
                reason = f"{breaker.threshold} consecutive failures"
            print(
                f"[CircuitBreaker] {name} OPEN ({reason}, retry in {breaker.open_timeout:.0f}s)",
                flush=True,
            )
        elif new == HALF_OPEN:
            print(f"[CircuitBreaker] {name} HALF_OPEN (probing...)", flush=True)
        elif new == CLOSED:
            print(f"[CircuitBreaker] {name} CLOSED (probe succeeded)", flush=True)

    async def _validate_args(self, name: str, arguments: dict) -> str | None:
        """Validate *arguments* against *name*'s registered JSON Schema.

        Returns an error message when the arguments are invalid, or ``None``
        when they pass (or the tool is unknown / has no schema — those paths
        fall through to the normal dispatch flow).
        """
        schema = self._registry.get_parameters(name)
        if not schema:
            return None
        try:
            await asyncio.to_thread(jsonschema.validate, instance=arguments, schema=schema)
        except jsonschema.exceptions.ValidationError as exc:
            return _format_validation_error(exc)
        return None

    def _get_trace_store(self):
        """Lazy-init the trace store (avoids import at module level)."""
        if self._trace_store is None:
            from services.tool_trace_store import ToolTraceStore
            self._trace_store = ToolTraceStore()
        return self._trace_store

    async def _persist_trace(
        self,
        call_id: str,
        name: str,
        arguments: dict,
        result: str,
        start: float,
        success: bool,
        error_msg: str | None,
        conversation_id: str | None,
        retry_count: int,
    ) -> None:
        """Persist a trace record when tracing is enabled."""
        if not self.enable_tracing:
            return
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        await self._trace(
            call_id, name, arguments, result, elapsed_ms, success, error_msg,
            conversation_id, retry_count,
        )

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
        retry_count: int = 0,
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
                retry_count=retry_count,
            )
        except Exception as exc:
            print(f"[ToolRuntime] trace write failed: {exc}", flush=True)


def _default_registry() -> ToolRegistry:
    """Return the module-level singleton (lazy to avoid import cycles)."""
    from core.tool_registry import tool_registry
    return tool_registry
