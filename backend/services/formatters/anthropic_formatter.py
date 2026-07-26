"""
AnthropicFormatter — converts internal message format to/from the Anthropic SDK.

Key differences from the internal format
----------------------------------------
- ``system`` messages are not inline — they are passed as a separate
  top-level ``system`` parameter.
- ``tool``-role messages become ``user`` messages whose content is a
  ``tool_result`` content block carrying ``tool_use_id`` + ``content``.
- Tool definitions use the native Anthropic shape (identical to our
  internal format), so ``format_tools`` is a pass-through.
"""

from typing import Any

from services.formatters.base import MessageFormatter


class AnthropicFormatter(MessageFormatter):
    """Message / tool format conversion for the Anthropic SDK."""

    # ── Messages ───────────────────────────────────────────────────

    def format_messages(
        self, messages: list[dict]
    ) -> tuple[str | None, list[dict]]:
        """Extract system message; convert ``role=tool`` → Anthropic
        ``user`` with ``tool_result`` content block."""
        system_prompt: str | None = None
        provider_msgs: list[dict] = []

        for m in messages:
            role = m.get("role", "")

            if role == "system":
                system_prompt = m.get("content", "")
                continue

            if role == "tool":
                provider_msgs.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", "unknown"),
                        "content": m.get("content", ""),
                    }],
                })
                continue

            # assistant / user — pass through as-is
            provider_msgs.append({"role": role, "content": m.get("content", "")})

        return system_prompt, provider_msgs

    # ── Tools ──────────────────────────────────────────────────────

    def format_tools(
        self, tools: list[dict] | None
    ) -> list[dict] | None:
        """Anthropic SDK accepts the internal format natively — pass-through."""
        return tools or None

    # ── Stream events ──────────────────────────────────────────────

    def parse_stream_event(self, event: object) -> str | None:
        """Extract text from an Anthropic ``content_block_delta`` event."""
        try:
            if getattr(event, "type", None) != "content_block_delta":
                return None
            delta = getattr(event, "delta", None)
            if delta is None:
                return None
            if getattr(delta, "type", None) != "text_delta":
                return None
            return getattr(delta, "text", None)
        except Exception:
            return None

    # ── Tool calls ─────────────────────────────────────────────────

    def extract_tool_calls(self, response: Any) -> list[dict]:
        """Extract tool-call blocks from an Anthropic ``Message`` object.

        *response* is the return value of ``stream.get_final_message()``
        (and also works with the non-streaming ``client.messages.create``).
        """
        result: list[dict] = []
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "tool_use":
                result.append({
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "arguments": getattr(block, "input", {}),
                })
        return result
