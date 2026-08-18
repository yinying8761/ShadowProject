"""
LLMUsageStore — async persistence adapter for per-round LLM token usage.

Mirrors the ToolTraceStore pattern: writes ``LLMUsage`` rows without blocking
the Agent's hot path, and supports an optional session-factory override so
tests can point it at an in-memory DB.
"""

from __future__ import annotations

from models.llm_usage import LLMUsage


class LLMUsageStore:
    """Async writer for the llm_usage table."""

    def __init__(self, session_factory=None):
        self._session_factory = session_factory

    async def _get_session(self):
        if self._session_factory is not None:
            return self._session_factory()
        from database import async_session
        return async_session()

    async def save(
        self,
        *,
        conversation_id: str,
        round_num: int,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        estimated_prompt_tokens: int,
    ) -> LLMUsage:
        """Persist one round's token usage. Returns the saved LLMUsage."""
        row = LLMUsage(
            conversation_id=conversation_id,
            round_num=round_num,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_prompt_tokens=estimated_prompt_tokens,
        )
        async with await self._get_session() as session:
            session.add(row)
            await session.commit()
        return row
