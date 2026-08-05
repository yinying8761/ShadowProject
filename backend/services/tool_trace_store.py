"""
ToolTraceStore — async persistence adapter for tool-call trace records.

Writes ``ToolRun`` rows without blocking the Agent's hot path.
Supports an optional session factory override for testing (in-memory DB).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from models.tool_run import ToolRun


class ToolTraceStore:
    """Async writer for tool_runs table."""

    def __init__(self, session_factory=None):
        self._session_factory = session_factory

    async def _get_session(self) -> AsyncSession:
        if self._session_factory is not None:
            return self._session_factory()
        from database import async_session
        return async_session()

    async def save(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict | None = None,
        result_summary: str | None = None,
        elapsed_ms: int = 0,
        success: bool = True,
        error_message: str | None = None,
        conversation_id: str | None = None,
    ) -> ToolRun:
        """Persist a tool-call trace record. Returns the saved ToolRun."""
        import json

        args_str = None
        if arguments:
            try:
                args_str = json.dumps(arguments, ensure_ascii=False)
                if len(args_str) > 2000:
                    args_str = args_str[:1997] + "..."
            except (TypeError, ValueError):
                args_str = str(arguments)[:2000]

        if result_summary and len(result_summary) > 500:
            result_summary = result_summary[:497] + "..."

        if error_message and len(error_message) > 500:
            error_message = error_message[:497] + "..."

        run = ToolRun(
            call_id=call_id,
            tool_name=tool_name,
            arguments=args_str,
            result_summary=result_summary,
            elapsed_ms=elapsed_ms,
            success=success,
            error_message=error_message,
            conversation_id=conversation_id,
        )

        async with await self._get_session() as session:
            session.add(run)
            await session.commit()

        return run
