"""
Formatters package — provider-specific message / tool format converters.

Factory
-------
``get_formatter(sdk_type)`` returns the right ``MessageFormatter`` for the
given SDK type string (``"anthropic"`` or ``"openai"``).
"""

from services.formatters.base import MessageFormatter
from services.formatters.anthropic_formatter import AnthropicFormatter

__all__ = ["MessageFormatter", "AnthropicFormatter", "get_formatter"]


def get_formatter(sdk_type: str) -> MessageFormatter:
    """Return a :class:`MessageFormatter` for *sdk_type*.

    Raises ``ValueError`` when the SDK type is unsupported.
    """
    if sdk_type == "anthropic":
        return AnthropicFormatter()
    if sdk_type == "openai":
        raise NotImplementedError(
            "OpenAIFormatter not yet implemented — see Ticket #2"
        )
    raise ValueError(f"Unsupported SDK type: {sdk_type!r}")
