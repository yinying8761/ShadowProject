"""
Token estimation for OpenAI-compatible providers — Workflow G (#24).

Pure functions over ``messages`` lists; the tiktoken encoder is loaded
lazily (first load may hit the network) and degrades to a character-based
heuristic when unavailable, so token counting never crashes the caller.
"""

import threading

_encoder_lock = threading.Lock()
_encoder_cache: dict = {}


def _openai_encoder():
    """Return cached tiktoken cl100k_base encoder, or None when unavailable.

    The lock keeps the lazy init single-flight: without it, two threads
    (e.g. two ``asyncio.to_thread`` calls on first use) could both trigger
    the network-touching ``get_encoding`` load.
    """
    if "enc" not in _encoder_cache:
        with _encoder_lock:
            if "enc" not in _encoder_cache:
                try:
                    import tiktoken
                    _encoder_cache["enc"] = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    _encoder_cache["enc"] = None
    return _encoder_cache["enc"]


def _char_estimate(text: str) -> int:
    """Rough fallback: ~0.6 tokens per CJK char, ~0.25 per other char."""
    if not text:
        return 0
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return int(cjk * 0.6 + other * 0.25) or 1


def _message_text(content: object) -> str:
    """Message content → 可以计 token 的文本。

    多模态消息的 content 是 content block 列表（ticket #02 的截图）：只数**文本块**，
    图片块跳过 —— `str([...])` 会把整段 base64 当文字数进去（误差几十万 token），
    而图片本身按 provider 的图片计价，权威数字由 `usage` 事件带回。
    """
    if isinstance(content, list):
        return " ".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


def estimate_openai_tokens(messages: list[dict], *, encoder=None) -> int:
    """Estimate prompt tokens for OpenAI-compatible providers."""
    enc = encoder if encoder is not None else _openai_encoder()
    total = 0
    for m in messages:
        text = _message_text(m.get("content"))
        for tc in m.get("tool_calls") or []:
            text += str(tc.get("function", {}).get("arguments") or "")
        total += len(enc.encode(text)) if enc is not None else _char_estimate(text)
    return total