import json
from typing import AsyncIterator
from config import settings


class LLMService:
    """LLM abstraction. Uses settings.llm_provider/llm_model/llm_base_url/llm_api_key."""

    def __init__(self):
        self._clients: dict[str, object] = {}

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
            client = self._get_anthropic_client()
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
            response = await client.messages.create(**kwargs)
            return response.content[0].text
        else:
            client = self._get_openai_client()
            response = await client.chat.completions.create(
                model=effective_model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
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
        Yields: {"type": "token"/"tool_use"/"error", ...}
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

            kwargs = {
                "model": model,
                "max_tokens": 4096,
                "messages": user_messages,
                "stream": True,
            }
            if system_msg:
                kwargs["system"] = system_msg
            if tools:
                kwargs["tools"] = tools

            client = self._get_anthropic_client()
            async with client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield {"type": "token", "content": event.delta.text}

                final_msg = stream.get_final_message()
                for block in final_msg.content:
                    if block.type == "tool_use":
                        yield {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "arguments": block.input,
                        }
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
            stream = await client.chat.completions.create(**kwargs)
            accumulated_tool_calls: dict[int, dict] = {}

            async for chunk in stream:
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

        except Exception as e:
            yield {"type": "error", "message": str(e)}
