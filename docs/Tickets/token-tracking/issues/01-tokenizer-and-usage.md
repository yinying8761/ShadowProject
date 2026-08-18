# 01 — tokenizer 预计算 + stream_chat usage 提取

**What to build:** 新增 token 计数模块 + `LLMService.estimate_prompt_tokens()`，并让
`stream_chat` 在流末尾多 yield 一个 `usage` 事件。OpenAI 兼容走 tiktoken、Anthropic 走
SDK count_tokens；tokenizer 不可用时降级字符估算。**不**触发 compact、不中断请求。

**Blocked by:** None — 可直接开始。

**Status:** ready-for-agent

**关键语义（实现前必读）：**

- `usage` 事件必须是该次流式调用的**最后一个**事件（在 token 和 tool_use 之后 yield）。
- 现有消费者（Agent/SubAgent）对未知事件类型静默忽略，所以新增 `usage` 是**向后兼容**的。
- 流中途出错走 `error` 事件路径时**不** yield usage（该轮不落库，best-effort）。
- tiktoken BPE 首次加载可能联网 → 必须**懒加载 + 降级**，失败时回退字符估算，绝不抛异常。

## Checklist

- [ ] `backend/requirements.txt` 新增 `tiktoken>=0.7` 并 `pip install`
- [ ] 新建 `backend/services/token_counter.py`（OpenAI 兼容估算 + 降级）：

```python
import asyncio

_encoder_cache: dict = {}


def _openai_encoder():
    """Return cached tiktoken cl100k_base encoder, or None when unavailable."""
    if "enc" not in _encoder_cache:
        try:
            import tiktoken
            _encoder_cache["enc"] = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _encoder_cache["enc"] = None
    return _encoder_cache["enc"]


def _char_estimate(text: str) -> int:
    """Rough fallback (~4 chars/token CJK, ~3.5 mixed)."""
    if not text:
        return 0
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return int(cjk * 0.6 + other * 0.25) or 1


def estimate_openai_tokens(messages: list[dict], *, encoder=None) -> int:
    """Estimate prompt tokens for OpenAI-compatible providers."""
    enc = encoder if encoder is not None else _openai_encoder()
    total = 0
    for m in messages:
        text = str(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            text += str(tc.get("function", {}).get("arguments") or "")
        total += len(enc.encode(text)) if enc is not None else _char_estimate(text)
    return total
```

- [ ] `LLMService` 新增异步方法（anthropic 直接走 SDK，其余 `asyncio.to_thread`）：

```python
async def estimate_prompt_tokens(self, messages: list[dict]) -> int:
    """Estimate prompt tokens for the current provider (non-blocking)."""
    from services.token_counter import estimate_openai_tokens
    if settings.get_sdk_type() == "anthropic":
        return self._anthropic_input_tokens(messages)
    return await asyncio.to_thread(estimate_openai_tokens, messages)


def _anthropic_input_tokens(self, messages: list[dict]) -> int:
    """Anthropic SDK count_tokens → input_tokens; fallback to char estimate."""
    from services.token_counter import _char_estimate
    try:
        client = self._get_anthropic_client()
        return client.messages.count_tokens(messages=messages).input_tokens
    except Exception:
        return sum(_char_estimate(str(m.get("content") or "")) for m in messages)
```

- [ ] `_stream_openai` 请求加 `stream_options={"include_usage": True}`，循环里捕获 `chunk.usage`
- [ ] `_stream_openai` 在 yield 完所有 `tool_use` 后，若捕获到 usage 则 yield：

```python
        if usage:
            yield {
                "type": "usage",
                "model": model,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
```

- [ ] `_stream_anthropic` 在 `final_msg = stream.get_final_message()` 后，yield 完 tool_use 后，
      用 `final_msg.usage.input_tokens` / `output_tokens` yield 同形 `usage` 事件
- [ ] 确认 `usage` 事件在 `error` 分支里**不**产出
- [ ] 现有测试全绿：`python -m pytest tests/test_llm_retry.py -v`
- [ ] 手动冒烟：注入假 OpenAI stream（最后一个 chunk 带 usage）→ `stream_chat` 末尾收到
      `{"type":"usage", ...}`；`estimate_prompt_tokens([{"role":"user","content":"你好"}])` 返回 >0 整数
