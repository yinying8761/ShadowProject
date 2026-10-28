"""
Formatters package — provider-specific message / tool format converters.

Factory
-------
``get_formatter(sdk_type)`` returns the right ``MessageFormatter`` for the
given SDK type string (``"anthropic"`` or ``"openai"``).

``get_image_formatter(image_api)`` returns the formatter **class** that owns the
image-block shape for a capability-table value (ticket #02 / ADR-0011): the shape
is a formatter concern, the *choice* comes from the model capability table — the
same arrangement as ``SEARCH_PARSERS[search_api]`` on the search side.
"""

from services.formatters.base import MessageFormatter
from services.formatters.anthropic_formatter import AnthropicFormatter
from services.formatters.gemini_formatter import GeminiFormatter

__all__ = [
    "MessageFormatter",
    "AnthropicFormatter",
    "GeminiFormatter",
    "get_formatter",
    "get_image_formatter",
]


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


def get_image_formatter(image_api: str | None) -> type[MessageFormatter] | None:
    """`image_api` → 拥有这种图片形状的 formatter 类（None = 不认识）。

    | `image_api` | 形状 | 谁提供 |
    |---|---|---|
    | `openai` | `image_url` + data URL | `MessageFormatter` 基类（默认形状） |
    | `anthropic` | `source` + base64 | `AnthropicFormatter` |
    | `gemini` | `inline_data` | `GeminiFormatter` |

    返回的是**类**：图片形状与消息格式化无关，不需要实例化（基类是抽象类）。
    """
    if image_api == "openai":
        return MessageFormatter
    if image_api == "anthropic":
        return AnthropicFormatter
    if image_api == "gemini":
        return GeminiFormatter
    return None
