"""
MessageFormatter — abstract base for provider-specific message format conversion.

Each provider (OpenAI, Anthropic, …) has its own message shape, tool-definition
format, and streaming protocol.  A formatter converts the internal canonical
format (Anthropic-flavoured) to/from the provider format so that ``LLMService``
doesn't have to know about provider-specific shapes.
"""

from abc import ABC, abstractmethod


class MessageFormatter(ABC):
    """Convert internal message / tool representations to a provider's format."""

    @abstractmethod
    def format_messages(
        self, messages: list[dict]
    ) -> tuple[str | None, list[dict]]:
        """Convert internal messages to provider format.

        Returns
        -------
        (system_prompt, provider_messages)
            *system_prompt* is ``None`` for providers that keep the system
            message inline in the message list (e.g. OpenAI).  For providers
            that require it as a separate parameter (e.g. Anthropic) it is
            the extracted system message text.
        """

    @abstractmethod
    def format_tools(
        self, tools: list[dict] | None
    ) -> list[dict] | None:
        """Convert internal tool definitions to provider format.

        Internal tools use the Anthropic schema shape:
        ``{"name": …, "description": …, "input_schema": {…}}``.

        Returns ``None`` when *tools* is ``None`` or empty.
        """

    @abstractmethod
    def parse_stream_event(self, event: object) -> str | None:
        """Extract a text token from a provider stream event.

        Returns ``None`` when the event does not carry text (e.g. a
        tool-call delta or a metadata frame).
        """

    @abstractmethod
    def extract_tool_calls(self, response: object) -> list[dict]:
        """Extract tool-call blocks from the completed provider response.

        Returns
        -------
        list[dict]
            Each dict has keys ``id``, ``name``, ``arguments`` (already
            JSON-decoded to a dict).
        """
