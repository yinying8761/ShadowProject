import asyncio
import json
import weakref
from typing import AsyncIterator, Awaitable, Callable

from services.formatters import get_formatter, MessageFormatter
from services.llm_config import LLMRuntimeConfig, default_runtime
from services.retry import RetryCallback, is_retryable, retry
from services.search_hits import (
    PageText,
    SearchHit,
    SHAPE_SDKS,
    fetch_parser_for,
    parse_search_hits,
    parser_for,
)

# Live LLMService instances (weak refs) — used to invalidate cached SDK
# clients after a runtime-config change without tracking each call site.
_instances: "weakref.WeakSet[LLMService]" = weakref.WeakSet()


# Bounded HTTP timeouts for the LLM SDK clients (seconds).
#
# Without an explicit timeout a severed connection leaves the request pending
# on the socket until the OS TCP timeout — retry() never fires, no llm_retry
# progress is emitted, and the UI shows an endless "thinking" state. httpx's
# `read` timeout is a per-chunk stall timeout (not a total budget), so long
# generations that keep streaming are unaffected.
LLM_CONNECT_TIMEOUT = 10.0
LLM_READ_TIMEOUT = 60.0
LLM_WRITE_TIMEOUT = 30.0
LLM_POOL_TIMEOUT = 10.0


def _llm_timeout():
    """Structured httpx timeout shared by the LLM SDK clients."""
    import httpx

    return httpx.Timeout(
        connect=LLM_CONNECT_TIMEOUT,
        read=LLM_READ_TIMEOUT,
        write=LLM_WRITE_TIMEOUT,
        pool=LLM_POOL_TIMEOUT,
    )


def _search_timeout() -> float:
    """原生搜索的整体超时（秒）。失败/超时都只是降级，不抛给调用方。"""
    from config import settings

    return float(getattr(settings, "search_timeout", 20.0))


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
    """LLM abstraction. Reads the mutable ``LLMRuntimeConfig`` (provider/model/
    base_url/key) instead of the static settings, so UI edits take effect
    without a restart."""

    def __init__(
        self,
        *,
        clients: dict[str, object] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        runtime_config: LLMRuntimeConfig | None = None,
    ):
        self._clients: dict[str, object] = clients if clients is not None else {}
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._runtime = runtime_config if runtime_config is not None else default_runtime()
        _instances.add(self)

    def invalidate_clients(self) -> None:
        """Drop cached SDK clients so the next call rebuilds them from the
        current runtime config (base_url / api_key changes)."""
        self._clients.clear()

    def _get_formatter(self) -> MessageFormatter | None:
        """Return a provider-specific formatter, or ``None`` when the
        SDK type is not yet supported by the formatters package."""
        try:
            return get_formatter(self._runtime.get_sdk_type())
        except (NotImplementedError, ValueError):
            return None

    def image_part(self, image_bytes: bytes, mime: str = "image/jpeg") -> dict:
        """把一张截图变成**当前模型**能接受的 content block（ticket #02）。

        形状按模型的 `image_api` 分派（openai / anthropic），业务代码永远
        不自己拼 payload。**不知道形状就报错**：用户明确要求看屏幕，静默发一个纯文本
        请求等于骗人（ADR-0011 的不对称 —— 视觉报错，搜索才降级）。
        """
        from services.formatters.images import image_part

        api = self._runtime.image_api()
        sdk = self._runtime.get_sdk_type()
        model = self._runtime.get_model() or "当前模型"
        if api is None:
            raise ValueError(
                f"{model} 的看图方式未知（image_api 未设置或不是已知形状）："
                "请在 设置 → 模型 → 模型能力 里指定图片格式后重试"
            )
        if api != sdk:
            # 形状必须和底层 SDK 对得上：openai SDK 只认 image_url、Anthropic SDK 只认
            # source。不一致 = 配置错了（例如把 anthropic 形状指给 OpenAI 兼容端点），
            # 与其发出去被对方拒，不如当场说清楚。
            raise ValueError(
                f"图片格式 {api} 与底层 SDK {sdk} 不匹配（模型 {model}）："
                "请把 image_api 设为该供应商实际使用的形状"
            )
        return image_part(api, image_bytes, mime)

    def _shape_api(self, *, attempt: bool, has_parser) -> str | None:
        """搜索/抓取共用的形状判定：能力**明确**支持 + 有 parser + 形状与 SDK 对得上。

        `attempt` 来自能力表的 `*_should_attempt()`（未知不算），`has_parser` 是
        `parser_for` / `fetch_parser_for`。**不抛错**：任何一条不满足就返回 None，
        调用方据此降级（ADR-0011：搜索/抓取是可选增强）。
        """
        if not attempt:
            return None
        api = self._runtime.search_api()
        if api is None or has_parser(api) is None:
            return None
        shape_sdk = SHAPE_SDKS.get(api)
        if shape_sdk is not None and shape_sdk != self._runtime.get_sdk_type():
            print(
                f"[Search] native skipped: search_api {api!r} needs "
                f"{shape_sdk!r} but SDK is {self._runtime.get_sdk_type()!r}",
                flush=True,
            )
            return None
        return api

    def _native_api(self) -> str | None:
        """这次能用哪种原生搜索形状（None = 不尝试）。"""
        return self._shape_api(
            attempt=self._runtime.native_search_should_attempt(),
            has_parser=parser_for,
        )

    def native_search_available(self) -> bool:
        """原生搜索此刻是否可用（后端链算顺序时用，与实际调用同一处规则）。"""
        return self._native_api() is not None

    def native_search_api(self) -> str | None:
        """`native_search` 会用哪种形状（None = 不走原生；日志用，保证打的是实际值）。"""
        return self._native_api()

    async def native_search(
        self, query: str, limit: int = 5
    ) -> list[SearchHit] | None:
        """用提供商的原生 web search 搜索（ticket 03）。

        返回统一结构的命中；`None` 表示「不支持 / 本次失败」—— 由调用方降级到本地链，
        **不向上抛**（全链失败也只是空结果）。
        """
        api = self._native_api()
        if api is None:
            return None
        try:
            response = await asyncio.wait_for(
                self._native_search_request(api, query, limit), timeout=_search_timeout()
            )
            hits = parse_search_hits(api, response)[:limit]
        except Exception as e:
            # 请求失败、超时、**或解析崩了**，都只是「这条后端不行」：降级，绝不外抛
            print(
                f"[Search] native api={api} failed: {type(e).__name__}: {e}",
                flush=True,
            )
            return None

        if not hits:
            # 解析不出任何带来源的命中：与"失败"同义 —— 让链继续降级到本地抓取
            # （spec：解析失败必须降级；空结果交给本地链去补，而不是当成"搜过了没有"）。
            print(f"[Search] native api={api} parsed no hits; falling back", flush=True)
            return None
        print(f"[Search] native api={api} parsed {len(hits)} hits", flush=True)
        return hits

    async def _native_search_request(self, api: str, query: str, limit: int):
        """按 `search_api` 发请求（只有形状不同，业务代码不按厂商判断）。

        每种形状 = 一个请求体 + 一个 parser（`services/search_hits.py`）**成对**登记；
        `openai` / `deepseek` 共用同一个 Responses 请求体，但响应形状不同（各有 parser）。
        这里没有"默认落到 anthropic"的兜底 —— 未知形状直接放弃，交给本地链。
        """
        model = self._runtime.get_model()
        if api in ("openai", "deepseek"):
            # Responses API：`web_search` 是服务端工具。DeepSeek 兼容这个请求体，
            # 但来源在 `output[].web_search_call` 里，不在 url_citation 注解里。
            return await self._get_openai_client().responses.create(
                model=model,
                tools=[{"type": "web_search"}],
                input=query,
            )
        if api == "anthropic":
            # 服务端 web_search 工具，结果在 web_search_tool_result 块里
            return await self._get_anthropic_client().messages.create(
                model=model,
                max_tokens=1024,
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": max(1, limit),
                    }
                ],
                messages=[{"role": "user", "content": query}],
            )
        if api == "gemini":
            # 原生 generateContent + google_search grounding（REST 由 GeminiRestClient 负责）
            return await self._get_gemini_client().generate(
                model,
                {
                    "contents": [{"parts": [{"text": query}]}],
                    "tools": [{"google_search": {}}],
                },
            )
        raise ValueError(f"no native search request shape for search_api={api!r}")

    def _get_gemini_client(self):
        if "gemini" not in self._clients:
            from services.gemini_rest import GeminiRestClient

            self._clients["gemini"] = GeminiRestClient(
                api_key=self._runtime.api_key,
                base_url=self._runtime.get_base_url(),
            )
        return self._clients["gemini"]

    # ── 原生抓取（ticket 06）─────────────────────────────────────────────

    def _native_fetch_api(self) -> str | None:
        """这次能不能用原生抓取（None = 回落到本地正文抽取）。与搜索共用 `_shape_api`。"""
        return self._shape_api(
            attempt=self._runtime.native_fetch_should_attempt(),
            has_parser=fetch_parser_for,
        )

    def native_fetch_api(self) -> str | None:
        """`native_fetch` 会用哪种形状（None = 不走原生；日志用）。"""
        return self._native_fetch_api()

    async def native_fetch(self, url: str) -> PageText | None:
        """用提供商的原生抓取拿网页正文（ticket 06）。

        `None` 表示「不支持 / 本次失败」—— 调用方回落到本地 HTML 抽取，**不向上抛**。
        """
        api = self._native_fetch_api()
        if api is None:
            return None
        parser = fetch_parser_for(api)
        try:
            response = await asyncio.wait_for(
                self._native_fetch_request(api, url), timeout=_search_timeout()
            )
            page = parser(response) if parser else None
        except Exception as e:
            # 请求失败、超时、解析崩了都是「这条后端不行」：回落本地抽取，绝不外抛
            print(f"[Fetch] native api={api} failed: {type(e).__name__}: {e}", flush=True)
            return None
        if page is None or not page.text.strip():
            print(f"[Fetch] native api={api} returned nothing; falling back", flush=True)
            return None
        return page

    async def _native_fetch_request(self, api: str, url: str):
        """按 `search_api` 发抓取请求（目前只有 Anthropic 有服务端 `web_fetch`）。"""
        model = self._runtime.get_model()
        if api == "anthropic":
            return await self._get_anthropic_client().messages.create(
                model=model,
                max_tokens=1024,
                tools=[
                    {
                        "type": "web_fetch_20250910",
                        "name": "web_fetch",
                        "max_uses": 1,
                    }
                ],
                messages=[{"role": "user", "content": f"抓取这个网页的正文：{url}"}],
            )
        raise ValueError(f"no native fetch shape for search_api={api!r}")

    def _get_openai_client(self):
        if "openai" not in self._clients:
            from openai import AsyncOpenAI
            base_url = self._runtime.get_base_url() or None
            self._clients["openai"] = AsyncOpenAI(
                api_key=self._runtime.api_key,
                base_url=base_url,
                timeout=_llm_timeout(),
                # retry() above owns retries and per-attempt progress
                # reporting; SDK-internal retries would hide attempts and
                # multiply the backoff.
                max_retries=0,
            )
        return self._clients["openai"]

    def _get_anthropic_client(self):
        if "anthropic" not in self._clients:
            import anthropic
            self._clients["anthropic"] = anthropic.AsyncAnthropic(
                api_key=self._runtime.api_key,
                timeout=_llm_timeout(),
                max_retries=0,
            )
        return self._clients["anthropic"]

    def _retry(self, fn, on_retry: RetryCallback | None = None):
        """Wrap *fn* in the shared LLM retry policy (backoff + classification)."""
        return retry(
            fn,
            retryable=_is_retryable_llm_error,
            sleep=self._sleep,
            on_retry=on_retry,
        )

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

        if self._runtime.get_sdk_type() == "anthropic":
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
                "model": self._runtime.get_model(),
                "messages": user_messages,
            }
            if system_msg:
                kwargs["system"] = system_msg
            result = await client.messages.count_tokens(**kwargs)
            return result.input_tokens
        except Exception:
            from services.token_counter import _message_text

            return sum(_char_estimate(_message_text(m.get("content"))) for m in messages)

    async def chat_sync(
        self,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        on_retry: RetryCallback | None = None,
    ) -> str:
        """Non-streaming chat for summarization/extraction. Returns full text."""
        effective_model = model or self._runtime.get_model()
        sdk_type = self._runtime.get_sdk_type()

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
            response = await self._retry(
                lambda: client.messages.create(**kwargs), on_retry=on_retry
            )
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
                on_retry=on_retry,
            )
            return response.choices[0].message.content or ""

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        on_retry: RetryCallback | None = None,
    ) -> AsyncIterator[dict]:
        """
        Stream chat response.
        Yields: {"type": "token"/"tool_use"/"error"/"usage", ...}
        The final ``usage`` event carries the authoritative token counts
        (model / prompt_tokens / completion_tokens / total_tokens).
        """
        effective_model = model or self._runtime.get_model()
        sdk_type = self._runtime.get_sdk_type()

        if sdk_type == "anthropic":
            async for event in self._stream_anthropic(
                messages, tools, effective_model, on_retry=on_retry
            ):
                yield event
        else:
            async for event in self._stream_openai(
                messages, tools, effective_model, on_retry=on_retry
            ):
                yield event

    async def _stream_anthropic(
        self, messages: list[dict], tools: list[dict] | None, model: str,
        on_retry: RetryCallback | None = None,
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

            manager, stream = await self._retry(_open_stream, on_retry=on_retry)
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
        self, messages: list[dict], tools: list[dict] | None, model: str,
        on_retry: RetryCallback | None = None,
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
            stream = await self._retry(
                lambda: client.chat.completions.create(**kwargs), on_retry=on_retry
            )
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


def invalidate_llm_clients() -> None:
    """Clear cached SDK clients on every live ``LLMService`` instance.

    Called after a runtime-config change (``PUT /api/llm-config``) so the next
    request rebuilds its client with the new base_url / api_key.
    """
    for llm in list(_instances):
        llm.invalidate_clients()


_shared_llm: "LLMService | None" = None


def shared_llm() -> LLMService:
    """进程内共享的 ``LLMService``（缓存 SDK client；换配置时由上面那个函数清）。

    工具路径（看屏幕 / 原生搜索）也要发 LLM 请求，别再各自造一个 —— 一个实例就够了。
    """
    global _shared_llm
    if _shared_llm is None:
        _shared_llm = LLMService()
    return _shared_llm
