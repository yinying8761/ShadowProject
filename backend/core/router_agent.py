"""
RouterAgent — task router between MainAgent and sub-agents.

**Reserved.**  Currently a placeholder.  When the system has ≥2
sub-agents this will receive MainAgent tool-call requests, decide
which sub-agent(s) to dispatch to, and aggregate their results.
"""

from __future__ import annotations


class RouterAgent:
    """Task router — reserved for future multi-agent orchestration.

    Current state: interface defined, no implementation.
    When enabled, intercepts ``research`` / ``see_screen`` (and future
    tools) tool calls from ``Agent._execute_tools_with_approval()``,
    dispatches to the appropriate sub-agent(s), and returns aggregated
    results.
    """

    async def run(self, tool_name: str, arguments: dict) -> str:
        """Route a tool call to the right sub-agent(s).

        Reserved — raises NotImplementedError until sub-agent count ≥ 2.
        """
        raise NotImplementedError("RouterAgent is reserved for future use")
