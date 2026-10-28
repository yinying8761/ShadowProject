"""原生 Gemini 的图片形状（ticket #02 / ADR-0011 / spec: multimodal-vision）。

Gemini **默认走它的 OpenAI 兼容端点**，也就是用 base（OpenAI）形状 —— 这个类只在
用户显式配置了原生 `generateContent` 时才需要。它只提供图片形状：消息格式化走的是
OpenAI 兼容那条路，所以其余四个方法不实现（调用会抛 `NotImplementedError`，而
`services/formatters.images.image_part` 只用它的 `image_part`）。
"""

from services.formatters.base import MessageFormatter
from services.formatters.images import b64


class GeminiFormatter(MessageFormatter):
    """只提供原生 `generateContent` 的图片块形状。"""

    @staticmethod
    def image_part(data: bytes, mime: str) -> dict:
        return {"inline_data": {"mime_type": mime, "data": b64(data)}}

    def _image_only(self, *_args, **_kwargs):
        raise NotImplementedError(
            "GeminiFormatter 只提供原生图片形状；Gemini 的消息格式化走它的 OpenAI 兼容端点"
        )

    format_messages = _image_only
    format_tools = _image_only
    parse_stream_event = _image_only
    extract_tool_calls = _image_only
