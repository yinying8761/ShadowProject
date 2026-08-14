"""
Tests for LLMService retry — Workflow E ticket #16 (E1).

Verifies: chat_sync retries 429/5xx/network errors with exponential backoff
and fails fast on 401/400; stream_chat retries connection establishment but
not mid-stream breaks. All via injected fake SDK clients (no real API).
"""

from types import SimpleNamespace

import pytest

from services.llm_service import LLMService, _is_retryable_llm_error


class FakeHTTPError(Exception):
    """Stand-in for SDK APIStatusError carrying an HTTP status_code."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class RecordingSleep:
    """Fake sleep that records delays instead of sleeping."""

    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class FakeStream:
    """Async iterator of OpenAI-style chunks; optionally raises after they run out."""

    def __init__(self, chunks, raise_after=None):
        self._chunks = list(chunks)
        self._raise_after = raise_after

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        if self._raise_after is not None:
            exc, self._raise_after = self._raise_after, None
            raise exc
        raise StopAsyncIteration


class FakeChunk:
    """OpenAI stream chunk with a single text delta."""

    def __init__(self, content: str):
        delta = SimpleNamespace(content=content, tool_calls=None)
        self.choices = [SimpleNamespace(delta=delta)]


class FakeResponse:
    """OpenAI non-streaming response."""

    def __init__(self, content: str):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]


class FakeOpenAIClient:
    """Fake for openai.AsyncOpenAI: exposes chat.completions.create."""

    def __init__(self):
        self.calls = 0
        self._handler = None

    def set_handler(self, handler):
        self._handler = handler

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        self.calls += 1
        return await self._handler(kwargs)


class FakeAnthropicClient:
    """Fake for anthropic.AsyncAnthropic: exposes messages.create."""

    def __init__(self):
        self.calls = 0
        self._handler = None

    def set_handler(self, handler):
        self._handler = handler

    @property
    def messages(self):
        return self

    async def create(self, **kwargs):
        self.calls += 1
        return await self._handler(kwargs)


class FakeAnthropicResponse:
    """Anthropic non-streaming response."""

    def __init__(self, text: str):
        self.content = [SimpleNamespace(text=text)]


def _make_llm(client):
    """Build an LLMService backed by *client*, with a recording sleep."""
    sleep = RecordingSleep()
    llm = LLMService(clients={"openai": client}, sleep=sleep)
    return llm, sleep


class TestChatSyncRetry:
    @pytest.mark.asyncio
    async def test_retries_429_then_succeeds(self):
        client = FakeOpenAIClient()

        async def handler(kwargs):
            if client.calls == 1:
                raise FakeHTTPError(429)
            return FakeResponse("hello")

        client.set_handler(handler)
        llm, sleep = _make_llm(client)

        result = await llm.chat_sync([{"role": "user", "content": "hi"}])

        assert result == "hello"
        assert client.calls == 2
        assert sleep.delays == [1.0]

    @pytest.mark.asyncio
    async def test_no_retry_on_401(self):
        client = FakeOpenAIClient()

        async def handler(kwargs):
            raise FakeHTTPError(401)

        client.set_handler(handler)
        llm, sleep = _make_llm(client)

        with pytest.raises(FakeHTTPError):
            await llm.chat_sync([{"role": "user", "content": "hi"}])

        assert client.calls == 1
        assert sleep.delays == []

    @pytest.mark.asyncio
    async def test_no_retry_on_400(self):
        client = FakeOpenAIClient()

        async def handler(kwargs):
            raise FakeHTTPError(400)

        client.set_handler(handler)
        llm, sleep = _make_llm(client)

        with pytest.raises(FakeHTTPError):
            await llm.chat_sync([{"role": "user", "content": "hi"}])

        assert client.calls == 1
        assert sleep.delays == []

    @pytest.mark.asyncio
    async def test_exhausts_and_raises(self):
        client = FakeOpenAIClient()

        async def handler(kwargs):
            raise FakeHTTPError(503)

        client.set_handler(handler)
        llm, sleep = _make_llm(client)

        with pytest.raises(FakeHTTPError):
            await llm.chat_sync([{"role": "user", "content": "hi"}])

        assert client.calls == 6  # 1 initial + 5 retries
        assert sleep.delays == [1.0, 2.0, 4.0, 8.0, 16.0]

    @pytest.mark.asyncio
    async def test_retries_sdk_connection_error(self):
        from openai import APIConnectionError

        client = FakeOpenAIClient()

        async def handler(kwargs):
            if client.calls == 1:
                raise APIConnectionError(message="connection refused", request=None)
            return FakeResponse("ok")

        client.set_handler(handler)
        llm, sleep = _make_llm(client)

        result = await llm.chat_sync([{"role": "user", "content": "hi"}])

        assert result == "ok"
        assert client.calls == 2
        assert sleep.delays == [1.0]


class TestStreamRetry:
    @pytest.mark.asyncio
    async def test_retries_establishment_then_streams(self):
        client = FakeOpenAIClient()

        async def handler(kwargs):
            if client.calls == 1:
                raise FakeHTTPError(503)
            return FakeStream([FakeChunk("你好")])

        client.set_handler(handler)
        llm, sleep = _make_llm(client)

        events = [e async for e in llm.stream_chat([{"role": "user", "content": "hi"}])]

        assert events == [{"type": "token", "content": "你好"}]
        assert client.calls == 2
        assert sleep.delays == [1.0]

    @pytest.mark.asyncio
    async def test_midstream_break_no_retry(self):
        client = FakeOpenAIClient()

        async def handler(kwargs):
            return FakeStream([FakeChunk("部分")], raise_after=FakeHTTPError(503))

        client.set_handler(handler)
        llm, sleep = _make_llm(client)

        events = [e async for e in llm.stream_chat([{"role": "user", "content": "hi"}])]

        # partial token preserved, then an error event; no retry happened
        assert events[0] == {"type": "token", "content": "部分"}
        assert events[1]["type"] == "error"
        assert client.calls == 1
        assert sleep.delays == []


class TestRetryableClassification:
    def test_status_codes(self):
        assert _is_retryable_llm_error(FakeHTTPError(429)) is True
        assert _is_retryable_llm_error(FakeHTTPError(500)) is True
        assert _is_retryable_llm_error(FakeHTTPError(503)) is True
        assert _is_retryable_llm_error(FakeHTTPError(400)) is False
        assert _is_retryable_llm_error(FakeHTTPError(401)) is False
        assert _is_retryable_llm_error(FakeHTTPError(404)) is False

    def test_network_errors(self):
        assert _is_retryable_llm_error(TimeoutError("t")) is True
        assert _is_retryable_llm_error(ConnectionError("c")) is True
        assert _is_retryable_llm_error(ValueError("v")) is False

    def test_sdk_connection_errors(self):
        from openai import APIConnectionError as OpenAIConn
        from anthropic import APIConnectionError as AnthropicConn

        assert _is_retryable_llm_error(OpenAIConn(message="refused", request=None)) is True
        assert _is_retryable_llm_error(AnthropicConn(message="refused", request=None)) is True

    def test_sdk_timeout_errors_are_retryable(self):
        from openai import APITimeoutError as OpenAITimeout
        from anthropic import APITimeoutError as AnthropicTimeout

        assert _is_retryable_llm_error(OpenAITimeout(request=None)) is True
        assert _is_retryable_llm_error(AnthropicTimeout(request=None)) is True


class TestAnthropicChatSync:
    @pytest.mark.asyncio
    async def test_retries_429_then_succeeds(self, monkeypatch):
        class FakeSettings:
            def get_sdk_type(self):
                return "anthropic"

            def get_model(self):
                return "claude-test"

        monkeypatch.setattr("services.llm_service.settings", FakeSettings())

        client = FakeAnthropicClient()

        async def handler(kwargs):
            if client.calls == 1:
                raise FakeHTTPError(429)
            return FakeAnthropicResponse("bonjour")

        client.set_handler(handler)
        sleep = RecordingSleep()
        llm = LLMService(clients={"anthropic": client}, sleep=sleep)
        monkeypatch.setattr(llm, "_get_formatter", lambda: None)

        result = await llm.chat_sync([{"role": "user", "content": "hi"}])

        assert result == "bonjour"
        assert client.calls == 2
        assert sleep.delays == [1.0]
