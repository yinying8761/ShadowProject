from __future__ import annotations

import json
from typing import Any, Callable, Awaitable


ToolHandler = Callable[..., Awaitable[str]]


class ToolRegistry:
    """Registry for tools that the Agent can call."""

    def __init__(self):
        self._tools: dict[str, dict] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: ToolHandler,
        require_approval: bool = False,
    ):
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": handler,
            "require_approval": require_approval,
        }

    def unregister(self, name: str):
        self._tools.pop(name, None)

    def get_tool_definitions(self) -> list[dict]:
        """Return tool schemas in Anthropic/OpenAI compatible format."""
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            }
            for t in self._tools.values()
        ]

    def needs_approval(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool["require_approval"] if tool else False

    def get_handler(self, name: str) -> ToolHandler | None:
        """Return the handler registered for *name*, or ``None`` if unknown."""
        tool = self._tools.get(name)
        return tool["handler"] if tool else None

    async def dispatch(self, name: str, arguments: dict) -> str:
        """Validate and execute a tool call. Returns the result as a string."""
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {name}"})

        try:
            result = await tool["handler"](**arguments)
            return str(result) if not isinstance(result, str) else result
        except Exception as e:
            return json.dumps({"error": str(e)})


tool_registry = ToolRegistry()
