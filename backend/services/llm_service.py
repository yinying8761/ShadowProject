import asyncio
import json
from typing import AsyncIterator, Awaitable, Callable

from config import settings
from services.formatters import get_formatter, MessageFormatter
from services.retry import is_retryable, retry


def _is_retryable_llm_error(exc: Exception) -> bool:
    """Classify an LLM SDK error as transient (worth retrying).

    Status-code errors (429 / 5xx) and generic timeout/connection errors are
    covered by :func:`services.retry.is_retryable`.  SDK connection/timeout
    errors carry no ``status_code`` and are recognised by their SDK base class.
    """
    if is_retryable(exc):
        return True
    from openai import APIConnectionError
    from anthropic import APIConnectionError as AnthropicAPIConnectionError
    return isinstance(exc, (APIConnectionError, AnthropicAPIConnectionError))


class LLMService:
    """LLM abstraction. Uses settings.llm_provider/llm_model/llm_base_url/llm_api_key."""

    def __init__(
        self,
        *,
        clients: dict[str, object] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ):
        self._clients: dict[str, object] = clients if clients is not None else {}
        self._sleep = sleep if sleep is not None else asyncio.sleep

    def _get_formatter(self) -> MessageFormatter | None:
        """Return a provider-specific formatter, or ``None`` when the
        SDK type is not yet supported by the formatters package."""
        try:
            return get_formatter(settings.get_sdk_type())
        except (NotImplementedError, ValueError):
            return None

    def _get_openai_client(self):
        if "openai" not in self._clients:
            from openai import AsyncOpenAI
            base_url = settings.get_base_url() or None
            self._clients["openai"] = AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url=base_url,
            )
        return self._clients["openai"]

    def _get_anthropic_client(self):
        if "anthropic" not in self._clients:
            import anthropic
            self._clients["anthropic"] = anthropic.AsyncAnthropic(
                api_key=settings.llm_api_key
            )
        return self._clients["anthropic"]

    def _retry(self, fn):
        """Wrap *fn* in the shared LLM retry policy (backoff + classification)."""
        return retry(fn, retryable=_is_retryable_llm_error, sleep=self._sleep)

    async def estimate_prompt_tokens(self, messages: list[dict]) -> int:
        """Estimate prompt tokens for the current provider (non-blocking).

        OpenAI-compatible providers (deepseek/qwen/zhipu/moonshot/custom/openai)
        use tiktoken's ``cl100k_base`` via :func:`services.token_counter
        .estimate_openai_tokens`, run in a thread so the first (possibly
        network-touching) encoder load never blocks the event loop. Anthropic
        uses the SDK's ``count_tokens``. Either path degrades to a character
        heuristic when its tokenizer is unavailable — never raises.
        """
        from services.token_counter import estimate_openai_tokens

        if settings.get_sdk_type() == "anthropic":
            return await self._anthropic_input_tokens(messages)
        return await asyncio.to_thread(estimate_openai_tokens, messages)

    async def _anthropic_input_tokens(self, messages: list[dict]) -> int:
        """Anthropic SDK count_tokens → input_tokens; fallback to char estimate.

        ``AsyncAnthropic.messages.count_tokens`` is async and requires the
        ``model`` argument; awaiting it keeps the count authoritative instead
        of silently degrading.

        Anthropic takes ``system`` as a top-level field — ``messages`` only
        accepts ``user``/``assistant`` roles — so the system message is split
        out via the formatter (same as ``_stream_anthropic``) before the call.
        Otherwise the SDK rejects the request and we silently degrade to the
        character heuristic on every round (the Agent's first message is always
        ``system``).
        """
        from services.token_counter import _char_estimate

        try:
            formatter = self._get_formatter()
            if formatter:
                system_msg, user_messages = formatter.format_messages(messages)
            else:
                system_msg = None
                user_messages = []
                for m in messages:
                    if m["role"] == "system":
                        system_msg = m["content"]
                    else:
                        user_messages.append({"role": m["role"], "content": m["content"]})

            client = self._get_anthropic_client()
            kwargs = {
                "model": settings.get_model(),
                "messages": user_messages,
            }
            if system_msg:
                kwargs["system"] = system_msg
            result = await client.messages.count_tokens(**kwargs)
            return result.input_tokens
        except Exception:
            return sum(_char_estimate(str(m.get("content") or "")) for m in messages)

    async def chat_sync(
        self,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        """Non-streaming chat for summarization/extraction. Returns full text."""
        effective_model = model or settings.get_model()
        sdk_type = settings.get_sdk_type()

        if sdk_type == "anthropic":
            formatter = self._get_formatter()
            client = self._get_anthropic_client()
            if formatter:
                system_msg, user_messages = formatter.format_messages(messages)
            else:
                system_msg = None
                user_messages = []
                for m in messages:
                    if m["role"] == "system":
                        system_msg = m["content"]
                    else:
                        user_messages.append({"role": m["role"], "content": m["content"]})
            kwargs = {
                "model": effective_model,
                "max_tokens": max_tokens,
                "messages": user_messages,
            }
            if system_msg:
                kwargs["system"] = system_msg
            response = await self._retry(lambda: client.messages.create(**kwargs))
            return response.content[0].text
        else:
            client = self._get_openai_client()
            response = await self._retry(
                lambda: client.chat.completions.create(
                    model=effective_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=messages,
                ),
            )
            return response.choices[0].message.content or ""

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[dict]:
        """
        Stream chat response.
        Yields: {"type": "token"/"tool_use"/"error"/"usage", ...}
        The final ``usage`` event carries the authoritative token counts
        (model / prompt_tokens / completion_tokens / total_tokens).
        """
        effective_model = model or settings.get_model()
        sdk_type = settings.get_sdk_type()

        if sdk_type == "anthropic":
            async for event in self._stream_anthropic(messages, tools, effective_model):
                yield event
        else:
            async for event in self._stream_openai(messages, tools, effective_model):
                yield event

    async def _stream_anthropic(
        self, messages: list[dict], tools: list[dict] | None, model: str
    ) -> AsyncIterator[dict]:
        try:
            formatter = self._get_formatter()
            if formatter:
                system_msg, user_messages = formatter.format_messages(messages)
                provider_tools = formatter.format_tools(tools)
            else:
                # Fallback — keep the inline logic until formatter is available
                system_msg = None
                user_messages = []
                for m in messages:
                    if m["role"] == "system":
                        system_msg = m["content"]
                    elif m["role"] == "tool":
                        user_messages.append({
                            "role": "user",
                            "content": [{
                                "type": "tool_result",
                                "tool_use_id": m.get("tool_call_id", "unknown"),
                                "content": m["content"],
                            }],
                        })
                    else:
                        user_messages.append({"role": m["role"], "content": m["content"]})
                provider_tools = tools

            kwargs = {
                "model": model,
                "max_tokens": 4096,
                "messages": user_messages,
                "stream": True,
            }
            if system_msg:
                kwargs["system"] = system_msg
            if provider_tools:
                kwargs["tools"] = provider_tools

            client = self._get_anthropic_client()

            async def _open_stream():
                manager = client.messages.stream(**kwargs)
                return manager, await manager.__aenter__()

            manager, stream = await self._retry(_open_stream)
            try:
                if formatter:
                    async for event in stream:
                        token = formatter.parse_stream_event(event)
                        if token:
                            yield {"type": "token", "content": token}
                else:
                    async for event in stream:
                        if event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                yield {"type": "token", "content": event.delta.text}

                final_msg = stream.get_final_message()
                if formatter:
                    for tc in formatter.extract_tool_calls(final_msg):
                        yield {
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        }
                else:
                    for block in final_msg.content:
                        if block.type == "tool_use":
                            yield {
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "arguments": block.input,
                            }

                # Authoritative usage from the final message (Workflow G).
                # input_tokens ≈ prompt_tokens, output_tokens ≈ completion_tokens.
                yield {
                    "type": "usage",
                    "model": model,
                    "prompt_tokens": final_msg.usage.input_tokens,
                    "completion_tokens": final_msg.usage.output_tokens,
                    "total_tokens": final_msg.usage.input_tokens + final_msg.usage.output_tokens,
                }
            finally:
                await manager.__aexit__(None, None, None)
        except Exception as e:
            yield {"type": "error", "message": str(e)}

    async def _stream_openai(
        self, messages: list[dict], tools: list[dict] | None, model: str
    ) -> AsyncIterator[dict]:
        try:
            openai_messages = []
            for m in messages:
                if m["role"] == "tool":
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": m.get("tool_call_id", "unknown"),
                        "content": m["content"],
                    })
                elif m["role"] == "assistant" and m.get("tool_calls"):
                    # Assistant message that triggered tool calls
                    openai_messages.append({
                        "role": "assistant",
                        "content": m.get("content") or None,
                        "tool_calls": m["tool_calls"],
                    })
                else:
                    openai_messages.append({"role": m["role"], "content": m["content"]})

            kwargs = {
                "model": model,
                "messages": openai_messages,
                "max_tokens": 4096,
                "stream": True,
                # Prompt the API to include a `usage` field on the final chunk
                # so the authoritative token count can be surfaced (Workflow G).
                "stream_options": {"include_usage": True},
            }
            if tools:
                kwargs["tools"] = [
                    {"type": "function", "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["input_schema"],
                    }}
                    for t in tools
                ]

            client = self._get_openai_client()
            stream = await self._retry(lambda: client.chat.completions.create(**kwargs))
            accumulated_tool_calls: dict[int, dict] = {}
            usage = None

            async for chunk in stream:
                # The final chunk (with `stream_options.include_usage`) carries
                # usage and an empty choices list; capture it before the delta
                # skip below. getattr keeps fakes without a usage attr working.
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue
                if delta.content:
                    yield {"type": "token", "content": delta.content}
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc.id:
                            accumulated_tool_calls[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                accumulated_tool_calls[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                accumulated_tool_calls[idx]["arguments"] += tc.function.arguments

            for tc in accumulated_tool_calls.values():
                try:
                    args = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    args = {}
                yield {
                    "type": "tool_use",
                    "id": tc["id"] or f"call_{tc['name']}",
                    "name": tc["name"],
                    "arguments": args,
                }

            if usage:
                yield {
                    "type": "usage",
                    "model": model,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                }

        except Exception as e:
            yield {"type": "error", "message": str(e)}
