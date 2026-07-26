"""
Tests for AnthropicFormatter — message / tool / stream-event conversion.
"""

import pytest
from services.formatters.anthropic_formatter import AnthropicFormatter


@pytest.fixture
def fmt():
    return AnthropicFormatter()


# ── format_messages ──────────────────────────────────────────────────


class TestFormatMessages:
    def test_extracts_system_message(self, fmt):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]
        system_prompt, provider_msgs = fmt.format_messages(messages)

        assert system_prompt == "You are a helpful assistant."
        assert len(provider_msgs) == 1
        assert provider_msgs[0] == {"role": "user", "content": "Hello"}

    def test_no_system_message_returns_none(self, fmt):
        messages = [
            {"role": "user", "content": "What's the weather?"},
        ]
        system_prompt, provider_msgs = fmt.format_messages(messages)

        assert system_prompt is None
        assert len(provider_msgs) == 1

    def test_converts_tool_role_to_tool_result_block(self, fmt):
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Search for cats"},
            {"role": "tool", "tool_call_id": "call_abc123", "content": "Found 5 results"},
            {"role": "assistant", "content": "Here are the results..."},
        ]
        system_prompt, provider_msgs = fmt.format_messages(messages)

        assert system_prompt == "System prompt"
        assert len(provider_msgs) == 3

        # User message passes through
        assert provider_msgs[0] == {"role": "user", "content": "Search for cats"}

        # Tool message becomes Anthropic tool_result
        assert provider_msgs[1]["role"] == "user"
        assert provider_msgs[1]["content"] == [{
            "type": "tool_result",
            "tool_use_id": "call_abc123",
            "content": "Found 5 results",
        }]

        # Assistant message passes through
        assert provider_msgs[2] == {"role": "assistant", "content": "Here are the results..."}

    def test_tool_message_with_missing_tool_call_id_defaults_to_unknown(self, fmt):
        messages = [
            {"role": "tool", "content": "Result without call ID"},
        ]
        _, provider_msgs = fmt.format_messages(messages)

        assert provider_msgs[0]["content"][0]["tool_use_id"] == "unknown"

    def test_preserves_empty_content(self, fmt):
        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": ""},
        ]
        _, provider_msgs = fmt.format_messages(messages)

        assert provider_msgs[0] == {"role": "user", "content": ""}
        assert provider_msgs[1] == {"role": "assistant", "content": ""}


# ── format_tools ─────────────────────────────────────────────────────


class TestFormatTools:
    def test_passes_through_tools(self, fmt):
        tools = [
            {"name": "search", "description": "Search the web", "input_schema": {"type": "object"}},
        ]
        result = fmt.format_tools(tools)
        assert result == tools

    def test_none_returns_none(self, fmt):
        assert fmt.format_tools(None) is None

    def test_empty_list_returns_none(self, fmt):
        assert fmt.format_tools([]) is None


# ── parse_stream_event ────────────────────────────────────────────────


class TestParseStreamEvent:
    def test_extracts_text_delta(self, fmt):
        event = _FakeEvent(
            type="content_block_delta",
            delta=_FakeEvent(type="text_delta", text="Hello"),
        )
        assert fmt.parse_stream_event(event) == "Hello"

    def test_non_text_delta_returns_none(self, fmt):
        event = _FakeEvent(
            type="content_block_delta",
            delta=_FakeEvent(type="input_json_delta", partial_json='{"q":'),
        )
        assert fmt.parse_stream_event(event) is None

    def test_non_content_block_delta_returns_none(self, fmt):
        event = _FakeEvent(type="message_start", message=_FakeEvent(id="msg_1"))
        assert fmt.parse_stream_event(event) is None

    def test_event_without_delta_returns_none(self, fmt):
        event = _FakeEvent(type="content_block_delta")
        assert fmt.parse_stream_event(event) is None

    def test_arbitrary_object_returns_none(self, fmt):
        assert fmt.parse_stream_event("not an event") is None


# ── extract_tool_calls ────────────────────────────────────────────────


class TestExtractToolCalls:
    def test_extracts_single_tool_use(self, fmt):
        block = _FakeEvent(
            type="tool_use",
            id="toolu_001",
            name="search",
            input={"query": "cats"},
        )
        response = _FakeEvent(content=[block])

        result = fmt.extract_tool_calls(response)
        assert len(result) == 1
        assert result[0] == {
            "id": "toolu_001",
            "name": "search",
            "arguments": {"query": "cats"},
        }

    def test_extracts_multiple_tool_uses(self, fmt):
        text_block = _FakeEvent(type="text", text="Let me search...")
        tool_block_1 = _FakeEvent(
            type="tool_use", id="t1", name="search", input={"query": "cats"}
        )
        tool_block_2 = _FakeEvent(
            type="tool_use", id="t2", name="fetch_url", input={"url": "https://example.com"}
        )
        response = _FakeEvent(content=[text_block, tool_block_1, tool_block_2])

        result = fmt.extract_tool_calls(response)
        assert len(result) == 2
        assert result[0]["name"] == "search"
        assert result[1]["name"] == "fetch_url"

    def test_no_tool_uses_returns_empty_list(self, fmt):
        text_block = _FakeEvent(type="text", text="Here is your answer.")
        response = _FakeEvent(content=[text_block])

        result = fmt.extract_tool_calls(response)
        assert result == []

    def test_empty_content_returns_empty_list(self, fmt):
        response = _FakeEvent(content=[])
        assert fmt.extract_tool_calls(response) == []

    def test_response_without_content_attribute_returns_empty(self, fmt):
        """Gracefully handle objects that lack a .content attribute."""
        assert fmt.extract_tool_calls(object()) == []


# ── Helpers ───────────────────────────────────────────────────────────


class _FakeEvent:
    """Minimal stand-in for Anthropic SDK event / message objects."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            object.__setattr__(self, k, v)
