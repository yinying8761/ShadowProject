"""
SubAgent — base class for sub-agents with independent LLM + tool loop.

Each sub-agent has its own LLM instance and isolated ToolRegistry.
The tool loop is a simplified version of Agent's main loop: no approval,
no memory, no user profile — just LLM ↔ tools for a bounded number of
rounds.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from core.tool_registry import ToolRegistry
from services.llm_service import LLMService


class SubAgent:
    """Base for sub-agents that run an independent LLM + tool loop."""

    def __init__(
        self,
        *,
        llm_service: LLMService | None = None,
        tool_registry: ToolRegistry | None = None,
        max_tool_rounds: int = 2,
    ):
        self._llm = llm_service or LLMService()
        self._tools = tool_registry or ToolRegistry()
        self._max_tool_rounds = max_tool_rounds

    # ── Tool registration ─────────────────────────────────────────────

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler,
    ):
        """Register a tool available to this sub-agent's LLM."""
        self._tools.register(name, description, parameters, handler, require_approval=False)

    def _tool_definitions(self) -> list[dict] | None:
        """Tool schemas for the LLM, or None when the registry is empty."""
        tools = self._tools.get_tool_definitions()
        return tools if tools else None

    # ── Public API ────────────────────────────────────────────────────

    async def run(self, prompt: str) -> str:
        """Execute the sub-agent's task.  Override in subclasses."""
        raise NotImplementedError

    # ── Tool loop ─────────────────────────────────────────────────────

    async def _run_tool_loop(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.3,
    ) -> tuple[str, list[str]]:
        """Execute an LLM ↔ tool calling loop.

        Returns ``(final_text, tool_outputs)`` — the assistant's last text
        plus the raw content of every tool result collected across rounds.
        Callers that need a grounded answer (e.g. SearchAgent) summarise
        from *tool_outputs* rather than trusting *final_text*.

        Parameters
        ----------
        system_prompt:
            System-level instruction for the sub-agent's LLM.
        user_prompt:
            The task / query.
        temperature:
            LLM temperature for the sub-agent (default 0.3 for factual
            tasks like search).
        """
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        tools = self._tool_definitions()
        final_text = ""
        tool_outputs: list[str] = []

        for round_num in range(self._max_tool_rounds):
            tool_use_blocks: list[dict] = []
            round_text = ""

            async for event in self._llm.stream_chat(
                messages=messages,
                tools=tools,
            ):
                if event["type"] == "token":
                    round_text += event["content"]
                elif event["type"] == "tool_use":
                    tool_use_blocks.append({
                        "id": event.get("id", f"call_{len(tool_use_blocks)}"),
                        "name": event["name"],
                        "arguments": event["arguments"],
                    })
                elif event["type"] == "error":
                    final_text = round_text
                    break

            if event.get("type") == "error":
                break

            if not tool_use_blocks:
                final_text = round_text
                break

            # ── Build assistant message with tool calls ──────────
            messages.append({
                "role": "assistant",
                "content": round_text or None,
                "tool_calls": [
                    {
                        "id": tb["id"],
                        "type": "function",
                        "function": {
                            "name": tb["name"],
                            "arguments": json.dumps(tb["arguments"], ensure_ascii=False),
                        },
                    }
                    for tb in tool_use_blocks
                ],
            })

            # ── Execute tools ────────────────────────────────────
            for tb in tool_use_blocks:
                try:
                    result = await self._tools.dispatch(tb["name"], tb["arguments"])
                    content = str(result)
                except Exception as exc:
                    content = json.dumps({"error": str(exc)}, ensure_ascii=False)

                tool_outputs.append(content)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tb["id"],
                    "content": content,
                })

            final_text = round_text

        return final_text, tool_outputs
